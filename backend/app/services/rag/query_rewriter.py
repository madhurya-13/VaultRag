"""Deterministic query rewriting and expansion.

Safety properties
-----------------
* The output is only ever a *search string*.
* No tenant, role, permission, filter or document decision is produced.
* A rewritten query is re-validated against the existing length/NUL rules
  before it is embedded, and expansion count is configurable and capped.
* The original question is always retained as the first retrieval query, so a
  rewrite can never silently replace the user's intent.
"""

from __future__ import annotations

import re

from app.services.rag.query_understanding import QueryAnalysis

# Deliberately small, domain-specific and offline. No network call, no model.
_DOMAIN_EXPANSIONS: dict[str, str] = {
    "leave": (
        "employee leave policy eligibility annual leave allowance "
        "approval process"
    ),
    "reimbursement": (
        "employee expense reimbursement policy business travel expense "
        "claims eligibility limits approval procedure"
    ),
    "expense": (
        "expense reimbursement policy claim eligibility limits approval procedure"
    ),
    "travel": (
        "business travel expense claims reimbursement eligibility limits "
        "approval procedure"
    ),
    "security": "security policy incident reporting access control requirements",
    "incident": (
        "security incident reporting procedure responsibilities response timeline"
    ),
    "retention": (
        "data retention schedule storage duration deletion and archival requirements"
    ),
    "contract": "contract terms obligations renewal termination notice requirements",
    "procurement": "procurement policy purchase approval supplier requirements",
    "onboarding": "employee onboarding policy probation and induction requirements",
    "remote": "remote work policy eligibility approval and equipment requirements",
    "performance": "performance review policy rating eligibility and process",
    "access": "access control policy authorization approval and review procedure",
}

_SYNONYMS: dict[str, str] = {
    "vacation": "leave",
    "holiday": "leave",
    "time off": "leave",
    "expenses": "reimbursement",
    "claims": "reimbursement",
    "cyber": "security",
    "privacy": "retention",
    "vendor": "procurement",
    "supplier": "procurement",
    "wfh": "remote",
    "appraisal": "performance",
}

_LEADING_FILLERS = (
    re.compile(r"^(?:and|also|then|so|but|now)\b[,\s]*", re.IGNORECASE),
    re.compile(r"^(?:what|how)\s+about\s+(?:the\s+)?", re.IGNORECASE),
    re.compile(r"^(?:tell\s+me\s+more\s+about)\s+", re.IGNORECASE),
    re.compile(r"^(?:can\s+you\s+explain)\s+", re.IGNORECASE),
    re.compile(r"^(?:what\s+is|what's)\s+(?:the\s+)?", re.IGNORECASE),
)


def _domain_terms(text: str) -> list[str]:
    lowered = (text or "").lower()
    terms: list[str] = []
    for key in _DOMAIN_EXPANSIONS:
        if re.search(rf"\b{re.escape(key)}", lowered) and key not in terms:
            terms.append(key)
    for phrase, key in _SYNONYMS.items():
        if phrase in lowered and key not in terms:
            terms.append(key)
    return terms


def _strip_fillers(text: str) -> str:
    cleaned = text.strip()
    changed = True
    while changed:
        changed = False
        for pattern in _LEADING_FILLERS:
            updated = pattern.sub("", cleaned).strip()
            if updated and updated != cleaned:
                cleaned = updated
                changed = True
    return cleaned.strip(" ?.!,\t\n")


def rewrite_question(
    question: str,
    analysis: QueryAnalysis,
    *,
    conversation_topic: str | None = None,
    max_chars: int = 400,
) -> str:
    """Produce a retrieval-oriented rewrite that preserves intent."""
    original = (question or "").strip()
    if not original:
        return ""

    cleaned = _strip_fillers(original) or original
    parts: list[str] = []

    if analysis.refers_to_context and conversation_topic:
        topic = conversation_topic.strip()
        if topic and topic.lower() not in cleaned.lower():
            parts.append(topic)

    parts.append(cleaned)

    terms = _domain_terms(cleaned) or _domain_terms(conversation_topic or "")
    short_query = len(cleaned.split()) <= 8 or len(analysis.keywords) <= 6
    if terms and (short_query or analysis.refers_to_context):
        for term in terms[:2]:
            expansion = _DOMAIN_EXPANSIONS.get(term)
            if expansion and expansion.lower() not in cleaned.lower():
                parts.append(expansion)

    rewritten = " ".join(part for part in parts if part).strip()
    return rewritten[:max_chars].strip()


def expand_queries(
    rewritten: str,
    analysis: QueryAnalysis,
    *,
    max_expansions: int = 2,
) -> tuple[str, ...]:
    """Generate a small, bounded set of alternative retrieval queries."""
    if max_expansions <= 0:
        return ()

    lowered = rewritten.lower()
    terms = _domain_terms(rewritten)
    topic = terms[0] if terms else (analysis.keywords[0].lower() if analysis.keywords else "")
    if not topic:
        return ()

    templates = (
        f"{topic} policy",
        f"{topic} eligibility and limits",
        f"{topic} approval procedure",
        f"{topic} requirements and exceptions",
    )

    expansions: list[str] = []
    for candidate in templates:
        normalized = candidate.lower()
        if normalized in lowered or normalized in {item.lower() for item in expansions}:
            continue
        expansions.append(candidate)
        if len(expansions) >= max_expansions:
            break
    return tuple(expansions)
