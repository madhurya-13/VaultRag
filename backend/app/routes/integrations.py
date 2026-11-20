"""Enterprise integration API.

Every mutating endpoint requires ``MANAGE_INTEGRATIONS`` (ADMIN). Reads that
expose provider configuration do as well; a non-administrator cannot enumerate
or inspect a tenant's connections. Secrets are never returned: the credential
summary says only whether a credential exists and when it was updated.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Query,
    Request,
    Response,
    status,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db, set_tenant_context
from app.core.exceptions import BadRequestError, NotFoundError
from app.core.request_context import RequestMeta, get_request_meta
from app.core.security_constants import (
    CAP_MANAGE_INTEGRATIONS,
    POLICY_INTEGRATION,
    SYNC_STATUS_CANCELLED,
    SYNC_TYPE_MANUAL,
)
from app.dependencies.authentication import (
    AuthenticatedContext,
    get_current_auth,
    require_capability,
)
from app.integrations import registry
from app.integrations.credentials import credential_summary, load_credentials
from app.integrations.exceptions import SyncAlreadyRunningError, WebhookValidationError
from app.integrations.models import (
    ExternalDocument,
    Integration,
    IntegrationIdentityMapping,
    IntegrationRepository,
    IntegrationSyncJob,
)
from app.integrations.oauth import begin_oauth, complete_oauth
from app.integrations.service import (
    connect_integration,
    create_identity_mapping,
    create_integration,
    delete_identity_mapping,
    disconnect_integration,
    discover_repositories,
    ensure_enabled,
    get_integration,
    select_repository,
    set_credentials,
    update_integration,
)
from app.integrations.webhooks import verify_and_parse, verify_webhook_token
from app.schemas.integration import (
    ConnectResponse,
    CredentialRequest,
    DocumentVersionListResponse,
    DocumentVersionResponse,
    ExternalDocumentListResponse,
    ExternalDocumentResponse,
    IdentityMappingListResponse,
    IdentityMappingRequest,
    IdentityMappingResponse,
    IntegrationCreateRequest,
    IntegrationListResponse,
    IntegrationResponse,
    IntegrationUpdateRequest,
    OAuthStartResponse,
    ProviderInfoResponse,
    ProviderListResponse,
    RepositoryListResponse,
    RepositoryResponse,
    RepositorySelectRequest,
    SyncJobListResponse,
    SyncJobResponse,
    SyncRequest,
    WebhookResponse,
)
from app.services.authorization import (
    authorize_document,
    enforce_authorization,
)
from app.services.integrations import sync as sync_service
from app.services.integrations.versions import list_versions, restore_version
from app.services.security_audit import log_security_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/integrations", tags=["Integrations"])
oauth_router = APIRouter(prefix="/integrations/oauth", tags=["Integrations"])
versions_router = APIRouter(prefix="/documents", tags=["Document versions"])

admin_only = require_capability(CAP_MANAGE_INTEGRATIONS)


def _repository_response(row: IntegrationRepository) -> RepositoryResponse:
    return RepositoryResponse(
        repository_id=row.repository_id,
        external_repository_id=row.external_repository_id,
        name=row.name,
        repository_kind=row.repository_kind,
        is_selected=row.is_selected,
        sync_mode=row.sync_mode,
        last_synced_at=row.last_synced_at,
    )


def _job_response(row: IntegrationSyncJob) -> SyncJobResponse:
    return SyncJobResponse(
        sync_job_id=row.sync_job_id,
        integration_id=row.integration_id,
        sync_type=row.sync_type,
        sync_status=row.sync_status,
        files_discovered=row.files_discovered,
        files_processed=row.files_processed,
        files_failed=row.files_failed,
        versions_created=row.versions_created,
        documents_deleted=row.documents_deleted,
        attempt_count=row.attempt_count,
        started_at=row.started_at,
        completed_at=row.completed_at,
        next_attempt_at=row.next_attempt_at,
        error_code=row.error_code,
        error_summary=row.error_summary,
        created_at=row.created_at,
    )


def _integration_response(db: Session, row) -> IntegrationResponse:
    connector_class = registry.get_connector_class(row.provider)
    try:
        summary = credential_summary(db, integration=row)
        stored = load_credentials(db, integration=row)
    except Exception:
        # A deployment without an encryption key can still list connections;
        # it simply cannot decrypt them, which is reported as unconfigured.
        summary, stored = None, {}
    return IntegrationResponse(
        integration_id=row.integration_id,
        provider=row.provider,
        provider_display_name=connector_class.display_name,
        integration_name=row.integration_name,
        connection_status=row.connection_status,
        permission_sync_mode=row.permission_sync_mode,
        sync_enabled=row.sync_enabled,
        sync_interval_minutes=row.sync_interval_minutes,
        next_sync_at=row.next_sync_at,
        last_sync_at=row.last_sync_at,
        configuration={
            key: value
            for key, value in (row.configuration or {}).items()
            if key not in {"webhook_token"}
        },
        credential=summary,  # type: ignore[arg-type]
        supports_oauth=connector_class.supports_oauth,
        webhook_configured=bool(stored.get("webhook_secret")),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("/providers", response_model=ProviderListResponse)
def list_providers(
    context: Annotated[AuthenticatedContext, Depends(admin_only)],
) -> ProviderListResponse:
    ensure_enabled()
    return ProviderListResponse(
        providers=[
            ProviderInfoResponse(**info) for info in registry.list_providers()
        ]
    )


@router.get("", response_model=IntegrationListResponse)
def list_integrations(
    context: Annotated[AuthenticatedContext, Depends(admin_only)],
    db: Annotated[Session, Depends(get_db)],
) -> IntegrationListResponse:
    ensure_enabled()
    rows = db.scalars(
        select(Integration)
        .where(Integration.tenant_id == context.tenant_id)
        .order_by(Integration.created_at.desc())
    ).all()
    return IntegrationListResponse(
        integrations=[_integration_response(db, row) for row in rows]
    )


@router.post("", response_model=IntegrationResponse, status_code=status.HTTP_201_CREATED)
def create_integration_endpoint(
    payload: IntegrationCreateRequest,
    context: Annotated[AuthenticatedContext, Depends(admin_only)],
    request_meta: Annotated[RequestMeta, Depends(get_request_meta)],
    db: Annotated[Session, Depends(get_db)],
) -> IntegrationResponse:
    integration = create_integration(
        db,
        tenant_id=context.tenant_id,
        actor_user_id=context.user.user_id,
        provider=payload.provider,
        name=payload.integration_name,
        permission_sync_mode=payload.permission_sync_mode,
        sync_interval_minutes=payload.sync_interval_minutes,
        configuration=payload.configuration,
        credentials=payload.credentials,
        request_meta=request_meta,
    )
    db.flush()
    return _integration_response(db, integration)


@router.get("/{integration_id}", response_model=IntegrationResponse)
def read_integration(
    integration_id: UUID,
    context: Annotated[AuthenticatedContext, Depends(admin_only)],
    db: Annotated[Session, Depends(get_db)],
) -> IntegrationResponse:
    integration = get_integration(
        db, tenant_id=context.tenant_id, integration_id=integration_id
    )
    return _integration_response(db, integration)


@router.patch("/{integration_id}", response_model=IntegrationResponse)
def update_integration_endpoint(
    integration_id: UUID,
    payload: IntegrationUpdateRequest,
    context: Annotated[AuthenticatedContext, Depends(admin_only)],
    request_meta: Annotated[RequestMeta, Depends(get_request_meta)],
    db: Annotated[Session, Depends(get_db)],
) -> IntegrationResponse:
    integration = get_integration(
        db, tenant_id=context.tenant_id, integration_id=integration_id
    )
    update_integration(
        db,
        integration=integration,
        actor_user_id=context.user.user_id,
        name=payload.integration_name,
        permission_sync_mode=payload.permission_sync_mode,
        sync_enabled=payload.sync_enabled,
        sync_interval_minutes=payload.sync_interval_minutes,
        request_meta=request_meta,
    )
    return _integration_response(db, integration)


@router.delete("/{integration_id}", status_code=status.HTTP_204_NO_CONTENT)
def disconnect_integration_endpoint(
    integration_id: UUID,
    context: Annotated[AuthenticatedContext, Depends(admin_only)],
    request_meta: Annotated[RequestMeta, Depends(get_request_meta)],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    integration = get_integration(
        db, tenant_id=context.tenant_id, integration_id=integration_id
    )
    disconnect_integration(
        db,
        integration=integration,
        actor_user_id=context.user.user_id,
        request_meta=request_meta,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{integration_id}/credentials", response_model=IntegrationResponse)
def store_credentials_endpoint(
    integration_id: UUID,
    payload: CredentialRequest,
    context: Annotated[AuthenticatedContext, Depends(admin_only)],
    request_meta: Annotated[RequestMeta, Depends(get_request_meta)],
    db: Annotated[Session, Depends(get_db)],
) -> IntegrationResponse:
    integration = get_integration(
        db, tenant_id=context.tenant_id, integration_id=integration_id
    )
    set_credentials(
        db,
        integration=integration,
        actor_user_id=context.user.user_id,
        credentials=payload.credentials,
        scopes=payload.scopes,
        request_meta=request_meta,
    )
    return _integration_response(db, integration)


@router.post("/{integration_id}/connect", response_model=ConnectResponse)
def connect_integration_endpoint(
    integration_id: UUID,
    context: Annotated[AuthenticatedContext, Depends(admin_only)],
    request_meta: Annotated[RequestMeta, Depends(get_request_meta)],
    db: Annotated[Session, Depends(get_db)],
) -> ConnectResponse:
    integration = get_integration(
        db, tenant_id=context.tenant_id, integration_id=integration_id
    )
    ok, detail = connect_integration(
        db,
        integration=integration,
        actor_user_id=context.user.user_id,
        request_meta=request_meta,
    )
    return ConnectResponse(
        connected=ok,
        connection_status=integration.connection_status,
        detail=detail,
    )


@router.post("/{integration_id}/oauth/start", response_model=OAuthStartResponse)
def start_oauth(
    integration_id: UUID,
    context: Annotated[AuthenticatedContext, Depends(admin_only)],
    request_meta: Annotated[RequestMeta, Depends(get_request_meta)],
    db: Annotated[Session, Depends(get_db)],
) -> OAuthStartResponse:
    integration = get_integration(
        db, tenant_id=context.tenant_id, integration_id=integration_id
    )
    result = begin_oauth(
        db,
        integration=integration,
        actor_user_id=context.user.user_id,
        request_meta=request_meta,
    )
    return OAuthStartResponse(**result)


@oauth_router.get("/callback")
def oauth_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """Unauthenticated provider callback. Identity comes only from signed state."""
    if error:
        raise BadRequestError("The provider declined the authorization")
    if not code or not state:
        raise BadRequestError("The OAuth callback is incomplete")
    integration = complete_oauth(db, code=code, state=state)
    return {
        "connected": True,
        "integration_id": str(integration.integration_id),
        "provider": integration.provider,
    }


@router.get("/{integration_id}/repositories", response_model=RepositoryListResponse)
def list_repositories(
    integration_id: UUID,
    context: Annotated[AuthenticatedContext, Depends(admin_only)],
    request_meta: Annotated[RequestMeta, Depends(get_request_meta)],
    db: Annotated[Session, Depends(get_db)],
    discover: bool = Query(default=True),
) -> RepositoryListResponse:
    integration = get_integration(
        db, tenant_id=context.tenant_id, integration_id=integration_id
    )
    if discover:
        discover_repositories(
            db,
            integration=integration,
            actor_user_id=context.user.user_id,
            request_meta=request_meta,
        )
    rows = db.scalars(
        select(IntegrationRepository)
        .where(
            IntegrationRepository.tenant_id == context.tenant_id,
            IntegrationRepository.integration_id == integration.integration_id,
        )
        .order_by(IntegrationRepository.name)
    ).all()
    return RepositoryListResponse(
        repositories=[_repository_response(row) for row in rows]
    )


@router.put(
    "/{integration_id}/repositories/{repository_id}",
    response_model=RepositoryResponse,
)
def update_repository_selection(
    integration_id: UUID,
    repository_id: UUID,
    payload: RepositorySelectRequest,
    context: Annotated[AuthenticatedContext, Depends(admin_only)],
    request_meta: Annotated[RequestMeta, Depends(get_request_meta)],
    db: Annotated[Session, Depends(get_db)],
) -> RepositoryResponse:
    integration = get_integration(
        db, tenant_id=context.tenant_id, integration_id=integration_id
    )
    row = select_repository(
        db,
        integration=integration,
        actor_user_id=context.user.user_id,
        repository_id=repository_id,
        selected=payload.selected,
        sync_mode=payload.sync_mode,
        request_meta=request_meta,
    )
    return _repository_response(row)


@router.post(
    "/{integration_id}/sync",
    response_model=SyncJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def start_sync(
    integration_id: UUID,
    payload: SyncRequest,
    context: Annotated[AuthenticatedContext, Depends(admin_only)],
    request_meta: Annotated[RequestMeta, Depends(get_request_meta)],
    db: Annotated[Session, Depends(get_db)],
) -> SyncJobResponse:
    # Capture identity before the commit below expires ORM instances.
    tenant_id = context.tenant_id
    integration = get_integration(
        db, tenant_id=tenant_id, integration_id=integration_id
    )
    if not integration.sync_enabled:
        raise BadRequestError("Synchronization is disabled for this integration")
    job = sync_service.enqueue_sync_job(
        db,
        integration=integration,
        actor_user_id=context.user.user_id,
        sync_type=payload.sync_type or SYNC_TYPE_MANUAL,
        request_meta=request_meta,
    )
    job_id = job.sync_job_id
    # The job row must be visible to the engine's own short transactions. The
    # commit also clears the transaction-local RLS tenant, so restore it before
    # this request reads the finished job.
    db.commit()
    set_tenant_context(db, tenant_id)
    sync_service.run_sync_job(tenant_id=tenant_id, sync_job_id=job_id)
    db.expire_all()
    refreshed = db.scalar(
        select(IntegrationSyncJob).where(
            IntegrationSyncJob.tenant_id == tenant_id,
            IntegrationSyncJob.sync_job_id == job_id,
        )
    )
    if refreshed is None:  # pragma: no cover - defensive
        raise NotFoundError("Synchronization job not found")
    return _job_response(refreshed)


@router.get("/{integration_id}/jobs", response_model=SyncJobListResponse)
def list_sync_jobs(
    integration_id: UUID,
    context: Annotated[AuthenticatedContext, Depends(admin_only)],
    db: Annotated[Session, Depends(get_db)],
    limit: int = Query(default=25, ge=1, le=100),
) -> SyncJobListResponse:
    integration = get_integration(
        db, tenant_id=context.tenant_id, integration_id=integration_id
    )
    rows = db.scalars(
        select(IntegrationSyncJob)
        .where(
            IntegrationSyncJob.tenant_id == context.tenant_id,
            IntegrationSyncJob.integration_id == integration.integration_id,
        )
        .order_by(IntegrationSyncJob.created_at.desc())
        .limit(limit)
    ).all()
    return SyncJobListResponse(jobs=[_job_response(row) for row in rows])


@router.post("/{integration_id}/jobs/{sync_job_id}/cancel", response_model=SyncJobResponse)
def cancel_sync_job(
    integration_id: UUID,
    sync_job_id: UUID,
    context: Annotated[AuthenticatedContext, Depends(admin_only)],
    db: Annotated[Session, Depends(get_db)],
) -> SyncJobResponse:
    integration = get_integration(
        db, tenant_id=context.tenant_id, integration_id=integration_id
    )
    job = db.scalar(
        select(IntegrationSyncJob).where(
            IntegrationSyncJob.tenant_id == context.tenant_id,
            IntegrationSyncJob.integration_id == integration.integration_id,
            IntegrationSyncJob.sync_job_id == sync_job_id,
        )
    )
    if job is None:
        raise NotFoundError("Synchronization job not found")
    job.cancel_requested = True
    job.sync_status = SYNC_STATUS_CANCELLED
    return _job_response(job)


@router.get(
    "/{integration_id}/documents", response_model=ExternalDocumentListResponse
)
def list_external_documents(
    integration_id: UUID,
    context: Annotated[AuthenticatedContext, Depends(admin_only)],
    db: Annotated[Session, Depends(get_db)],
    limit: int = Query(default=100, ge=1, le=500),
) -> ExternalDocumentListResponse:
    integration = get_integration(
        db, tenant_id=context.tenant_id, integration_id=integration_id
    )
    rows = db.scalars(
        select(ExternalDocument)
        .where(
            ExternalDocument.tenant_id == context.tenant_id,
            ExternalDocument.integration_id == integration.integration_id,
        )
        .order_by(ExternalDocument.last_seen_at.desc().nullslast())
        .limit(limit)
    ).all()
    return ExternalDocumentListResponse(
        documents=[
            ExternalDocumentResponse(
                external_document_id=row.external_document_id,
                internal_document_id=row.internal_document_id,
                external_file_id=row.external_file_id,
                external_repository_id=row.external_repository_id,
                external_version_id=row.external_version_id,
                source_url=row.source_url,
                source_path=row.source_path,
                content_checksum=row.content_checksum,
                file_size=row.file_size,
                sync_status=row.sync_status,
                permission_state=row.permission_state,
                last_modified_at=row.last_modified_at,
                last_synced_at=row.last_synced_at,
                deleted_at=row.deleted_at,
            )
            for row in rows
        ]
    )


@router.get("/{integration_id}/identities", response_model=IdentityMappingListResponse)
def list_identity_mappings(
    integration_id: UUID,
    context: Annotated[AuthenticatedContext, Depends(admin_only)],
    db: Annotated[Session, Depends(get_db)],
) -> IdentityMappingListResponse:
    integration = get_integration(
        db, tenant_id=context.tenant_id, integration_id=integration_id
    )
    rows = db.scalars(
        select(IntegrationIdentityMapping)
        .where(
            IntegrationIdentityMapping.tenant_id == context.tenant_id,
            IntegrationIdentityMapping.integration_id == integration.integration_id,
        )
        .order_by(IntegrationIdentityMapping.created_at.desc())
    ).all()
    return IdentityMappingListResponse(
        mappings=[
            IdentityMappingResponse(
                mapping_id=row.mapping_id,
                external_principal_id=row.external_principal_id,
                external_principal_type=row.external_principal_type,
                user_id=row.user_id,
                role_id=row.role_id,
                is_active=row.is_active,
                created_at=row.created_at,
            )
            for row in rows
        ]
    )


@router.post(
    "/{integration_id}/identities",
    response_model=IdentityMappingResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_identity_mapping_endpoint(
    integration_id: UUID,
    payload: IdentityMappingRequest,
    context: Annotated[AuthenticatedContext, Depends(admin_only)],
    request_meta: Annotated[RequestMeta, Depends(get_request_meta)],
    db: Annotated[Session, Depends(get_db)],
) -> IdentityMappingResponse:
    integration = get_integration(
        db, tenant_id=context.tenant_id, integration_id=integration_id
    )
    mapping = create_identity_mapping(
        db,
        integration=integration,
        actor_user_id=context.user.user_id,
        external_principal_id=payload.external_principal_id,
        external_principal_type=payload.external_principal_type,
        user_id=payload.user_id,
        role_id=payload.role_id,
        request_meta=request_meta,
    )
    return IdentityMappingResponse(
        mapping_id=mapping.mapping_id,
        external_principal_id=mapping.external_principal_id,
        external_principal_type=mapping.external_principal_type,
        user_id=mapping.user_id,
        role_id=mapping.role_id,
        is_active=mapping.is_active,
        created_at=mapping.created_at,
    )


@router.delete(
    "/{integration_id}/identities/{mapping_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_identity_mapping_endpoint(
    integration_id: UUID,
    mapping_id: UUID,
    context: Annotated[AuthenticatedContext, Depends(admin_only)],
    request_meta: Annotated[RequestMeta, Depends(get_request_meta)],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    integration = get_integration(
        db, tenant_id=context.tenant_id, integration_id=integration_id
    )
    delete_identity_mapping(
        db,
        integration=integration,
        actor_user_id=context.user.user_id,
        mapping_id=mapping_id,
        request_meta=request_meta,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/webhooks/{integration_id}",
    response_model=WebhookResponse,
    include_in_schema=False,
)
async def receive_webhook(
    integration_id: UUID,
    request: Request,
    background: BackgroundTasks,
    token: str = Query(...),
    db: Session = Depends(get_db),
) -> WebhookResponse:
    """Public provider notification endpoint; fails closed on any doubt."""
    tenant_id = verify_webhook_token(integration_id, token)
    set_tenant_context(db, tenant_id)
    integration = db.scalar(
        select(Integration).where(
            Integration.tenant_id == tenant_id,
            Integration.integration_id == integration_id,
        )
    )
    if integration is None:
        raise WebhookValidationError("The webhook target does not exist")
    body = await request.body()
    if len(body) > 1024 * 1024:
        raise WebhookValidationError("The webhook body is too large")
    headers = {key.lower(): value for key, value in request.headers.items()}
    secrets = load_credentials(db, integration=integration)
    try:
        event_id, event_type = verify_and_parse(
            provider=integration.provider,
            headers=headers,
            body=body,
            secrets=secrets,
        )
    except WebhookValidationError:
        log_security_event(
            db,
            tenant_id=tenant_id,
            user_id=None,
            resource_type="INTEGRATION",
            resource_id=integration.integration_id,
            action="INTEGRATION_WEBHOOK_REJECTED",
            decision="DENY",
            reason="Webhook signature verification failed",
            policy=POLICY_INTEGRATION,
        )
        raise

    try:
        event = sync_service.record_webhook_event(
            db,
            tenant_id=tenant_id,
            integration=integration,
            external_event_id=event_id,
            event_type=event_type,
            payload_digest=hashlib.sha256(body).hexdigest(),
        )
    except SyncAlreadyRunningError:
        return WebhookResponse(accepted=True, duplicate=True, queued=False)

    queued = False
    job = None
    if integration.sync_enabled:
        try:
            job = sync_service.enqueue_sync_job(
                db,
                integration=integration,
                actor_user_id=integration.created_by,
                sync_type="WEBHOOK",
            )
            queued = True
        except SyncAlreadyRunningError:
            queued = False
    if job is not None:
        event.processing_status = "PROCESSED"
        event.processed_at = datetime.now(timezone.utc)
    db.commit()
    if job is not None:
        background.add_task(
            sync_service.run_sync_job,
            tenant_id=tenant_id,
            sync_job_id=job.sync_job_id,
        )
    return WebhookResponse(accepted=True, duplicate=False, queued=queued)


@versions_router.get(
    "/{document_id}/versions", response_model=DocumentVersionListResponse
)
def document_versions(
    document_id: UUID,
    context: Annotated[AuthenticatedContext, Depends(get_current_auth)],
    request_meta: Annotated[RequestMeta, Depends(get_request_meta)],
    db: Annotated[Session, Depends(get_db)],
) -> DocumentVersionListResponse:
    decision = authorize_document(db, context, document_id, "READ")
    enforce_authorization(db, context, decision, request_meta)
    rows = list_versions(
        db, tenant_id=context.tenant_id, document_id=document_id
    )
    return DocumentVersionListResponse(
        versions=[
            DocumentVersionResponse(
                document_version_id=row.document_version_id,
                document_id=row.document_id,
                version_number=row.version_number,
                external_version_id=row.external_version_id,
                content_checksum=row.content_checksum,
                file_size=row.file_size,
                ingestion_status=row.ingestion_status,
                is_current=row.is_current,
                source_modified_at=row.source_modified_at,
                created_at=row.created_at,
            )
            for row in rows
        ]
    )


@versions_router.post(
    "/{document_id}/versions/{document_version_id}/restore",
    response_model=DocumentVersionResponse,
)
def restore_document_version(
    document_id: UUID,
    document_version_id: UUID,
    context: Annotated[AuthenticatedContext, Depends(admin_only)],
    request_meta: Annotated[RequestMeta, Depends(get_request_meta)],
    db: Annotated[Session, Depends(get_db)],
) -> DocumentVersionResponse:
    decision = authorize_document(db, context, document_id, "MANAGE_PERMISSIONS")
    enforce_authorization(db, context, decision, request_meta)
    document = restore_version(
        db,
        context,
        document_id=document_id,
        document_version_id=document_version_id,
        request_meta=request_meta,
    )
    rows = list_versions(
        db, tenant_id=context.tenant_id, document_id=document.document_id
    )
    current = next(
        (row for row in rows if row.document_version_id == document_version_id),
        None,
    )
    if current is None:  # pragma: no cover - defensive
        raise NotFoundError("Document version not found")
    return DocumentVersionResponse(
        document_version_id=current.document_version_id,
        document_id=current.document_id,
        version_number=current.version_number,
        external_version_id=current.external_version_id,
        content_checksum=current.content_checksum,
        file_size=current.file_size,
        ingestion_status=current.ingestion_status,
        is_current=current.is_current,
        source_modified_at=current.source_modified_at,
        created_at=current.created_at,
    )
