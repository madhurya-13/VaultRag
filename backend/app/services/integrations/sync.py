"""Integration synchronization engine.

One run is one ``integration_sync_jobs`` row. The engine:

1. claims the job and marks it ``DISCOVERING``;
2. enumerates selected repositories with a full listing or the provider's
   incremental cursor;
3. downloads each ingestible file to a private temporary file, computes the
   digest itself, and skips content that has not changed;
4. creates or replaces the live VaultRAG document, records an immutable
   ``document_versions`` row and re-runs the normal ingestion pipeline;
5. mirrors the external ACL only when the integration is in
   ``EXTERNAL_MIRROR`` mode;
6. advances the repository cursor and records real counters.

Nothing here widens document access: the document is created through the same
``documents`` row, the same ingestion pipeline and the same authorization
engine as an ordinary upload.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Callable
from uuid import UUID, uuid4

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import SessionLocal, set_tenant_context
from app.core.request_context import RequestMeta
from app.core.security_constants import (
    DOC_STATUS_ARCHIVED,
    DOC_STATUS_COMPLETED,
    DOC_STATUS_UPLOADED,
    ERROR_INTEGRATION_PROVIDER_REQUEST_FAILED,
    EXTERNAL_SYNC_STATUS_DELETED,
    EXTERNAL_SYNC_STATUS_FAILED,
    EXTERNAL_SYNC_STATUS_SYNCED,
    INTEGRATION_STATUS_REAUTH_REQUIRED,
    PERMISSION_SYNC_MODE_MIRROR,
    POLICY_INTEGRATION_SYNC,
    SYNC_STATUS_CANCELLED,
    SYNC_STATUS_COMPLETED,
    SYNC_STATUS_DISCOVERING,
    SYNC_STATUS_FAILED,
    SYNC_STATUS_PARTIALLY_COMPLETED,
    SYNC_STATUS_PENDING,
    SYNC_STATUS_PROCESSING,
    SYNC_STATUS_DOWNLOADING,
    SYNC_TYPE_MANUAL,
)
from app.integrations.base import (
    Capability,
    ChangeKind,
    NormalizedDocument,
    NormalizedPermission,
)
from app.integrations.credentials import load_credentials
from app.integrations.exceptions import (
    IntegrationError,
    IntegrationValidationError,
    ProviderAuthenticationError,
    SyncAlreadyRunningError,
)
from app.integrations.models import (
    DocumentVersion,
    ExternalDocument,
    Integration,
    IntegrationRepository,
    IntegrationSyncEvent,
    IntegrationSyncJob,
)
from app.integrations.registry import create_connector
from app.models import Document, DocumentChunk
from app.services.abac import validate_document_attributes
from app.services.document_upload import assign_initial_permissions
from app.services.ingestion_service import process_document
from app.services.integrations.permissions import mirror_document_permissions
from app.services.integrations.versions import (
    mark_version_result,
    next_version_number,
    record_version,
)
from app.services.security_audit import log_security_event
from app.services.storage import PrivateStorage, get_private_storage
from app.integrations import service as integration_service

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], Session]
MAX_FILES_PER_RUN = 2000
MAX_PAGES_PER_REPOSITORY = 200


@dataclass(frozen=True, slots=True)
class IntegrationSnapshot:
    integration_id: UUID
    tenant_id: UUID
    provider: str
    permission_sync_mode: str
    configuration: dict[str, Any]
    credentials: dict[str, Any]
    sync_interval_minutes: int
    created_by: UUID
    department: str
    classification: str


@dataclass(slots=True)
class SyncSummary:
    sync_job_id: UUID
    sync_status: str
    files_discovered: int = 0
    files_processed: int = 0
    files_failed: int = 0
    versions_created: int = 0
    documents_deleted: int = 0
    error_code: str | None = None
    error_summary: str | None = None
    failures: list[str] = field(default_factory=list)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _factory(session_factory: SessionFactory | None) -> SessionFactory:
    return session_factory or SessionLocal


def _request_meta() -> RequestMeta:
    return RequestMeta(request_id=uuid4(), ip_address=None)


def _actor(tenant_id: UUID, user_id: UUID):
    """A minimal authenticated context for pipeline calls.

    The integration was created by a tenant administrator and the sync runs with
    that identity. Ingestion only uses the tenant and user id; it makes no
    document authorization decision of its own.
    """
    return SimpleNamespace(
        tenant_id=tenant_id,
        user=SimpleNamespace(user_id=user_id, tenant_id=tenant_id),
        roles=("ADMIN",),
    )


def _load_snapshot(
    factory: SessionFactory,
    *,
    tenant_id: UUID,
    integration_id: UUID,
) -> IntegrationSnapshot:
    with factory() as session:
        set_tenant_context(session, tenant_id)
        integration = session.scalar(
            select(Integration).where(
                Integration.tenant_id == tenant_id,
                Integration.integration_id == integration_id,
            )
        )
        if integration is None:
            raise IntegrationValidationError("Integration not found")
        credentials = load_credentials(session, integration=integration)
        configuration = dict(integration.configuration or {})
        return IntegrationSnapshot(
            integration_id=integration.integration_id,
            tenant_id=integration.tenant_id,
            provider=integration.provider,
            permission_sync_mode=integration.permission_sync_mode,
            configuration=configuration,
            credentials=credentials,
            sync_interval_minutes=integration.sync_interval_minutes,
            created_by=integration.created_by,
            department=str(configuration.get("department", "ENGINEERING")).upper(),
            classification=str(configuration.get("classification", "INTERNAL")).upper(),
        )


def _selected_repositories(
    factory: SessionFactory, *, tenant_id: UUID, integration_id: UUID
) -> list[IntegrationRepository]:
    with factory() as session:
        set_tenant_context(session, tenant_id)
        rows = session.scalars(
            select(IntegrationRepository)
            .where(
                IntegrationRepository.tenant_id == tenant_id,
                IntegrationRepository.integration_id == integration_id,
                IntegrationRepository.is_selected.is_(True),
            )
            .order_by(IntegrationRepository.name)
        ).all()
        for row in rows:
            session.expunge(row)
        return list(rows)


def _update_job(
    factory: SessionFactory,
    *,
    tenant_id: UUID,
    sync_job_id: UUID,
    values: dict[str, Any],
) -> None:
    with factory() as session:
        with session.begin():
            set_tenant_context(session, tenant_id)
            job = session.scalar(
                select(IntegrationSyncJob).where(
                    IntegrationSyncJob.tenant_id == tenant_id,
                    IntegrationSyncJob.sync_job_id == sync_job_id,
                )
            )
            if job is not None:
                for key, value in values.items():
                    setattr(job, key, value)


def enqueue_sync_job(
    db: Session,
    *,
    integration: Integration,
    actor_user_id: UUID,
    sync_type: str = SYNC_TYPE_MANUAL,
    request_meta: RequestMeta | None = None,
) -> IntegrationSyncJob:
    """Create the single active job for an integration, or fail loudly."""
    integration_service.ensure_enabled()
    settings = get_settings()
    job = IntegrationSyncJob(
        tenant_id=integration.tenant_id,
        integration_id=integration.integration_id,
        sync_type=sync_type,
        sync_status=SYNC_STATUS_PENDING,
        max_attempts=settings.integration_sync_max_attempts,
        created_by=actor_user_id,
    )
    db.add(job)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise SyncAlreadyRunningError(
            "A synchronization is already running for this integration"
        ) from exc
    integration_service.audit(
        db,
        tenant_id=integration.tenant_id,
        user_id=actor_user_id,
        integration_id=integration.integration_id,
        action="INTEGRATION_SYNC_QUEUED",
        reason=f"sync_type={sync_type}",
        request_meta=request_meta,
    )
    return job


def _storage_key(tenant_id: UUID, document_id: UUID, version_number: int) -> str:
    return f"{tenant_id}/{document_id.hex}-v{version_number}.pdf"


def _prepare_document(
    factory: SessionFactory,
    *,
    snapshot: IntegrationSnapshot,
    metadata: NormalizedDocument,
    permissions: tuple[NormalizedPermission, ...],
    downloaded: Any,
    storage: PrivateStorage,
) -> tuple[UUID, UUID | None, str]:
    """Create or replace the live document and record the version.

    Returns ``(document_id, version_id, outcome)`` where ``outcome`` is
    ``created``, ``updated``, ``duplicate`` or ``unchanged``.
    """
    actor_id = snapshot.created_by
    with factory() as session:
        with session.begin():
            set_tenant_context(session, snapshot.tenant_id)
            external: ExternalDocument | None = session.scalar(
                select(ExternalDocument).where(
                    ExternalDocument.tenant_id == snapshot.tenant_id,
                    ExternalDocument.integration_id == snapshot.integration_id,
                    ExternalDocument.external_file_id == metadata.external_file_id,
                )
            )
            document: Document | None = None
            if external is not None:
                document = session.scalar(
                    select(Document).where(
                        Document.tenant_id == snapshot.tenant_id,
                        Document.document_id == external.internal_document_id,
                    )
                )
            if (
                external is not None
                and document is not None
                and external.content_checksum == downloaded.sha256
                and document.processing_status == DOC_STATUS_COMPLETED
                and document.is_active
            ):
                external.last_seen_at = _utcnow()
                external.sync_status = EXTERNAL_SYNC_STATUS_SYNCED
                external.external_version_id = metadata.external_version_id
                return document.document_id, None, "unchanged"

            department, classification, clearance = validate_document_attributes(
                department=snapshot.department,
                classification=snapshot.classification,
                required_clearance_level=None,
            )
            if document is None:
                document = Document(
                    tenant_id=snapshot.tenant_id,
                    document_name=metadata.file_name[:255],
                    document_type="INTEGRATION",
                    department=department,
                    classification=classification,
                    required_clearance_level=clearance,
                    uploaded_by=actor_id,
                    is_active=True,
                    processing_status=DOC_STATUS_UPLOADED,
                )
                session.add(document)
                session.flush()
                assign_initial_permissions(
                    session,
                    tenant_id=snapshot.tenant_id,
                    document_id=document.document_id,
                    actor_user_id=actor_id,
                    department=department,
                    request_meta=_request_meta(),
                )
                outcome = "created"
            else:
                # Replacing the live revision: old chunks must go before the
                # ingestion claim, and the old bytes stay referenced by the
                # previous document_versions row.
                session.execute(
                    delete(DocumentChunk).where(
                        DocumentChunk.tenant_id == snapshot.tenant_id,
                        DocumentChunk.document_id == document.document_id,
                    )
                )
                document.document_name = metadata.file_name[:255]
                document.department = department
                document.classification = classification
                document.required_clearance_level = clearance
                document.processing_status = DOC_STATUS_UPLOADED
                document.total_pages = 0
                document.total_chunks = 0
                document.embedding_model = None
                document.is_active = True
                outcome = "updated"

            version_number = next_version_number(
                session,
                tenant_id=snapshot.tenant_id,
                document_id=document.document_id,
            )
            key = _storage_key(
                snapshot.tenant_id, document.document_id, version_number
            )
            temporary = storage.create_temp_path()
            shutil.copyfile(downloaded.temp_path, temporary)
            storage.finalize(temporary, key)

            document.storage_path = key
            document.file_hash = downloaded.sha256
            document.file_size = downloaded.size
            document.updated_at = _utcnow()

            version = record_version(
                session,
                tenant_id=snapshot.tenant_id,
                document_id=document.document_id,
                content_checksum=downloaded.sha256,
                file_size=downloaded.size,
                storage_path=key,
                external_version_id=metadata.external_version_id,
                source_modified_at=metadata.modified_at,
                created_by=actor_id,
            )

            if external is None:
                external = ExternalDocument(
                    tenant_id=snapshot.tenant_id,
                    integration_id=snapshot.integration_id,
                    internal_document_id=document.document_id,
                    provider=snapshot.provider,
                    external_file_id=metadata.external_file_id,
                    external_repository_id=metadata.external_repository_id,
                    external_version_id=metadata.external_version_id,
                    source_url=metadata.source_url,
                    source_path=metadata.source_path,
                    source_metadata=dict(metadata.metadata or {}),
                    source_permissions=[
                        {
                            "principal_id": item.principal_id,
                            "principal_type": item.principal_type.value,
                            "role": item.role.value,
                            "inherited": item.inherited,
                        }
                        for item in permissions
                    ],
                    content_checksum=downloaded.sha256,
                    file_size=downloaded.size,
                    last_modified_at=metadata.modified_at,
                    last_seen_at=_utcnow(),
                    sync_status=EXTERNAL_SYNC_STATUS_SYNCED,
                )
                session.add(external)
                session.flush()
            external.external_version_id = metadata.external_version_id
            external.source_metadata = dict(metadata.metadata or {})
            external.source_permissions = [
                {
                    "principal_id": item.principal_id,
                    "principal_type": item.principal_type.value,
                    "role": item.role.value,
                    "inherited": item.inherited,
                }
                for item in permissions
            ]
            external.content_checksum = downloaded.sha256
            external.file_size = downloaded.size
            external.last_modified_at = metadata.modified_at
            external.last_seen_at = _utcnow()
            external.sync_status = EXTERNAL_SYNC_STATUS_SYNCED
            external.deleted_at = None
            session.flush()

            integration = session.scalar(
                select(Integration).where(
                    Integration.tenant_id == snapshot.tenant_id,
                    Integration.integration_id == snapshot.integration_id,
                )
            )
            if integration is not None:
                mirror_document_permissions(
                    session,
                    integration=integration,
                    document=document,
                    external_document=external,
                    permissions=permissions,
                    actor_user_id=actor_id,
                    request_meta=_request_meta(),
                )
            session.flush()
            return document.document_id, version.document_version_id, outcome


def _finish_document(
    factory: SessionFactory,
    *,
    snapshot: IntegrationSnapshot,
    document_id: UUID,
    version_id: UUID | None,
    succeeded: bool,
    error_code: str | None = None,
) -> None:
    with factory() as session:
        with session.begin():
            set_tenant_context(session, snapshot.tenant_id)
            if version_id is not None:
                version = session.scalar(
                    select(DocumentVersion).where(
                        DocumentVersion.tenant_id == snapshot.tenant_id,
                        DocumentVersion.document_version_id == version_id,
                    )
                )
                if version is not None:
                    mark_version_result(session, version=version, succeeded=succeeded)
            external = session.scalar(
                select(ExternalDocument).where(
                    ExternalDocument.tenant_id == snapshot.tenant_id,
                    ExternalDocument.integration_id == snapshot.integration_id,
                    ExternalDocument.internal_document_id == document_id,
                )
            )
            if external is not None and not succeeded:
                external.sync_status = EXTERNAL_SYNC_STATUS_FAILED
            session.flush()


def _mark_deleted(
    factory: SessionFactory,
    *,
    snapshot: IntegrationSnapshot,
    external_file_id: str,
) -> bool:
    with factory() as session:
        with session.begin():
            set_tenant_context(session, snapshot.tenant_id)
            external = session.scalar(
                select(ExternalDocument).where(
                    ExternalDocument.tenant_id == snapshot.tenant_id,
                    ExternalDocument.integration_id == snapshot.integration_id,
                    ExternalDocument.external_file_id == external_file_id,
                )
            )
            if external is None:
                return False
            external.sync_status = EXTERNAL_SYNC_STATUS_DELETED
            external.deleted_at = _utcnow()
            document = session.scalar(
                select(Document).where(
                    Document.tenant_id == snapshot.tenant_id,
                    Document.document_id == external.internal_document_id,
                )
            )
            if document is not None:
                document.is_active = False
                document.processing_status = DOC_STATUS_ARCHIVED
            return True


def _bump_job(
    factory: SessionFactory,
    *,
    tenant_id: UUID,
    sync_job_id: UUID,
    processed: int = 0,
    failed: int = 0,
    versions: int = 0,
    deleted: int = 0,
    status: str | None = None,
) -> None:
    with factory() as session:
        with session.begin():
            set_tenant_context(session, tenant_id)
            job = session.scalar(
                select(IntegrationSyncJob).where(
                    IntegrationSyncJob.tenant_id == tenant_id,
                    IntegrationSyncJob.sync_job_id == sync_job_id,
                )
            )
            if job is None:
                return
            job.files_processed += processed
            job.files_failed += failed
            job.versions_created += versions
            job.documents_deleted += deleted
            if status is not None:
                job.sync_status = status


async def _discover_full(
    connector: Any, repository_id: str
) -> list[NormalizedDocument]:
    documents: list[NormalizedDocument] = []
    cursor: str | None = None
    for _ in range(MAX_PAGES_PER_REPOSITORY):
        page = await connector.list_files(
            repository_id, cursor=cursor, page_size=100
        )
        documents.extend(page.items)
        if len(documents) >= MAX_FILES_PER_RUN:
            break
        if not page.has_more or not page.next_cursor:
            break
        cursor = page.next_cursor
    return documents[:MAX_FILES_PER_RUN]


async def _discover_incremental(
    connector: Any,
    repository_id: str,
    cursor: str | None,
) -> tuple[list[tuple[bool, NormalizedDocument]], str | None]:
    """Return ``(is_deleted, metadata)`` items and the next cursor."""
    items: list[tuple[bool, NormalizedDocument]] = []
    page = await connector.detect_changes(repository_id, cursor=cursor)
    next_cursor = page.next_cursor or cursor
    for change in page.changes:
        if change.kind is ChangeKind.DELETED or change.deleted:
            items.append(
                (
                    True,
                    NormalizedDocument(
                        external_file_id=change.external_file_id,
                        external_repository_id=change.external_repository_id,
                        file_name=change.file_name or "deleted",
                    ),
                )
            )
            continue
        try:
            metadata = await connector.get_metadata(change.external_file_id)
        except IntegrationError:
            continue
        items.append((False, metadata))
        if len(items) >= MAX_FILES_PER_RUN:
            break
    return items, next_cursor


async def _execute_sync(
    *,
    snapshot: IntegrationSnapshot,
    repositories: list[IntegrationRepository],
    sync_job_id: UUID,
    sync_type: str,
    factory: SessionFactory,
    transport: Any | None = None,
) -> SyncSummary:
    settings = get_settings()
    summary = SyncSummary(
        sync_job_id=sync_job_id, sync_status=SYNC_STATUS_DISCOVERING
    )
    storage = get_private_storage()
    connector = create_connector(snapshot.provider, transport=transport)
    try:
        await connector.authenticate(
            {**snapshot.configuration, **snapshot.credentials}
        )
        state = await connector.validate_connection()
        if not state.ok:
            raise ProviderAuthenticationError(
                state.detail or "The provider rejected the stored credential"
            )

        _update_job(
            factory,
            tenant_id=snapshot.tenant_id,
            sync_job_id=sync_job_id,
            values={
                "sync_status": SYNC_STATUS_DISCOVERING,
                "started_at": _utcnow(),
                "attempt_count": _attempt_count(
                    factory,
                    tenant_id=snapshot.tenant_id,
                    sync_job_id=sync_job_id,
                ),
            },
        )

        for repository in repositories:
            incremental = (
                repository.sync_mode == "INCREMENTAL"
                and repository.cursor is not None
                and sync_type != "FULL"
            )
            if incremental:
                changes, next_cursor = await _discover_incremental(
                    connector,
                    repository.external_repository_id,
                    repository.cursor,
                )
            else:
                changes = [
                    (False, item)
                    for item in await _discover_full(
                        connector, repository.external_repository_id
                    )
                ]
                next_cursor = repository.cursor
            metadata_items = [item for _, item in changes]
            summary.files_discovered += len(metadata_items)
            _bump_job(
                factory,
                tenant_id=snapshot.tenant_id,
                sync_job_id=sync_job_id,
                processed=0,
            )
            with factory() as session:
                with session.begin():
                    set_tenant_context(session, snapshot.tenant_id)
                    job = session.scalar(
                        select(IntegrationSyncJob).where(
                            IntegrationSyncJob.tenant_id == snapshot.tenant_id,
                            IntegrationSyncJob.sync_job_id == sync_job_id,
                        )
                    )
                    if job is not None:
                        job.files_discovered = summary.files_discovered
                        job.sync_status = SYNC_STATUS_DOWNLOADING

            for is_deleted, metadata in changes:
                if is_deleted:
                    if _mark_deleted(
                        factory,
                        snapshot=snapshot,
                        external_file_id=metadata.external_file_id,
                    ):
                        summary.documents_deleted += 1
                        _bump_job(
                            factory,
                            tenant_id=snapshot.tenant_id,
                            sync_job_id=sync_job_id,
                            deleted=1,
                            status=SYNC_STATUS_DOWNLOADING,
                        )
                    continue

                if metadata.ingestible_mime_type is None:
                    summary.files_failed += 1
                    summary.failures.append(
                        f"{metadata.external_file_id}:unsupported_type"
                    )
                    _bump_job(
                        factory,
                        tenant_id=snapshot.tenant_id,
                        sync_job_id=sync_job_id,
                        failed=1,
                    )
                    continue

                downloaded = None
                try:
                    downloaded = await connector.download(
                        metadata.external_file_id,
                        max_bytes=settings.integration_max_download_bytes,
                        expected_version_id=metadata.external_version_id,
                    )
                    permissions = ()
                    if snapshot.permission_sync_mode == PERMISSION_SYNC_MODE_MIRROR:
                        if connector.supports(Capability.GET_PERMISSIONS):
                            permissions = tuple(
                                await connector.get_permissions(
                                    metadata.external_file_id
                                )
                            )
                    document_id, version_id, outcome = _prepare_document(
                        factory,
                        snapshot=snapshot,
                        metadata=metadata,
                        permissions=permissions,
                        downloaded=downloaded,
                        storage=storage,
                    )
                    if outcome in {"created", "updated"}:
                        document = _load_document(
                            factory,
                            tenant_id=snapshot.tenant_id,
                            document_id=document_id,
                        )
                        job_status = SYNC_STATUS_PROCESSING
                        _bump_job(
                            factory,
                            tenant_id=snapshot.tenant_id,
                            sync_job_id=sync_job_id,
                            status=job_status,
                        )
                        process_document(
                            _actor(snapshot.tenant_id, snapshot.created_by),
                            document.document_id,
                            request_meta=_request_meta(),
                        )
                        _finish_document(
                            factory,
                            snapshot=snapshot,
                            document_id=document_id,
                            version_id=version_id,
                            succeeded=True,
                        )
                        summary.files_processed += 1
                        summary.versions_created += 1
                        _bump_job(
                            factory,
                            tenant_id=snapshot.tenant_id,
                            sync_job_id=sync_job_id,
                            processed=1,
                            versions=1,
                        )
                    else:
                        summary.files_processed += 1
                        _bump_job(
                            factory,
                            tenant_id=snapshot.tenant_id,
                            sync_job_id=sync_job_id,
                            processed=1,
                        )
                except ProviderAuthenticationError:
                    raise
                except Exception as exc:
                    logger.warning(
                        "Integration file sync failed integration_id=%s code=%s",
                        snapshot.integration_id,
                        getattr(exc, "error_code", "INTEGRATION_SYNC_FILE_FAILED"),
                    )
                    summary.files_failed += 1
                    summary.failures.append(
                        metadata.external_file_id[:120]
                    )
                    _bump_job(
                        factory,
                        tenant_id=snapshot.tenant_id,
                        sync_job_id=sync_job_id,
                        failed=1,
                    )
                    try:
                        with factory() as session:
                            with session.begin():
                                set_tenant_context(session, snapshot.tenant_id)
                                external = session.scalar(
                                    select(ExternalDocument).where(
                                        ExternalDocument.tenant_id
                                        == snapshot.tenant_id,
                                        ExternalDocument.integration_id
                                        == snapshot.integration_id,
                                        ExternalDocument.external_file_id
                                        == metadata.external_file_id,
                                    )
                                )
                                if external is not None:
                                    external.sync_status = (
                                        EXTERNAL_SYNC_STATUS_FAILED
                                    )
                    except Exception:  # pragma: no cover - best effort
                        logger.exception("Failed to record integration failure")
                finally:
                    if downloaded is not None:
                        downloaded.cleanup()

            with factory() as session:
                with session.begin():
                    set_tenant_context(session, snapshot.tenant_id)
                    row = session.scalar(
                        select(IntegrationRepository).where(
                            IntegrationRepository.tenant_id
                            == snapshot.tenant_id,
                            IntegrationRepository.repository_id
                            == repository.repository_id,
                        )
                    )
                    if row is not None:
                        row.cursor = next_cursor
                        row.cursor_updated_at = _utcnow()
                        row.last_synced_at = _utcnow()

        if summary.files_failed and summary.files_processed:
            summary.sync_status = SYNC_STATUS_PARTIALLY_COMPLETED
        elif summary.files_failed and not summary.files_processed:
            summary.sync_status = SYNC_STATUS_FAILED
        else:
            summary.sync_status = SYNC_STATUS_COMPLETED
        return summary
    finally:
        try:
            await connector.disconnect()
        except Exception:  # pragma: no cover - best effort
            logger.debug("Connector disconnect failed", exc_info=True)


def _attempt_count(
    factory: SessionFactory, *, tenant_id: UUID, sync_job_id: UUID
) -> int:
    with factory() as session:
        set_tenant_context(session, tenant_id)
        value = session.scalar(
            select(IntegrationSyncJob.attempt_count).where(
                IntegrationSyncJob.tenant_id == tenant_id,
                IntegrationSyncJob.sync_job_id == sync_job_id,
            )
        )
        return int(value or 0) + 1


def _load_document(
    factory: SessionFactory, *, tenant_id: UUID, document_id: UUID
) -> Document:
    with factory() as session:
        set_tenant_context(session, tenant_id)
        document = session.scalar(
            select(Document).where(
                Document.tenant_id == tenant_id,
                Document.document_id == document_id,
            )
        )
        if document is None:
            raise IntegrationValidationError("Document disappeared during sync")
        session.expunge(document)
        return document


def run_sync_job(
    *,
    tenant_id: UUID,
    sync_job_id: UUID,
    session_factory: SessionFactory | None = None,
    transport: Any | None = None,
) -> SyncSummary:
    """Run one queued job to completion. Safe to call from the API or worker."""
    factory = _factory(session_factory)
    with factory() as session:
        set_tenant_context(session, tenant_id)
        job = session.scalar(
            select(IntegrationSyncJob).where(
                IntegrationSyncJob.tenant_id == tenant_id,
                IntegrationSyncJob.sync_job_id == sync_job_id,
            )
        )
        if job is None:
            raise IntegrationValidationError("Synchronization job not found")
        if job.cancel_requested or job.sync_status == SYNC_STATUS_CANCELLED:
            return SyncSummary(
                sync_job_id=sync_job_id, sync_status=SYNC_STATUS_CANCELLED
            )
        integration_id = job.integration_id
        sync_type = job.sync_type

    snapshot = _load_snapshot(
        factory, tenant_id=tenant_id, integration_id=integration_id
    )
    repositories = _selected_repositories(
        factory, tenant_id=tenant_id, integration_id=integration_id
    )
    summary = SyncSummary(
        sync_job_id=sync_job_id, sync_status=SYNC_STATUS_DISCOVERING
    )

    if not repositories:
        summary = SyncSummary(
            sync_job_id=sync_job_id,
            sync_status=SYNC_STATUS_FAILED,
            error_code="INTEGRATION_NO_REPOSITORIES",
            error_summary="No repository has been selected for synchronization",
        )
        _finish_job(
            factory,
            tenant_id=tenant_id,
            integration_id=integration_id,
            summary=summary,
            sync_interval_minutes=snapshot.sync_interval_minutes,
        )
        return summary

    try:
        summary = asyncio.run(
            _execute_sync(
                snapshot=snapshot,
                repositories=repositories,
                sync_job_id=sync_job_id,
                sync_type=sync_type,
                factory=factory,
                transport=transport,
            )
        )
    except ProviderAuthenticationError as exc:
        summary = SyncSummary(
            sync_job_id=sync_job_id,
            sync_status=SYNC_STATUS_FAILED,
            error_code=getattr(exc, "error_code", "INTEGRATION_PROVIDER_AUTH_FAILED"),
            error_summary="The provider rejected the stored credential",
        )
        with factory() as session:
            with session.begin():
                set_tenant_context(session, tenant_id)
                integration = session.scalar(
                    select(Integration).where(
                        Integration.tenant_id == tenant_id,
                        Integration.integration_id == integration_id,
                    )
                )
                if integration is not None:
                    integration.connection_status = INTEGRATION_STATUS_REAUTH_REQUIRED
    except Exception as exc:
        logger.exception("Integration sync failed integration_id=%s", integration_id)
        summary = SyncSummary(
            sync_job_id=sync_job_id,
            sync_status=SYNC_STATUS_FAILED,
            error_code=getattr(exc, "error_code", ERROR_INTEGRATION_PROVIDER_REQUEST_FAILED),
            error_summary="Synchronization failed safely",
        )
    _finish_job(
        factory,
        tenant_id=tenant_id,
        integration_id=integration_id,
        summary=summary,
        sync_interval_minutes=snapshot.sync_interval_minutes,
    )
    return summary


def _finish_job(
    factory: SessionFactory,
    *,
    tenant_id: UUID,
    integration_id: UUID,
    summary: SyncSummary,
    sync_interval_minutes: int,
) -> None:
    now = _utcnow()
    with factory() as session:
        with session.begin():
            set_tenant_context(session, tenant_id)
            job = session.scalar(
                select(IntegrationSyncJob).where(
                    IntegrationSyncJob.tenant_id == tenant_id,
                    IntegrationSyncJob.sync_job_id == summary.sync_job_id,
                )
            )
            if job is not None:
                job.sync_status = summary.sync_status
                job.files_discovered = summary.files_discovered
                job.files_processed = summary.files_processed
                job.files_failed = summary.files_failed
                job.versions_created = summary.versions_created
                job.documents_deleted = summary.documents_deleted
                job.error_code = summary.error_code
                job.error_summary = summary.error_summary
                job.completed_at = now
                if summary.sync_status == SYNC_STATUS_FAILED:
                    job.next_attempt_at = now + timedelta(
                        seconds=get_settings().integration_sync_backoff_base_seconds
                        * max(1, job.attempt_count)
                    )
            integration = session.scalar(
                select(Integration).where(
                    Integration.tenant_id == tenant_id,
                    Integration.integration_id == integration_id,
                )
            )
            if integration is not None:
                integration.last_sync_at = now
                if integration.sync_enabled:
                    integration.next_sync_at = now + timedelta(
                        minutes=sync_interval_minutes
                    )
            log_security_event(
                session,
                tenant_id=tenant_id,
                user_id=integration.created_by if integration else None,
                resource_type="INTEGRATION",
                resource_id=integration_id,
                action="INTEGRATION_SYNC_COMPLETE",
                decision="ALLOW"
                if summary.sync_status
                in {SYNC_STATUS_COMPLETED, SYNC_STATUS_PARTIALLY_COMPLETED}
                else "DENY",
                reason=(
                    f"status={summary.sync_status};processed={summary.files_processed};"
                    f"failed={summary.files_failed};deleted={summary.documents_deleted}"
                ),
                policy=POLICY_INTEGRATION_SYNC,
            )


def claim_due_jobs(
    factory: SessionFactory, *, tenant_id: UUID, limit: int = 5
) -> list[UUID]:
    """Return queued jobs whose integration is due, oldest first."""
    with factory() as session:
        set_tenant_context(session, tenant_id)
        rows = session.scalars(
            select(IntegrationSyncJob)
            .where(
                IntegrationSyncJob.tenant_id == tenant_id,
                IntegrationSyncJob.sync_status == SYNC_STATUS_PENDING,
            )
            .order_by(IntegrationSyncJob.created_at)
            .limit(limit)
        ).all()
        return [row.sync_job_id for row in rows]


def schedule_due_integrations(
    factory: SessionFactory, *, tenant_id: UUID
) -> list[UUID]:
    """Create PENDING jobs for enabled integrations whose next_sync_at is due."""
    created: list[UUID] = []
    now = _utcnow()
    with factory() as session:
        set_tenant_context(session, tenant_id)
        due = [
            (row.integration_id, row.created_by)
            for row in session.scalars(
                select(Integration).where(
                    Integration.tenant_id == tenant_id,
                    Integration.sync_enabled.is_(True),
                    Integration.next_sync_at.is_not(None),
                    Integration.next_sync_at <= now,
                )
            ).all()
        ]
    for integration_id, created_by in due:
        try:
            with factory() as session:
                with session.begin():
                    set_tenant_context(session, tenant_id)
                    job = IntegrationSyncJob(
                        tenant_id=tenant_id,
                        integration_id=integration_id,
                        sync_type="INCREMENTAL",
                        sync_status=SYNC_STATUS_PENDING,
                        created_by=created_by,
                    )
                    session.add(job)
                    session.flush()
                    created.append(job.sync_job_id)
        except IntegrityError:
            # Another scheduler already owns the active job for this integration.
            continue
    return created


def record_webhook_event(
    db: Session,
    *,
    tenant_id: UUID,
    integration: Integration,
    external_event_id: str,
    event_type: str,
    payload_digest: str,
) -> IntegrationSyncEvent:
    """Persist one webhook notification, de-duplicating provider retries."""
    event = IntegrationSyncEvent(
        tenant_id=tenant_id,
        integration_id=integration.integration_id,
        external_event_id=external_event_id[:255],
        event_type=event_type[:64],
        payload_digest=payload_digest,
    )
    try:
        with db.begin_nested():
            db.add(event)
            db.flush()
    except IntegrityError as exc:
        raise SyncAlreadyRunningError("Duplicate webhook event") from exc
    return event
