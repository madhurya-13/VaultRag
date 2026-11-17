"""Semantic retrieval adapter with ranking provenance.

The SQL and its security predicates live in
``app.services.vector_retrieval`` and ``app.services.rag.retrieval_predicates``.
This module adds the provenance fields the fusion stage needs without changing
the proven retrieval path or the "no text before authorization" property.
"""

from __future__ import annotations

from dataclasses import replace
from uuid import UUID

from sqlalchemy.orm import Session

from app.dependencies.authentication import AuthenticatedContext
from app.schemas.rag import RAGFilters
from app.services.vector_retrieval import CandidateChunk, retrieve_candidate_chunks


def retrieve_semantic_candidates(
    db: Session,
    context: AuthenticatedContext,
    query_embedding: list[float],
    *,
    top_k: int,
    model_label: str,
    role_ids: frozenset[UUID],
    filters: RAGFilters | None = None,
) -> list[CandidateChunk]:
    """Return cosine-ranked metadata candidates with semantic provenance."""
    candidates = retrieve_candidate_chunks(
        db,
        context,
        query_embedding,
        top_k=top_k,
        model_label=model_label,
        role_ids=role_ids,
        filters=filters,
    )
    return [
        replace(
            candidate,
            semantic_similarity=max(-1.0, min(1.0, 1.0 - candidate.distance)),
            retrieval_sources=("semantic",),
            retrieval_rank=rank,
        )
        for rank, candidate in enumerate(candidates, start=1)
    ]
