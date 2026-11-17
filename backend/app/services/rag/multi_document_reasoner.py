"""Multi-document reasoning support.

Two responsibilities:

1. Produce extra instructions for the generation prompt when a question
   genuinely spans documents.
2. Detect *potential* conflicts in the authorized evidence.

Conflict detection is deliberately conservative and evidence-only. It reports a
disagreement only when two different authorized documents state incompatible
values for the same topic. It never infers precedence from ``created_at``: the
schema has no reliable effective-date or version field, so claiming a winner
would be fabrication.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from app.services.context_builder import ContextSource
from app.services.rag.query_understanding import QueryAnalysis

_TOPIC_PATTERNS: dict[str, re.Pattern[str]] = {
    "annual leave": re.compile(r"\bannual\s+leave\b", re.IGNORECASE),
    "sick leave": re.compile(r"\bsick\s+leave\b", re.IGNORECASE),
    "reimbursement limit": re.compile(
        r"\b(?:reimbursement|expense)\s+(?:limit|cap|maximum)\b", re.IGNORECASE
    ),
    "notice period": re.compile(r"\bnotice\s+period\b", re.IGNORECASE),
    "probation period": re.compile(r"\bprobation\s+period\b", re.IGNORECASE),
}

_UNIT_RE = re.compile(
    r"(\d{1,4})\s*(days?|weeks?|months?|hours?|percent|%)", re.IGNORECASE
)
_APPROVAL_REQUIRED_RE = re.compile(
    r"\b(?:requires?|required|must\s+be|subject\s+to)\s+approval\b", re.IGNORECASE
)
_APPROVAL_NOT_REQUIRED_RE = re.compile(
    r"\b(?:no\s+approval\s+required|does\s+not\s+require\s+approval|"
    r"without\s+approval)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class Conflict:
    topic: str
    description: str
    values: tuple[str, ...]
    sources: tuple[ContextSource, ...]


def _unit_label(unit: str) -> str:
    normalized = unit.lower()
    return "%" if normalized in {"percent", "%"} else normalized.rstrip("s")


def _format_value(number: str, unit: str) -> str:
    label = _unit_label(unit)
    if label == "%":
        return f"{number}%"
    return f"{number} {label}{'' if number == '1' else 's'}"


def _numeric_claims(source: ContextSource) -> dict[str, list[str]]:
    text = " ".join(source.text.split())
    claims: dict[str, list[str]] = {}
    for topic, pattern in _TOPIC_PATTERNS.items():
        for match in pattern.finditer(text):
            # Look at a bounded window around the topic mention. This keeps a
            # number from an unrelated section out of the claim.
            window = text[max(0, match.start() - 80) : match.end() + 160]
            for number, unit in _UNIT_RE.findall(window):
                claims.setdefault(topic, []).append(_format_value(number, unit))
    return claims


def _approval_claims(source: ContextSource) -> tuple[bool, bool]:
    text = " ".join(source.text.split())
    return (
        bool(_APPROVAL_REQUIRED_RE.search(text)),
        bool(_APPROVAL_NOT_REQUIRED_RE.search(text)),
    )


def detect_conflicts(
    sources: Sequence[ContextSource],
) -> tuple[Conflict, ...]:
    """Return conservative, evidence-backed conflicts only."""
    if len({source.document_id for source in sources}) < 2:
        return ()

    conflicts: list[Conflict] = []

    by_topic: dict[str, dict[str, ContextSource]] = {}
    for source in sources:
        for topic, values in _numeric_claims(source).items():
            for value in values:
                by_topic.setdefault(topic, {}).setdefault(value, source)

    for topic, values in by_topic.items():
        if len(values) < 2:
            continue
        involved: list[ContextSource] = []
        for source in values.values():
            if all(
                existing.document_id != source.document_id for existing in involved
            ):
                involved.append(source)
        if len(involved) < 2:
            continue
        ordered_values = tuple(sorted(values))
        conflicts.append(
            Conflict(
                topic=topic,
                description=(
                    f"The authorized documents state different values for {topic}: "
                    + ", ".join(ordered_values)
                    + "."
                ),
                values=ordered_values,
                sources=tuple(involved),
            )
        )

    required_docs = [
        source for source in sources if _approval_claims(source)[0]
    ]
    not_required_docs = [
        source for source in sources if _approval_claims(source)[1]
    ]
    if required_docs and not_required_docs:
        distinct = {
            source.document_id
            for source in required_docs + not_required_docs
        }
        if len(distinct) >= 2:
            involved = []
            for source in required_docs + not_required_docs:
                if all(
                    existing.document_id != source.document_id
                    for existing in involved
                ):
                    involved.append(source)
            conflicts.append(
                Conflict(
                    topic="approval requirement",
                    description=(
                        "The authorized documents disagree about whether approval "
                        "is required."
                    ),
                    values=("requires approval", "does not require approval"),
                    sources=tuple(involved),
                )
            )

    return tuple(conflicts)


def reasoning_instructions(
    analysis: QueryAnalysis,
    *,
    conflict_count: int,
    distinct_documents: int,
) -> str:
    """Extra generation instructions. Empty for a simple single-document query."""
    instructions: list[str] = []
    if analysis.intent == "comparison":
        instructions.append(
            "The question compares sources. Present each document's position "
            "separately, then state the differences. Cite every claim."
        )
    if distinct_documents >= 2:
        instructions.append(
            "Evidence comes from multiple documents. Attribute each fact to its "
            "own source identifier instead of merging them silently."
        )
    if analysis.intent == "timeline":
        instructions.append(
            "Construct the timeline only from explicit dates in the evidence. "
            "Do not infer missing dates."
        )
    if conflict_count:
        instructions.append(
            "The retrieved evidence contains a potential disagreement. Present "
            "both positions with their citations. Do not declare which document "
            "prevails unless the evidence explicitly establishes precedence."
        )
    return " ".join(instructions)
