"""Relevance ranking over already-authorized chunks only.

The initial implementation uses pgvector cosine distance. An optional local
cross-encoder can reorder the same authorized set; it can never expand the set.
Cosine similarity is not a calibrated probability, so the threshold is a policy
gate, not a confidence score.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol, Sequence
from uuid import UUID

from app.services.retrieval_authorization import AuthorizedChunk

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RankedChunk:
    chunk_id: UUID
    tenant_id: UUID
    document_id: UUID
    chunk_index: int
    page_start: int
    page_end: int
    distance: float
    document_name: str
    text: str
    similarity: float


class SupportsRerank(Protocol):
    def score(self, question: str, texts: Sequence[str]) -> list[float]: ...


class CrossEncoderReranker:
    """Lazy optional local cross-encoder.

    Disabled by default. It is loaded only when RAG_RERANK_ENABLED=true and a
    caller explicitly constructs it.
    """

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name
        self._model = None

    def _load(self):
        if self._model is not None:
            return self._model
        from sentence_transformers import CrossEncoder

        self._model = CrossEncoder(self.model_name, device="cpu")
        return self._model

    def score(self, question: str, texts: Sequence[str]) -> list[float]:
        if not texts:
            return []
        model = self._load()
        pairs = [(question, text) for text in texts]
        values = model.predict(pairs)
        return [float(value) for value in values]


def _cosine_similarity(distance: float) -> float:
    # pgvector cosine distance is 1 - cosine similarity for normalized vectors.
    return max(-1.0, min(1.0, 1.0 - float(distance)))


def rerank_authorized_chunks(
    chunks: Sequence[AuthorizedChunk],
    *,
    min_similarity: float,
    top_n: int,
    question: str | None = None,
    reranker: SupportsRerank | None = None,
) -> list[RankedChunk]:
    """Deduplicate, threshold and rank already-authorized chunks."""
    if top_n < 1:
        return []

    candidates: list[RankedChunk] = []
    seen_chunk_ids: set[UUID] = set()
    seen_text: set[tuple[UUID, str]] = set()

    for chunk in chunks:
        if chunk.chunk_id in seen_chunk_ids:
            continue
        normalized_text = " ".join(chunk.text.split()).lower()
        duplicate_key = (chunk.document_id, normalized_text)
        if normalized_text and duplicate_key in seen_text:
            continue

        similarity = _cosine_similarity(chunk.distance)
        if similarity < min_similarity:
            continue

        seen_chunk_ids.add(chunk.chunk_id)
        if normalized_text:
            seen_text.add(duplicate_key)
        candidates.append(
            RankedChunk(
                chunk_id=chunk.chunk_id,
                tenant_id=chunk.tenant_id,
                document_id=chunk.document_id,
                chunk_index=chunk.chunk_index,
                page_start=chunk.page_start,
                page_end=chunk.page_end,
                distance=chunk.distance,
                document_name=chunk.document_name,
                text=chunk.text,
                similarity=similarity,
            )
        )

    if not candidates:
        return []

    # Stable base ordering: cosine similarity, then document/chunk identity.
    candidates.sort(
        key=lambda item: (-item.similarity, str(item.document_id), item.chunk_index)
    )

    if reranker is not None and question:
        try:
            scores = reranker.score(question, [item.text for item in candidates])
            if len(scores) == len(candidates):
                candidates = [
                    item
                    for _, item in sorted(
                        zip(scores, candidates),
                        key=lambda pair: (
                            -float(pair[0]),
                            -pair[1].similarity,
                            str(pair[1].document_id),
                            pair[1].chunk_index,
                        ),
                    )
                ]
        except Exception:
            # Optional reranking must never widen the authorized set. A failed
            # reranker degrades to the deterministic cosine ordering.
            logger.exception("Optional cross-encoder reranking failed; using cosine order")

    return candidates[:top_n]
