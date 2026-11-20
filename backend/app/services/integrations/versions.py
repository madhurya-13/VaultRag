"""Immutable document versions.

Every ingested revision keeps its own stored bytes and checksum. Chunks and
embeddings stay attached to the live ``documents`` row, so no retrieval or
authorization query needs a version predicate. A rollback re-ingests the stored
bytes of the selected version through the existing pipeline.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.database import set_tenant_context
from app.core.exceptions import BadRequestError, NotFoundError
from app.core.request_context import RequestMeta
from app.core.security_constants import (
    DOC_STATUS_UPLOADED,
    DOCUMENT_VERSION_STATUS_COMPLETED,
    DOCUMENT_VERSION_STATUS_FAILED,
    DOCUMENT_VERSION_STATUS_PENDING,
    POLICY_INTEGRATION_SYNC,
)
from app.dependencies.authentication import AuthenticatedContext
from app.models import Document, DocumentChunk, DocumentVersion
from app.services.ingestion_service import process_document
from app.services.security_audit import log_security_event


def next_version_number(db: Session, *, tenant_id: UUID, document_id: UUID) -> int:
    current = db.scalar(
        select(func.max(DocumentVersion.version_number)).where(
            DocumentVersion.tenant_id == tenant_id,
            DocumentVersion.document_id == document_id,
        )
    )
    return int(current or 0) + 1


def record_version(
    db: Session,
    *,
    tenant_id: UUID,
    document_id: UUID,
    content_checksum: str,
    file_size: int,
    storage_path: str,
    external_version_id: str | None,
    source_modified_at: datetime | None,
    created_by: UUID,
    ingestion_status: str = DOCUMENT_VERSION_STATUS_PENDING,
) -> DocumentVersion:
    version = DocumentVersion(
        tenant_id=tenant_id,
        document_id=document_id,
        version_number=next_version_number(
            db, tenant_id=tenant_id, document_id=document_id
        ),
        external_version_id=external_version_id,
        content_checksum=content_checksum,
        file_size=file_size,
        storage_path=storage_path,
        source_modified_at=source_modified_at,
        ingestion_status=ingestion_status,
        is_current=False,
        created_by=created_by,
    )
    db.add(version)
    db.flush()
    return version


def mark_version_result(
    db: Session,
    *,
    version: DocumentVersion,
    succeeded: bool,
) -> None:
    if not succeeded:
        version.ingestion_status = DOCUMENT_VERSION_STATUS_FAILED
        version.is_current = False
        return
    db.execute(
        DocumentVersion.__table__.update()
        .where(
            DocumentVersion.tenant_id == version.tenant_id,
            DocumentVersion.document_id == version.document_id,
            DocumentVersion.document_version_id != version.document_version_id,
            DocumentVersion.is_current.is_(True),
        )
        .values(is_current=False)
    )
    version.ingestion_status = DOCUMENT_VERSION_STATUS_COMPLETED
    db.flush()
    version.is_current = True


def list_versions(
    db: Session, *, tenant_id: UUID, document_id: UUID
) -> list[DocumentVersion]:
    return list(
        db.scalars(
            select(DocumentVersion)
            .where(
                DocumentVersion.tenant_id == tenant_id,
                DocumentVersion.document_id == document_id,
            )
            .order_by(DocumentVersion.version_number.desc())
        ).all()
    )


def restore_version(
    db: Session,
    context: AuthenticatedContext,
    *,
    document_id: UUID,
    document_version_id: UUID,
    request_meta: RequestMeta | None = None,
) -> Document:
    """Make an earlier stored revision live again by re-ingesting it.

    The caller must hold ``MANAGE_PERMISSIONS`` on the document (enforced by the
    route). Live chunks are replaced inside the existing ingestion pipeline, so
    a failed rollback leaves an explicit FAILED state rather than a mixed index.
    """
    document = db.scalar(
        select(Document).where(
            Document.tenant_id == context.tenant_id,
            Document.document_id == document_id,
        )
    )
    if document is None:
        raise NotFoundError("Document not found")
    version = db.scalar(
        select(DocumentVersion).where(
            DocumentVersion.tenant_id == context.tenant_id,
            DocumentVersion.document_id == document_id,
            DocumentVersion.document_version_id == document_version_id,
        )
    )
    if version is None:
        raise NotFoundError("Document version not found")
    if version.ingestion_status != DOCUMENT_VERSION_STATUS_COMPLETED:
        raise BadRequestError("Only a successfully ingested version can be restored")

    # Replace the live revision. The version's own stored file is never moved,
    # so history remains reproducible after the rollback.
    db.execute(
        delete(DocumentChunk).where(
            DocumentChunk.tenant_id == context.tenant_id,
            DocumentChunk.document_id == document_id,
        )
    )
    document.storage_path = version.storage_path
    document.file_hash = version.content_checksum
    document.file_size = version.file_size
    document.processing_status = DOC_STATUS_UPLOADED
    document.total_pages = 0
    document.total_chunks = 0
    document.embedding_model = None
    document.is_active = True
    document.updated_at = datetime.now(timezone.utc)
    # Deactivate every revision first, in one statement, and only then mark the
    # restored one current. Assigning both flags in one flush can trip the
    # partial unique index that permits exactly one current version.
    db.execute(
        DocumentVersion.__table__.update()
        .where(
            DocumentVersion.tenant_id == context.tenant_id,
            DocumentVersion.document_id == document_id,
        )
        .values(is_current=False)
    )
    version.is_current = True
    db.flush()

    log_security_event(
        db,
        tenant_id=context.tenant_id,
        user_id=context.user.user_id,
        resource_type="DOCUMENT_VERSION",
        resource_id=document_id,
        action="DOCUMENT_VERSION_RESTORE",
        decision="ALLOW",
        reason=f"version_number={version.version_number}",
        policy=POLICY_INTEGRATION_SYNC,
        request_meta=request_meta,
    )
    db.commit()
    # Committing clears the transaction-local tenant; restore it before any
    # follow-up read in this request.
    set_tenant_context(db, context.tenant_id)

    try:
        process_document(
            context,
            document_id,
            request_meta=request_meta or RequestMeta(request_id=UUID(int=0), ip_address=None),
        )
    except Exception:
        # The ingestion pipeline records FAILED on the document and its own job;
        # the version row keeps its COMPLETED status because its bytes are fine.
        raise
    refreshed = db.scalar(
        select(Document).where(
            Document.tenant_id == context.tenant_id,
            Document.document_id == document_id,
        )
    )
    if refreshed is None:  # pragma: no cover - defensive
        raise NotFoundError("Document not found")
    return refreshed
