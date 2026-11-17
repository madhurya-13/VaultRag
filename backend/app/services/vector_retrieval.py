"""Tenant-filtered pgvector candidate retrieval.

The first retrieval stage deliberately selects **no chunk text**. It selects
only chunk identifiers, page attribution, cosine distance and the document
security metadata needed by the authorization layer. Text is fetched in a later
stage only for chunk IDs that passed current authorization.

Every query in this module relies on the existing transaction-local RLS
context. Explicit tenant predicates are present as defence in depth; RLS remains
the database-level boundary. Keyword retrieval uses the same shared predicate
builder (``app.services.rag.retrieval_predicates``).
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import BadRequestError, ServiceUnavailableError
from app.dependencies.authentication import AuthenticatedContext
from app.models import Document, DocumentChunk
from app.schemas.rag import RAGFilters
from app.services.rag.retrieval_predicates import (
    candidate_where_clause,
    document_join_condition,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CandidateChunk:
    """A retrieval row with no chunk text attached."""

    chunk_id: UUID
    tenant_id: UUID
    document_id: UUID
    chunk_index: int
    page_start: int
    page_end: int
    distance: float
    document_name: str
    department: str
    classification: str
    required_clearance_level: int
    document_is_active: bool
    processing_status: str
    embedding_model: str | None
    # Additive Milestone 7 provenance. Keyword candidates leave
    # ``semantic_similarity`` as None so a cosine threshold is never applied to
    # a non-semantic match.
    semantic_similarity: float | None = None
    keyword_score: float | None = None
    fused_score: float | None = None
    retrieval_sources: tuple[str, ...] = ()
    retrieval_rank: int | None = None


@dataclass(frozen=True, slots=True)
class ChunkText:
    chunk_id: UUID
    document_id: UUID
    chunk_index: int
    page_start: int
    page_end: int
    text: str


def validate_top_k(top_k: int) -> int:
    settings = get_settings()
    if not isinstance(top_k, int) or isinstance(top_k, bool):
        raise BadRequestError("top_k must be an integer")
    if top_k < 1 or top_k > settings.rag_top_k_max:
        raise BadRequestError(
            f"top_k must be between 1 and {settings.rag_top_k_max}"
        )
    return top_k


def _validate_query_vector(
    query_embedding: list[float], dimension: int = 384
) -> list[float]:
    if len(query_embedding) != dimension:
        raise ServiceUnavailableError(
            "The query embedding dimension does not match the vector column"
        )
    if any(not math.isfinite(float(value)) for value in query_embedding):
        raise ServiceUnavailableError("The query embedding contains invalid values")
    return [float(value) for value in query_embedding]


def retrieve_candidate_chunks(
    db: Session,
    context: AuthenticatedContext,
    query_embedding: list[float],
    *,
    top_k: int,
    model_label: str,
    role_ids: frozenset[UUID],
    filters: RAGFilters | None = None,
) -> list[CandidateChunk]:
    """Return authorized-by-SQL *candidates* without selecting chunk text.

    The SQL predicate applies:
    * tenant equality on both chunk and document;
    * active and fully processed parent document;
    * matching embedding model label;
    * any validated metadata filters (narrowing only);
    * an active, unexpired READ permission for the user or one of their
      current roles, unless the caller currently holds ADMIN.

    ABAC is evaluated in the next stage using the existing central engine.
    """
    limit = validate_top_k(top_k)
    vector = _validate_query_vector(query_embedding)
    if not model_label:
        raise ServiceUnavailableError("The query embedding model is not configured")

    distance = DocumentChunk.embedding.cosine_distance(vector).label("distance")

    statement = (
        select(
            DocumentChunk.chunk_id,
            DocumentChunk.tenant_id,
            DocumentChunk.document_id,
            DocumentChunk.chunk_index,
            DocumentChunk.page_start,
            DocumentChunk.page_end,
            distance,
            Document.document_name,
            Document.department,
            Document.classification,
            Document.required_clearance_level,
            Document.is_active.label("document_is_active"),
            Document.processing_status,
            Document.embedding_model,
        )
        .select_from(DocumentChunk)
        .join(Document, document_join_condition())
        .where(
            *candidate_where_clause(
                context,
                model_label=model_label,
                role_ids=role_ids,
                filters=filters,
            )
        )
        .order_by(distance.asc(), DocumentChunk.chunk_id.asc())
        .limit(limit)
    )

    query_started = time.monotonic()
    rows = db.execute(statement).all()
    logger.info(
        "RAG stage=vector_query duration_ms=%d top_k=%d candidates=%d",
        int((time.monotonic() - query_started) * 1000),
        limit,
        len(rows),
    )
    return [
        CandidateChunk(
            chunk_id=row.chunk_id,
            tenant_id=row.tenant_id,
            document_id=row.document_id,
            chunk_index=row.chunk_index,
            page_start=row.page_start,
            page_end=row.page_end,
            distance=float(row.distance),
            document_name=row.document_name,
            department=row.department,
            classification=row.classification,
            required_clearance_level=row.required_clearance_level,
            document_is_active=row.document_is_active,
            processing_status=row.processing_status,
            embedding_model=row.embedding_model,
            semantic_similarity=max(-1.0, min(1.0, 1.0 - float(row.distance))),
            retrieval_sources=("semantic",),
            retrieval_rank=rank,
        )
        for rank, row in enumerate(rows, start=1)
    ]


def fetch_chunk_texts(
    db: Session,
    context: AuthenticatedContext,
    chunk_ids: list[UUID],
) -> dict[UUID, ChunkText]:
    """Fetch text only for chunk IDs that already passed authorization."""
    if not chunk_ids:
        return {}

    query_started = time.monotonic()
    rows = db.execute(
        select(
            DocumentChunk.chunk_id,
            DocumentChunk.document_id,
            DocumentChunk.chunk_index,
            DocumentChunk.page_start,
            DocumentChunk.page_end,
            DocumentChunk.chunk_text,
        ).where(
            DocumentChunk.tenant_id == context.tenant_id,
            DocumentChunk.chunk_id.in_(chunk_ids),
        )
    ).all()
    logger.info(
        "RAG stage=authorized_text_fetch duration_ms=%d chunks=%d",
        int((time.monotonic() - query_started) * 1000),
        len(rows),
    )

    return {
        row.chunk_id: ChunkText(
            chunk_id=row.chunk_id,
            document_id=row.document_id,
            chunk_index=row.chunk_index,
            page_start=row.page_start,
            page_end=row.page_end,
            text=row.chunk_text,
        )
        for row in rows
    }
