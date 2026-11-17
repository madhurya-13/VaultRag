"""PostgreSQL full-text keyword retrieval.

Uses ``websearch_to_tsquery('simple', ...)`` against the generated
``document_chunks.content_tsv`` column and ranks with ``ts_rank_cd``. The
``simple`` configuration is intentional: employee IDs, policy numbers, product
codes and exact phrases are not stemmed away.

Security
--------
* No chunk text is selected at this stage.
* The WHERE clause is the same shared predicate set used by semantic search
  (tenant, active/COMPLETED document, embedding model, permission EXISTS).
* RLS remains the database-level boundary.
"""

from __future__ import annotations

import logging
import time
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.exceptions import ServiceUnavailableError
from app.dependencies.authentication import AuthenticatedContext
from app.models import Document, DocumentChunk
from app.schemas.rag import RAGFilters
from app.services.rag.retrieval_predicates import (
    candidate_where_clause,
    document_join_condition,
)
from app.services.vector_retrieval import CandidateChunk, validate_top_k

logger = logging.getLogger(__name__)

MAX_KEYWORD_QUERY_CHARS = 500


def normalize_keyword_query(text: str) -> str:
    """Bound and normalize a query before it reaches ``websearch_to_tsquery``.

    ``websearch_to_tsquery`` is already tolerant of malformed input, but a
    bounded query keeps the GIN lookup and ranking cost predictable. Control
    characters and NUL are removed. Database syntax is never concatenated.
    """
    if not text:
        return ""
    cleaned = text.replace("\x00", " ")
    cleaned = " ".join(cleaned.split())
    cleaned = cleaned[:MAX_KEYWORD_QUERY_CHARS]
    if not any(character.isalnum() for character in cleaned):
        return ""
    return cleaned


def retrieve_keyword_candidates(
    db: Session,
    context: AuthenticatedContext,
    query_text: str,
    *,
    top_k: int,
    model_label: str,
    role_ids: frozenset[UUID],
    filters: RAGFilters | None = None,
) -> list[CandidateChunk]:
    """Return keyword-ranked candidate metadata without any chunk text."""
    limit = validate_top_k(top_k)
    normalized = normalize_keyword_query(query_text)
    if not normalized:
        return []
    if not model_label:
        raise ServiceUnavailableError("The query embedding model is not configured")

    tsquery = func.websearch_to_tsquery("simple", normalized)
    rank = func.ts_rank_cd(DocumentChunk.content_tsv, tsquery)

    statement = (
        select(
            DocumentChunk.chunk_id,
            DocumentChunk.tenant_id,
            DocumentChunk.document_id,
            DocumentChunk.chunk_index,
            DocumentChunk.page_start,
            DocumentChunk.page_end,
            Document.document_name,
            Document.department,
            Document.classification,
            Document.required_clearance_level,
            Document.is_active.label("document_is_active"),
            Document.processing_status,
            Document.embedding_model,
            rank.label("keyword_score"),
        )
        .select_from(DocumentChunk)
        .join(Document, document_join_condition())
        .where(
            DocumentChunk.content_tsv.op("@@")(tsquery),
            *candidate_where_clause(
                context,
                model_label=model_label,
                role_ids=role_ids,
                filters=filters,
            ),
        )
        .order_by(rank.desc(), DocumentChunk.chunk_id.asc())
        .limit(limit)
    )

    started = time.monotonic()
    rows = db.execute(statement).all()
    logger.info(
        "RAG stage=keyword_query duration_ms=%d top_k=%d candidates=%d",
        int((time.monotonic() - started) * 1000),
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
            # Cosine distance is not defined for a keyword match. The value is
            # never used as a semantic score: semantic_similarity stays None and
            # the cosine policy gate is skipped for this candidate.
            distance=1.0,
            document_name=row.document_name,
            department=row.department,
            classification=row.classification,
            required_clearance_level=row.required_clearance_level,
            document_is_active=row.document_is_active,
            processing_status=row.processing_status,
            embedding_model=row.embedding_model,
            semantic_similarity=None,
            keyword_score=float(row.keyword_score),
            retrieval_sources=("keyword",),
        )
        for row in rows
    ]
