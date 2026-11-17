"""Reciprocal Rank Fusion for semantic and keyword candidates.

Fusion operates on *ranks*, not raw scores. Cosine similarity and
``ts_rank_cd`` are on different, uncalibrated scales, so combining raw values
would be arbitrary. RRF avoids that without pretending the scales are
comparable:

    score(chunk) = sum(weight_source / (k + rank_source))

The same chunk found by both retrievers accumulates both contributions, so it
outranks chunks found by only one. Ties break deterministically.
"""

from __future__ import annotations

from dataclasses import replace
from uuid import UUID

from app.services.vector_retrieval import CandidateChunk

DEFAULT_RRF_K = 60


def reciprocal_rank_fusion(
    ranked_lists: list[list[UUID]],
    *,
    rrf_k: int = DEFAULT_RRF_K,
    weights: list[float] | None = None,
) -> dict[UUID, float]:
    """Fuse multiple ranked ID lists into one score map."""
    if rrf_k < 1:
        raise ValueError("rrf_k must be at least 1")

    scores: dict[UUID, float] = {}
    for index, ranked in enumerate(ranked_lists):
        weight = 1.0 if weights is None else float(weights[index])
        if weight == 0:
            continue
        for rank, chunk_id in enumerate(ranked, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + weight / (rrf_k + rank)
    return scores


def fuse_candidate_lists(
    *,
    semantic_lists: list[list[CandidateChunk]] | None = None,
    keyword_lists: list[list[CandidateChunk]] | None = None,
    rrf_k: int = DEFAULT_RRF_K,
    limit: int,
    semantic_weight: float = 1.0,
    keyword_weight: float = 1.0,
) -> list[CandidateChunk]:
    """Combine semantic and keyword result lists into one candidate list."""
    if limit < 1:
        return []

    scores: dict[UUID, float] = {}
    best: dict[UUID, CandidateChunk] = {}
    sources: dict[UUID, set[str]] = {}

    def _accumulate(
        ranked: list[CandidateChunk],
        *,
        label: str,
        weight: float,
    ) -> None:
        for rank, candidate in enumerate(ranked, start=1):
            chunk_id = candidate.chunk_id
            best.setdefault(chunk_id, candidate)
            sources.setdefault(chunk_id, set()).add(label)
            if weight:
                scores[chunk_id] = scores.get(chunk_id, 0.0) + weight / (
                    rrf_k + rank
                )

    for ranked in semantic_lists or []:
        _accumulate(ranked, label="semantic", weight=semantic_weight)
    for ranked in keyword_lists or []:
        _accumulate(ranked, label="keyword", weight=keyword_weight)

    fused: list[CandidateChunk] = []
    for chunk_id, candidate in best.items():
        fused.append(
            replace(
                candidate,
                fused_score=scores.get(chunk_id, 0.0),
                retrieval_sources=tuple(sorted(sources[chunk_id])),
            )
        )

    fused.sort(
        key=lambda item: (
            -(item.fused_score or 0.0),
            -(item.semantic_similarity or 0.0),
            str(item.document_id),
            item.chunk_index,
            str(item.chunk_id),
        )
    )
    return fused[:limit]
