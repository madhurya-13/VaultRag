"""Advanced relevance ranking for already-authorized chunks.

The type boundary is the security control: this module only accepts
``AuthorizedChunk`` values, which the pipeline creates after current RBAC,
document permission, expiry/revocation and ABAC checks. Reranking is a
relevance optimization, never an authorization mechanism, and it can only
reorder or drop chunks — it can never introduce a new one.

Scoring notes
-------------
* The optional cross-encoder returns uncalibrated logits. Unless an operator
  explicitly sets ``rerank_min_score``, it is used purely for ordering.
* The cosine policy gate applies only to candidates that were found *solely*
  by semantic search. A chunk also found by keyword search must not be
  discarded because its cosine similarity is low.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Sequence
from uuid import UUID

from app.services.reranking import CrossEncoderReranker, RankedChunk, SupportsRerank
from app.services.retrieval_authorization import AuthorizedChunk

logger = logging.getLogger(__name__)

_MAX_MERGED_CHARS = 4000
_MAX_OVERLAP_PROBE = 400


def _normalized_text(text: str) -> str:
    return " ".join(text.split()).lower()


def _base_score(chunk: AuthorizedChunk) -> float:
    if chunk.semantic_similarity is not None:
        return chunk.semantic_similarity
    if chunk.fused_score is not None:
        return chunk.fused_score
    if chunk.keyword_score is not None:
        return chunk.keyword_score
    return 0.0


def _admitted(chunk: AuthorizedChunk, min_similarity: float) -> bool:
    if "keyword" in chunk.retrieval_sources:
        return True
    if chunk.semantic_similarity is None:
        return True
    return chunk.semantic_similarity >= min_similarity


def _join_without_overlap(left: str, right: str) -> str:
    """Join adjacent chunk text, removing an overlapping suffix/prefix."""
    limit = min(len(left), len(right), _MAX_OVERLAP_PROBE)
    for size in range(limit, 0, -1):
        if left[-size:].lower() == right[:size].lower():
            return f"{left}{right[size:]}"
    return f"{left}\n{right}"


def _merge_adjacent(chunks: Sequence[AuthorizedChunk]) -> list[AuthorizedChunk]:
    """Merge consecutive chunk indexes of the same document.

    Merging is order-based within a document and never crosses document or
    tenant boundaries. The merged chunk keeps the highest-ranked member's
    identity and the full page span so citations remain verifiable.
    """
    by_document: dict[UUID, list[AuthorizedChunk]] = {}
    for chunk in chunks:
        by_document.setdefault(chunk.document_id, []).append(chunk)

    merged: list[AuthorizedChunk] = []
    for items in by_document.values():
        items = sorted(items, key=lambda item: item.chunk_index)
        current: AuthorizedChunk | None = None
        for chunk in items:
            if (
                current is not None
                and chunk.chunk_index == current.chunk_index + 1
                and len(current.text) + len(chunk.text) <= _MAX_MERGED_CHARS
            ):
                combined = _join_without_overlap(current.text, chunk.text)
                current = replace(
                    current,
                    chunk_index=chunk.chunk_index,
                    page_start=min(current.page_start, chunk.page_start),
                    page_end=max(current.page_end, chunk.page_end),
                    text=combined,
                )
                continue
            if current is not None:
                merged.append(current)
            current = chunk
        if current is not None:
            merged.append(current)
    return merged


def _to_ranked(chunk: AuthorizedChunk) -> RankedChunk:
    similarity = chunk.semantic_similarity
    return RankedChunk(
        chunk_id=chunk.chunk_id,
        tenant_id=chunk.tenant_id,
        document_id=chunk.document_id,
        chunk_index=chunk.chunk_index,
        page_start=chunk.page_start,
        page_end=chunk.page_end,
        distance=chunk.distance,
        document_name=chunk.document_name,
        text=chunk.text,
        similarity=0.0 if similarity is None else similarity,
        semantic_similarity=similarity,
        fused_score=chunk.fused_score,
        keyword_score=chunk.keyword_score,
        retrieval_sources=chunk.retrieval_sources,
    )


def rerank_evidence(
    chunks: Sequence[AuthorizedChunk],
    *,
    limit: int,
    question: str | None = None,
    reranker: SupportsRerank | None = None,
    min_similarity: float = 0.0,
    max_candidates: int | None = None,
    per_document_limit: int | None = None,
    merge_adjacent: bool = True,
    rerank_min_score: float | None = None,
) -> list[RankedChunk]:
    """Deduplicate, optionally merge, score and diversify authorized chunks."""
    if limit < 1:
        return []

    admitted: list[AuthorizedChunk] = []
    seen_chunks: set[UUID] = set()
    seen_text: set[tuple[UUID, str]] = set()

    for chunk in chunks:
        if chunk.chunk_id in seen_chunks:
            continue
        normalized = _normalized_text(chunk.text)
        key = (chunk.document_id, normalized)
        if normalized and key in seen_text:
            continue
        if not _admitted(chunk, min_similarity):
            continue
        seen_chunks.add(chunk.chunk_id)
        if normalized:
            seen_text.add(key)
        admitted.append(chunk)

    if not admitted:
        return []

    if merge_adjacent:
        admitted = _merge_adjacent(admitted)

    admitted.sort(
        key=lambda item: (
            -_base_score(item),
            str(item.document_id),
            item.chunk_index,
            str(item.chunk_id),
        )
    )
    if max_candidates is not None and max_candidates > 0:
        admitted = admitted[:max_candidates]

    ranked = [_to_ranked(chunk) for chunk in admitted]

    if reranker is not None and question:
        try:
            scores = reranker.score(question, [item.text for item in ranked])
            if len(scores) == len(ranked):
                scored = [
                    replace(item, rerank_score=float(score))
                    for score, item in zip(scores, ranked)
                ]
                if rerank_min_score is not None:
                    scored = [
                        item
                        for item in scored
                        if item.rerank_score is not None
                        and item.rerank_score >= rerank_min_score
                    ]
                ranked = scored
        except Exception:
            # A failed optional reranker degrades to deterministic ordering.
            # It must never widen or bypass the authorized set.
            logger.exception("Cross-encoder reranking failed; using base ordering")

    def order_key(item: RankedChunk):
        rerank_score = item.rerank_score if item.rerank_score is not None else float("-inf")
        return (
            -rerank_score,
            -(item.fused_score or 0.0),
            -(item.semantic_similarity or 0.0),
            str(item.document_id),
            item.chunk_index,
        )

    ranked.sort(key=order_key)

    if per_document_limit is not None and per_document_limit > 0:
        selected: list[RankedChunk] = []
        counts: dict[UUID, int] = {}
        for item in ranked:
            if counts.get(item.document_id, 0) >= per_document_limit:
                continue
            counts[item.document_id] = counts.get(item.document_id, 0) + 1
            selected.append(item)
            if len(selected) >= limit:
                break
        if len(selected) < limit:
            chosen = {item.chunk_id for item in selected}
            for item in ranked:
                if len(selected) >= limit:
                    break
                if item.chunk_id in chosen:
                    continue
                selected.append(item)
        order = {item.chunk_id: index for index, item in enumerate(ranked)}
        selected.sort(key=lambda item: order.get(item.chunk_id, 0))
        ranked = selected

    return ranked[:limit]


__all__ = ["CrossEncoderReranker", "RankedChunk", "rerank_evidence"]
