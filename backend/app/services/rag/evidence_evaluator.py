"""Qualitative evidence-quality assessment.

This is **not** a calibrated probability and must never be presented as one.
It combines measurable pipeline signals into a qualitative bucket:

* retrieval relevance (best semantic similarity, or keyword-only evidence),
* evidence coverage (distinct documents),
* citation coverage (how many factual sentences carry a valid citation),
* citation validity (invalid references reduce quality),
* conflicting evidence (reduces confidence without hiding either source).

The level answers "how well is this answer supported by authorized documents?"
It does not answer "is this fact true?".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from app.services.context_builder import ContextSource
from app.services.reranking import RankedChunk

LEVEL_STRONG = "strong"
LEVEL_MODERATE = "moderate"
LEVEL_LIMITED = "limited"
LEVEL_INSUFFICIENT = "insufficient"

_CONFLICT_PENALTY = 0.05
_MAX_CONFLICT_PENALTY = 0.10


@dataclass(frozen=True, slots=True)
class EvidenceAssessment:
    level: str
    score: float
    sufficient: bool
    signals: dict[str, float | int | str | bool]


def assess_evidence(
    *,
    ranked: Sequence[RankedChunk],
    sources: Sequence[ContextSource],
    citation_coverage: float,
    invalid_citations: int,
    conflict_count: int,
    min_coverage: float,
    strong_threshold: float,
    moderate_threshold: float,
    limited_threshold: float,
) -> EvidenceAssessment:
    source_count = len(sources)
    distinct_documents = len({source.document_id for source in sources})
    semantic_scores = [
        chunk.semantic_similarity
        for chunk in ranked
        if chunk.semantic_similarity is not None
    ]
    relevance = max(semantic_scores) if semantic_scores else (0.5 if source_count else 0.0)
    relevance = max(0.0, min(1.0, relevance))

    diversity = min(distinct_documents / 2.0, 1.0)

    if not sources:
        citation_validity = 0.0
    elif invalid_citations == 0:
        citation_validity = 1.0
    else:
        citation_validity = max(
            0.0, source_count / (source_count + invalid_citations)
        )

    penalty = min(conflict_count * _CONFLICT_PENALTY, _MAX_CONFLICT_PENALTY)
    score = (
        0.40 * max(0.0, min(1.0, citation_coverage))
        + 0.25 * relevance
        + 0.20 * diversity
        + 0.15 * citation_validity
        - penalty
    )
    score = round(max(0.0, min(1.0, score)), 4)

    sufficient = bool(
        source_count > 0
        and citation_coverage >= min_coverage
        and score >= limited_threshold
    )

    if not sufficient:
        level = LEVEL_INSUFFICIENT
    elif score >= strong_threshold:
        level = LEVEL_STRONG
    elif score >= moderate_threshold:
        level = LEVEL_MODERATE
    else:
        level = LEVEL_LIMITED

    return EvidenceAssessment(
        level=level,
        score=score,
        sufficient=sufficient,
        signals={
            "source_count": source_count,
            "distinct_documents": distinct_documents,
            "citation_coverage": round(float(citation_coverage), 4),
            "relevance": round(relevance, 4),
            "invalid_citations": int(invalid_citations),
            "conflicts": int(conflict_count),
            "semantic_evidence": bool(semantic_scores),
        },
    )
