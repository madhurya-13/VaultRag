"""Document ingestion orchestration.

The pipeline is deliberately synchronous in this milestone but its transaction
boundaries are worker-friendly:

1. claim: lock the document, reject concurrent work, create the job and mark
   the document PROCESSING; commit immediately;
2. process outside any transaction: extract, clean, chunk and embed;
3. finalise: one transaction inserts every chunk, updates document totals and
   marks both document and job COMPLETED;
4. on failure: the finalise transaction rolls back and a separate short
   transaction records FAILED.

This guarantees a document is never left COMPLETED with a partial chunk set.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import SessionLocal, set_tenant_context
from app.core.exceptions import (
    AppError,
    ConflictError,
    NotFoundError,
    ServiceUnavailableError,
    UnprocessableDocumentError,
)
from app.core.request_context import RequestMeta
from app.core.security_constants import (
    DOC_STATUS_COMPLETED,
    DOC_STATUS_FAILED,
    DOC_STATUS_PROCESSING,
    ERROR_CHUNK_STORAGE,
    ERROR_CHUNK_VALIDATION,
    ERROR_DOCUMENT_HAS_NO_FILE,
    ERROR_EMBEDDING_MODEL,
    ERROR_INGESTION_INTERNAL,
    ERROR_INVALID_EMBEDDING,
    ERROR_PERMISSION_SNAPSHOT,
    ERROR_STORAGE_FAILURE,
    ERROR_TEXT_CLEANING_FAILED,
    INGESTION_STATUS_COMPLETED,
    INGESTION_STATUS_FAILED,
    INGESTION_STATUS_PENDING,
    INGESTION_STATUS_PROCESSING,
    POLICY_INGESTION,
    PROCESSABLE_DOCUMENT_STATUSES,
)
from app.dependencies.authentication import AuthenticatedContext
from app.models import Document, DocumentChunk, DocumentIngestionJob
from app.services.chunk_metadata import (
    PermissionSnapshot,
    build_chunk_metadata,
    load_permission_snapshot,
)
from app.services.chunk_storage import (
    ChunkStorageError,
    ChunkValidationError,
    document_has_chunks,
    store_chunks,
    validate_chunk_batch,
)
from app.services.chunking import ChunkingConfig, ChunkingError, chunk_pages
from app.services.document_processor import (
    DocumentProcessingError,
    extract_pdf_text,
)
from app.services.embedding_service import (
    EmbeddingService,
    EmbeddingServiceError,
    get_embedding_service,
)
from app.services.security_audit import log_security_event
from app.services.storage import PrivateStorage, get_private_storage
from app.services.text_cleaner import clean_text

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], Session]


@dataclass(frozen=True, slots=True)
class IngestionResult:
    document_id: UUID
    job_id: UUID
    processing_status: str
    total_pages: int
    total_chunks: int
    embedding_model: str | None


@dataclass(frozen=True, slots=True)
class _ClaimedIngestion:
    document_id: UUID
    job_id: UUID
    storage_path: str


@dataclass(frozen=True)
class _ProcessingFailure(Exception):
    code: str
    message: str
    status_code: int = 422

    def __str__(self) -> str:
        return self.message


def _session_factory(session_factory: SessionFactory | None) -> SessionFactory:
    return session_factory or SessionLocal


def _claim_document(
    session_factory: SessionFactory,
    *,
    context: AuthenticatedContext,
    document_id: UUID,
    request_meta: RequestMeta,
) -> _ClaimedIngestion:
    """Atomically claim a document for processing."""
    now = datetime.now(timezone.utc)
    job_id = uuid4()
    try:
        with session_factory() as session:
            with session.begin():
                set_tenant_context(session, context.tenant_id)
                document = session.scalar(
                    select(Document)
                    .where(
                        Document.tenant_id == context.tenant_id,
                        Document.document_id == document_id,
                    )
                    .with_for_update()
                )
                if document is None:
                    raise NotFoundError("Document not found")
                if not document.is_active:
                    raise ConflictError("Document is inactive")
                if document.processing_status not in PROCESSABLE_DOCUMENT_STATUSES:
                    raise ConflictError(
                        "Document is not in a processable state "
                        f"({document.processing_status})"
                    )
                if not document.storage_path or not document.file_hash:
                    raise UnprocessableDocumentError(
                        "Document has no stored PDF and cannot be processed"
                    )

                active_job = session.scalar(
                    select(DocumentIngestionJob).where(
                        DocumentIngestionJob.tenant_id == context.tenant_id,
                        DocumentIngestionJob.document_id == document_id,
                        DocumentIngestionJob.status.in_(
                            {
                                INGESTION_STATUS_PENDING,
                                INGESTION_STATUS_PROCESSING,
                            }
                        ),
                    )
                )
                if active_job is not None:
                    raise ConflictError("Ingestion is already running for this document")
                if document_has_chunks(
                    session,
                    tenant_id=context.tenant_id,
                    document_id=document_id,
                ):
                    raise ConflictError(
                        "Document already has stored chunks; cleanup is required "
                        "before reprocessing"
                    )

                previous_jobs = session.scalar(
                    select(func.count())
                    .select_from(DocumentIngestionJob)
                    .where(
                        DocumentIngestionJob.tenant_id == context.tenant_id,
                        DocumentIngestionJob.document_id == document_id,
                    )
                )
                job = DocumentIngestionJob(
                    job_id=job_id,
                    tenant_id=context.tenant_id,
                    document_id=document_id,
                    status=INGESTION_STATUS_PROCESSING,
                    started_at=now,
                    retry_count=int(previous_jobs or 0),
                )
                document.processing_status = DOC_STATUS_PROCESSING
                document.total_pages = 0
                document.total_chunks = 0
                document.embedding_model = None
                session.add(job)
                session.flush()

                log_security_event(
                    session,
                    tenant_id=context.tenant_id,
                    user_id=context.user.user_id,
                    resource_type="DOCUMENT",
                    resource_id=document_id,
                    action="INGESTION_START",
                    decision="ALLOW",
                    reason=f"Ingestion job {job.job_id} started",
                    policy=POLICY_INGESTION,
                    request_meta=request_meta,
                )
                return _ClaimedIngestion(
                    document_id=document_id,
                    job_id=job_id,
                    storage_path=document.storage_path,
                )
    except IntegrityError as exc:
        # The partial unique index on active jobs is the final concurrency
        # guard if two claim transactions race.
        raise ConflictError("Ingestion is already running for this document") from exc


def _load_processing_document(
    session_factory: SessionFactory,
    *,
    context: AuthenticatedContext,
    document_id: UUID,
) -> tuple[Document, PermissionSnapshot]:
    """Load the authoritative document and current permission snapshot."""
    with session_factory() as session:
        with session.begin():
            set_tenant_context(session, context.tenant_id)
            document = session.scalar(
                select(Document).where(
                    Document.tenant_id == context.tenant_id,
                    Document.document_id == document_id,
                )
            )
            if document is None:
                raise _ProcessingFailure(
                    ERROR_INGESTION_INTERNAL,
                    "Document disappeared during ingestion",
                    500,
                )
            if (
                document.processing_status != DOC_STATUS_PROCESSING
                or not document.is_active
            ):
                raise _ProcessingFailure(
                    ERROR_INGESTION_INTERNAL,
                    "Document state changed during ingestion",
                    409,
                )
            try:
                snapshot = load_permission_snapshot(
                    session,
                    tenant_id=context.tenant_id,
                    document_id=document.document_id,
                )
            except Exception as exc:
                logger.exception(
                    "Failed to load permission snapshot document_id=%s",
                    document.document_id,
                )
                raise _ProcessingFailure(
                    ERROR_PERMISSION_SNAPSHOT,
                    "Document permission snapshot could not be loaded",
                    500,
                ) from exc
            return document, snapshot


def _record_failure(
    session_factory: SessionFactory,
    *,
    context: AuthenticatedContext,
    document_id: UUID,
    job_id: UUID,
    failure: _ProcessingFailure,
    pages_processed: int,
    request_meta: RequestMeta,
) -> None:
    """Persist FAILED in a separate transaction; never leave a fake COMPLETED."""
    now = datetime.now(timezone.utc)
    try:
        with session_factory() as session:
            with session.begin():
                set_tenant_context(session, context.tenant_id)
                document = session.scalar(
                    select(Document)
                    .where(
                        Document.tenant_id == context.tenant_id,
                        Document.document_id == document_id,
                    )
                    .with_for_update()
                )
                job = session.scalar(
                    select(DocumentIngestionJob)
                    .where(
                        DocumentIngestionJob.tenant_id == context.tenant_id,
                        DocumentIngestionJob.document_id == document_id,
                        DocumentIngestionJob.job_id == job_id,
                    )
                    .with_for_update()
                )

                if document is not None and document.processing_status == DOC_STATUS_PROCESSING:
                    document.processing_status = DOC_STATUS_FAILED
                    document.total_pages = 0
                    document.total_chunks = 0
                    document.embedding_model = None

                if job is not None and job.status in {
                    INGESTION_STATUS_PENDING,
                    INGESTION_STATUS_PROCESSING,
                }:
                    job.status = INGESTION_STATUS_FAILED
                    job.completed_at = now
                    job.error_code = failure.code
                    job.error_message = failure.message[:1000]
                    job.pages_processed = max(0, pages_processed)
                    job.chunks_created = 0

                log_security_event(
                    session,
                    tenant_id=context.tenant_id,
                    user_id=context.user.user_id,
                    resource_type="DOCUMENT",
                    resource_id=document_id,
                    action="INGESTION_FAIL",
                    decision="DENY",
                    reason=f"Ingestion failed safely with code {failure.code}",
                    policy=POLICY_INGESTION,
                    request_meta=request_meta,
                )
    except Exception:
        logger.exception(
            "Failed to persist ingestion failure document_id=%s code=%s",
            document_id,
            failure.code,
        )


def _public_failure(failure: _ProcessingFailure) -> AppError:
    if failure.status_code == 409:
        return ConflictError(failure.message)
    if failure.status_code == 404:
        return NotFoundError(failure.message)
    if failure.status_code >= 500:
        return ServiceUnavailableError(failure.message)
    return UnprocessableDocumentError(failure.message)


def process_document(
    context: AuthenticatedContext,
    document_id: UUID,
    *,
    request_meta: RequestMeta,
    embedding_service: EmbeddingService | None = None,
    storage: PrivateStorage | None = None,
    session_factory: SessionFactory | None = None,
) -> IngestionResult:
    """Run the full ingestion pipeline for one tenant-owned document."""
    pipeline_started = time.monotonic()
    stage_ms: dict[str, int] = {}
    factory = _session_factory(session_factory)
    settings = get_settings()
    backend = storage or get_private_storage()
    embedder = embedding_service or get_embedding_service()

    claim_started = time.monotonic()
    claim = _claim_document(
        factory,
        context=context,
        document_id=document_id,
        request_meta=request_meta,
    )
    stage_ms["claim"] = int((time.monotonic() - claim_started) * 1000)
    pages_seen = 0

    try:
        document, snapshot = _load_processing_document(
            factory,
            context=context,
            document_id=document_id,
        )

        try:
            path = backend.path_for(claim.storage_path)
        except ValueError as exc:
            raise _ProcessingFailure(
                ERROR_STORAGE_FAILURE,
                "Stored document path is invalid",
                500,
            ) from exc
        if not path.is_file():
            raise _ProcessingFailure(
                ERROR_DOCUMENT_HAS_NO_FILE,
                "Stored PDF is missing from private storage",
                422,
            )

        extraction_started = time.monotonic()
        try:
            extraction = extract_pdf_text(
                path,
                min_extractable_chars=settings.pdf_min_extractable_chars,
                max_pages=settings.max_pdf_pages,
            )
        except DocumentProcessingError as exc:
            raise _ProcessingFailure(exc.code, exc.safe_message, 422) from exc
        stage_ms["extraction"] = int(
            (time.monotonic() - extraction_started) * 1000
        )
        pages_seen = extraction.total_pages

        cleaning_started = time.monotonic()
        cleaned_pages: list[tuple[int, str]] = []
        meaningful_chars = 0
        for page in extraction.pages:
            cleaned = clean_text(page.text)
            if cleaned:
                cleaned_pages.append((page.page_number, cleaned))
                meaningful_chars += len(cleaned)
        if meaningful_chars == 0 or not cleaned_pages:
            raise _ProcessingFailure(
                ERROR_TEXT_CLEANING_FAILED,
                "No meaningful text remained after cleaning",
                422,
            )
        stage_ms["cleaning"] = int(
            (time.monotonic() - cleaning_started) * 1000
        )

        chunking_started = time.monotonic()
        try:
            tokenizer = embedder.tokenizer
        except EmbeddingServiceError as exc:
            raise _ProcessingFailure(
                ERROR_EMBEDDING_MODEL,
                "Embedding model is unavailable",
                503,
            ) from exc

        chunk_config = ChunkingConfig(
            chunk_size_tokens=settings.chunk_size_tokens,
            chunk_overlap_tokens=settings.chunk_overlap_tokens,
            min_tokens=settings.chunk_min_tokens,
            max_tokens=settings.chunk_max_tokens,
        )
        try:
            chunks = chunk_pages(cleaned_pages, tokenizer, chunk_config)
        except ChunkingError as exc:
            raise _ProcessingFailure(exc.code, exc.safe_message, 422) from exc
        stage_ms["chunking"] = int(
            (time.monotonic() - chunking_started) * 1000
        )

        embedding_started = time.monotonic()
        try:
            embeddings = embedder.generate_embeddings(
                [chunk.chunk_text for chunk in chunks]
            )
        except EmbeddingServiceError as exc:
            raise _ProcessingFailure(
                exc.code,
                exc.safe_message,
                503,
            ) from exc
        if len(embeddings) != len(chunks):
            raise _ProcessingFailure(
                ERROR_INVALID_EMBEDDING,
                "Embedding count does not match chunk count",
                503,
            )
        stage_ms["embedding"] = int(
            (time.monotonic() - embedding_started) * 1000
        )

        model_name = getattr(embedder, "model_name", settings.embedding_model_name)
        model_version = getattr(
            embedder,
            "model_version",
            settings.embedding_model_version,
        )
        model_label = getattr(
            embedder,
            "model_label",
            f"{model_name}@{model_version}",
        )
        metadata = build_chunk_metadata(
            document,
            snapshot,
            embedding_model=model_name,
            embedding_model_version=model_version,
        )

        # Final transaction: chunks, document totals/status and job completion
        # must all commit together.
        persistence_started = time.monotonic()
        try:
            with factory() as session:
                with session.begin():
                    set_tenant_context(session, context.tenant_id)
                    final_document = session.scalar(
                        select(Document)
                        .where(
                            Document.tenant_id == context.tenant_id,
                            Document.document_id == document_id,
                        )
                        .with_for_update()
                    )
                    final_job = session.scalar(
                        select(DocumentIngestionJob)
                        .where(
                            DocumentIngestionJob.tenant_id == context.tenant_id,
                            DocumentIngestionJob.document_id == document_id,
                            DocumentIngestionJob.job_id == claim.job_id,
                        )
                        .with_for_update()
                    )
                    if final_document is None or final_job is None:
                        raise _ProcessingFailure(
                            ERROR_INGESTION_INTERNAL,
                            "Ingestion state disappeared before finalisation",
                            500,
                        )
                    if (
                        final_document.processing_status != DOC_STATUS_PROCESSING
                        or final_job.status != INGESTION_STATUS_PROCESSING
                    ):
                        raise _ProcessingFailure(
                            ERROR_INGESTION_INTERNAL,
                            "Ingestion state changed before finalisation",
                            409,
                        )
                    if document_has_chunks(
                        session,
                        tenant_id=context.tenant_id,
                        document_id=document_id,
                    ):
                        raise _ProcessingFailure(
                            ERROR_INGESTION_INTERNAL,
                            "Document already has chunks",
                            409,
                        )

                    final_document.total_pages = extraction.total_pages
                    final_document.total_chunks = len(chunks)
                    final_document.embedding_model = model_label
                    final_document.processing_status = DOC_STATUS_COMPLETED

                    try:
                        validate_chunk_batch(
                            document=final_document,
                            chunks=chunks,
                            embeddings=embeddings,
                            metadata=metadata,
                            snapshot=snapshot,
                            embedding_dimension=settings.embedding_dimension,
                            total_pages=extraction.total_pages,
                        )
                    except ChunkValidationError as exc:
                        raise _ProcessingFailure(
                            ERROR_CHUNK_VALIDATION,
                            "A document chunk failed security validation",
                            500,
                        ) from exc

                    try:
                        store_chunks(
                            session,
                            document=final_document,
                            chunks=chunks,
                            embeddings=embeddings,
                            metadata=metadata,
                        )
                    except ChunkStorageError as exc:
                        raise _ProcessingFailure(
                            ERROR_CHUNK_STORAGE,
                            "Document chunks could not be stored",
                            500,
                        ) from exc

                    final_job.status = INGESTION_STATUS_COMPLETED
                    final_job.completed_at = datetime.now(timezone.utc)
                    final_job.pages_processed = extraction.total_pages
                    final_job.chunks_created = len(chunks)
                    final_job.error_code = None
                    final_job.error_message = None

                    log_security_event(
                        session,
                        tenant_id=context.tenant_id,
                        user_id=context.user.user_id,
                        resource_type="DOCUMENT",
                        resource_id=document_id,
                        action="INGESTION_COMPLETE",
                        decision="ALLOW",
                        reason=(
                            f"Ingestion stored {len(chunks)} chunks across "
                            f"{extraction.total_pages} pages"
                        ),
                        policy=POLICY_INGESTION,
                        request_meta=request_meta,
                    )
        except _ProcessingFailure:
            raise
        except Exception as exc:
            logger.exception("Chunk finalisation failed document_id=%s", document_id)
            raise _ProcessingFailure(
                ERROR_CHUNK_STORAGE,
                "Document chunks could not be stored",
                500,
            ) from exc
        stage_ms["persistence"] = int(
            (time.monotonic() - persistence_started) * 1000
        )

        logger.info(
            "Ingestion timing document_id=%s job_id=%s total_ms=%d claim_ms=%d extraction_ms=%d cleaning_ms=%d chunking_ms=%d embedding_ms=%d persistence_ms=%d pages=%d chunks=%d",
            document_id,
            claim.job_id,
            int((time.monotonic() - pipeline_started) * 1000),
            stage_ms["claim"],
            stage_ms["extraction"],
            stage_ms["cleaning"],
            stage_ms["chunking"],
            stage_ms["embedding"],
            stage_ms["persistence"],
            extraction.total_pages,
            len(chunks),
        )

        return IngestionResult(
            document_id=document_id,
            job_id=claim.job_id,
            processing_status=DOC_STATUS_COMPLETED,
            total_pages=extraction.total_pages,
            total_chunks=len(chunks),
            embedding_model=model_label,
        )
    except _ProcessingFailure as failure:
        logger.warning(
            "Ingestion failed safely document_id=%s code=%s",
            document_id,
            failure.code,
        )
        _record_failure(
            factory,
            context=context,
            document_id=document_id,
            job_id=claim.job_id,
            failure=failure,
            pages_processed=pages_seen,
            request_meta=request_meta,
        )
        raise _public_failure(failure) from failure
    except AppError:
        raise
    except Exception as exc:
        logger.exception("Unexpected ingestion failure document_id=%s", document_id)
        failure = _ProcessingFailure(
            ERROR_INGESTION_INTERNAL,
            "Document ingestion failed",
            500,
        )
        _record_failure(
            factory,
            context=context,
            document_id=document_id,
            job_id=claim.job_id,
            failure=failure,
            pages_processed=pages_seen,
            request_meta=request_meta,
        )
        raise _public_failure(failure) from exc


def latest_ingestion_job(
    db: Session,
    *,
    tenant_id: UUID,
    document_id: UUID,
) -> DocumentIngestionJob | None:
    return db.scalar(
        select(DocumentIngestionJob)
        .where(
            DocumentIngestionJob.tenant_id == tenant_id,
            DocumentIngestionJob.document_id == document_id,
        )
        .order_by(
            DocumentIngestionJob.created_at.desc(),
            DocumentIngestionJob.job_id.desc(),
        )
        .limit(1)
    )


def list_document_chunks(
    db: Session,
    *,
    tenant_id: UUID,
    document_id: UUID,
) -> list[DocumentChunk]:
    return list(
        db.scalars(
            select(DocumentChunk)
            .where(
                DocumentChunk.tenant_id == tenant_id,
                DocumentChunk.document_id == document_id,
            )
            .order_by(DocumentChunk.chunk_index)
        ).all()
    )
