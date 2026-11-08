"""Validated, transactional chunk storage with pgvector.

This module never commits. The ingestion service owns explicit transactions so
chunk inserts, document totals and the ingestion job status can commit or roll
back together. RLS remains enabled and tenant context must already be set.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security_constants import ERROR_CHUNK_STORAGE, ERROR_CHUNK_VALIDATION
from app.models import Document, DocumentChunk
from app.services.chunk_metadata import (
    PermissionSnapshot,
    validate_metadata_against_document,
)
from app.services.chunking import ChunkDraft

logger = logging.getLogger(__name__)

ALLOWED_METADATA_KEYS = frozenset(
    {
        "tenant_id",
        "document_id",
        "department",
        "classification",
        "required_clearance_level",
        "allowed_roles",
        "allowed_role_ids",
        "allowed_user_ids",
        "permission_snapshot_at",
        "embedding_model",
        "embedding_model_version",
    }
)


class ChunkStorageError(Exception):
    code = ERROR_CHUNK_STORAGE
    default_message = "Document chunks could not be stored"

    def __init__(self, message: str | None = None) -> None:
        self.code = type(self).code
        self.safe_message = message or self.default_message
        super().__init__(self.safe_message)


class ChunkValidationError(ChunkStorageError):
    code = ERROR_CHUNK_VALIDATION
    default_message = "A document chunk failed security validation"


def validate_chunk_batch(
    *,
    document: Document,
    chunks: Sequence[ChunkDraft],
    embeddings: Sequence[Sequence[float]],
    metadata: dict,
    snapshot: PermissionSnapshot,
    embedding_dimension: int,
    total_pages: int,
) -> None:
    """Validate every chunk before a single row reaches PostgreSQL."""
    if not chunks:
        raise ChunkValidationError("No chunks were supplied")
    if len(chunks) != len(embeddings):
        raise ChunkValidationError("Chunk and embedding counts do not match")
    if not document.is_active:
        raise ChunkValidationError("Parent document is inactive")
    if not document.tenant_id or not document.document_id:
        raise ChunkValidationError("Parent document identity is incomplete")
    if total_pages < 1:
        raise ChunkValidationError("Parent document has no page count")

    extra_keys = set(metadata) - ALLOWED_METADATA_KEYS
    if extra_keys:
        raise ChunkValidationError("Chunk metadata contains unexpected privileged keys")
    try:
        validate_metadata_against_document(metadata, document, snapshot)
    except ValueError as exc:
        raise ChunkValidationError(str(exc)) from exc

    seen_indexes: set[int] = set()
    for expected_index, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
        if chunk.chunk_index != expected_index:
            raise ChunkValidationError("Chunk indexes are not sequential")
        if chunk.chunk_index in seen_indexes:
            raise ChunkValidationError("Duplicate chunk index")
        seen_indexes.add(chunk.chunk_index)

        if not chunk.chunk_text.strip():
            raise ChunkValidationError("Chunk text is empty")
        if chunk.page_start < 1:
            raise ChunkValidationError("Chunk page_start is invalid")
        if chunk.page_end < chunk.page_start:
            raise ChunkValidationError("Chunk page_end precedes page_start")
        if chunk.page_end > total_pages:
            raise ChunkValidationError("Chunk page_end exceeds document page count")

        if len(embedding) != embedding_dimension:
            raise ChunkValidationError("Chunk embedding has the wrong dimension")
        if not all(math.isfinite(value) for value in embedding):
            raise ChunkValidationError("Chunk embedding contains non-finite values")


def document_has_chunks(
    db: Session,
    *,
    tenant_id: UUID,
    document_id: UUID,
) -> bool:
    count = db.scalar(
        select(func.count())
        .select_from(DocumentChunk)
        .where(
            DocumentChunk.tenant_id == tenant_id,
            DocumentChunk.document_id == document_id,
        )
    )
    return bool(count)


def store_chunks(
    db: Session,
    *,
    document: Document,
    chunks: Sequence[ChunkDraft],
    embeddings: Sequence[Sequence[float]],
    metadata: dict,
) -> int:
    """Insert one validated chunk batch inside a savepoint.

    The caller must set tenant context and own the outer transaction. A failure
    rolls back the savepoint; the caller then rolls back the outer transaction
    and records FAILED in a separate transaction.
    """
    rows = [
        DocumentChunk(
            tenant_id=document.tenant_id,
            document_id=document.document_id,
            chunk_index=chunk.chunk_index,
            chunk_text=chunk.chunk_text,
            embedding=list(embedding),
            page_start=chunk.page_start,
            page_end=chunk.page_end,
            chunk_metadata=dict(metadata),
        )
        for chunk, embedding in zip(chunks, embeddings)
    ]

    try:
        with db.begin_nested():
            db.add_all(rows)
            db.flush()
    except IntegrityError as exc:
        logger.warning(
            "Chunk insert violated a database constraint document_id=%s",
            document.document_id,
        )
        raise ChunkStorageError("Chunk storage was rejected by the database") from exc
    except Exception as exc:
        logger.exception(
            "Chunk storage failed document_id=%s",
            document.document_id,
        )
        raise ChunkStorageError() from exc

    return len(rows)
