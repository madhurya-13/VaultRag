"""Deterministic query analysis.

This layer answers *what is being asked* so retrieval can be tailored. It is
pure: no database session, no tenant, no permissions. It cannot decide that a
document is accessible, and it never produces a filter, tenant ID, role or
permission.

The analysis is intentionally rule-based. An optional LLM rewriter exists
elsewhere but is disabled by default: rewriting must not add latency, cost or a
prompt-injection surface to every query.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.core.security_constants import ALLOWED_DEPARTMENTS

_STOPWORDS = frozenset(
    {
        "a",
        "about",
        "after",
        "all",
        "also",
        "am",
        "an",
        "and",
        "any",
        "are",
        "as",
        "at",
        "be",
        "been",
        "before",
        "between",
        "both",
        "but",
        "by",
        "can",
        "could",
        "did",
        "do",
        "does",
        "for",
        "from",
        "get",
        "give",
        "has",
        "have",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "me",
        "my",
        "of",
        "on",
        "or",
        "our",
        "please",
        "should",
        "show",
        "so",
        "tell",
        "than",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "to",
        "us",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "will",
        "with",
        "would",
        "you",
    }
)

_COMPARISON_RE = re.compile(
    r"\b(compare|comparison|versus|vs\.?|differences?|differ|contrast|both)\b",
    re.IGNORECASE,
)
_TIMELINE_RE = re.compile(
    r"\b(timeline|chronolog\w*|sequence of events|first .* then|order of events)\b",
    re.IGNORECASE,
)
_PROCEDURE_RE = re.compile(
    r"\b(procedure|process|approval|approve|steps?|workflow|how do i|how to)\b",
    re.IGNORECASE,
)
_POLICY_RE = re.compile(
    r"\b(policy|policies|guidelines?|handbook|rule|regulation)\b",
    re.IGNORECASE,
)
_SUMMARY_RE = re.compile(
    r"\b(summar\w+|overview|explain|list)\b", re.IGNORECASE
)
_MULTI_EVIDENCE_RE = re.compile(
    r"\b(across (?:the )?documents?|multiple documents?|each department|"
    r"both departments?|all documents?|every document)\b",
    re.IGNORECASE,
)

_DATE_PATTERNS = (
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
    re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b"),
    re.compile(
        r"\b(?:january|february|march|april|may|june|july|august|september|"
        r"october|november|december)\s+\d{4}\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:last|this|next)\s+(?:quarter|year|month)\b", re.IGNORECASE),
    re.compile(r"\bQ[1-4]\s+\d{4}\b", re.IGNORECASE),
)

_ENTITY_PATTERNS = (
    re.compile(r"\b[A-Z]{2,6}-\d{2,}(?:-[A-Z0-9]+)*\b"),
    re.compile(r"\b[A-Z]{2,6}\d{4,}\b"),
)

_DOCUMENT_REFERENCE_RE = re.compile(
    r"\b([A-Za-z][\w&.-]*(?:\s+[A-Za-z][\w&.-]*){0,4}\s+"
    r"(?:policy|policies|guidelines?|handbook|agreement|contract|manual|"
    r"procedure|notice))\b",
    re.IGNORECASE,
)

_CONTEXT_REFERENCE_STARTERS = (
    "it",
    "its",
    "this",
    "that",
    "these",
    "those",
    "they",
    "them",
    "their",
)
_CONTEXT_REFERENCE_PREFIXES = (
    "what about",
    "how about",
    "and ",
    "also ",
    "then ",
    "what if",
)


@dataclass(frozen=True, slots=True)
class QueryAnalysis:
    intent: str
    entities: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    dates: tuple[str, ...] = ()
    departments: tuple[str, ...] = ()
    document_references: tuple[str, ...] = ()
    needs_multi_evidence: bool = False
    refers_to_context: bool = False
    is_complex: bool = False
    signals: dict[str, bool] = field(default_factory=dict)


def _dedupe(values: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        key = value.strip()
        if not key or key.lower() in seen:
            continue
        seen.add(key.lower())
        result.append(key)
    return tuple(result)


def _extract_departments(text: str) -> tuple[str, ...]:
    tokens = re.findall(r"[A-Za-z]+", text.upper())
    found: list[str] = []
    for token in tokens:
        if token in ALLOWED_DEPARTMENTS and token not in found:
            found.append(token)
    return tuple(found)


def _extract_keywords(text: str, limit: int = 12) -> tuple[str, ...]:
    tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9._/-]*", text)
    keywords = [
        token
        for token in tokens
        if len(token) >= 2 and token.lower() not in _STOPWORDS
    ]
    return _dedupe(keywords)[:limit]


def _detect_intent(text: str, *, entities: tuple[str, ...]) -> str:
    if _COMPARISON_RE.search(text):
        return "comparison"
    if _TIMELINE_RE.search(text):
        return "timeline"
    if _PROCEDURE_RE.search(text):
        return "procedure"
    if entities:
        return "lookup"
    if _SUMMARY_RE.search(text):
        return "summary"
    if _POLICY_RE.search(text):
        return "policy"
    return "factual"


def _refers_to_context(text: str, keywords: tuple[str, ...]) -> bool:
    lowered = text.strip().lower()
    if any(lowered.startswith(prefix) for prefix in _CONTEXT_REFERENCE_PREFIXES):
        return True
    first_token = re.findall(r"[a-z]+", lowered)
    if first_token and first_token[0] in _CONTEXT_REFERENCE_STARTERS:
        return True
    # A very short question without its own subject ("How many days can I
    # take?") is treated as a follow-up. Retrieval still runs fresh and only a
    # validated, caller-owned conversation may supply a topic.
    if len(keywords) <= 3 and not re.search(r"\b(policy|document|section)\b", lowered):
        return True
    return False


def analyze_query(question: str) -> QueryAnalysis:
    """Produce a deterministic analysis of one user question."""
    text = (question or "").strip()
    entities = _dedupe(
        [
            match
            for pattern in _ENTITY_PATTERNS
            for match in pattern.findall(text)
        ]
    )
    dates = _dedupe(
        [match for pattern in _DATE_PATTERNS for match in pattern.findall(text)]
    )
    departments = _extract_departments(text)
    keywords = _extract_keywords(text)
    document_references = _dedupe(
        [
            match.strip()
            for match in _DOCUMENT_REFERENCE_RE.findall(text)
            if len(match.strip()) >= 5
        ]
    )[:5]

    intent = _detect_intent(text, entities=entities)
    comparison = intent == "comparison"
    needs_multi = bool(
        comparison
        or len(departments) >= 2
        or _MULTI_EVIDENCE_RE.search(text)
    )
    refers = _refers_to_context(text, keywords)
    is_complex = bool(
        comparison
        or intent == "timeline"
        or len(departments) >= 2
        or len(entities) >= 2
        or _MULTI_EVIDENCE_RE.search(text)
    )

    return QueryAnalysis(
        intent=intent,
        entities=entities[:8],
        keywords=keywords,
        dates=dates[:5],
        departments=departments[:4],
        document_references=document_references,
        needs_multi_evidence=needs_multi,
        refers_to_context=refers,
        is_complex=is_complex,
        signals={
            "comparison": comparison,
            "timeline": intent == "timeline",
            "procedure": intent == "procedure",
            "has_entities": bool(entities),
            "has_dates": bool(dates),
            "multi_department": len(departments) >= 2,
        },
    )
