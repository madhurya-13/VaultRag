"""Permission-aware document metadata endpoints."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.request_context import RequestMeta, get_request_meta
from app.core.security_constants import CAP_CREATE_DOCUMENT, POLICY_ALLOW
from app.dependencies.authentication import (
    AuthenticatedContext,
    get_current_auth,
    require_capability,
)
from app.models import Document, DocumentPermission
from app.schemas.document import DocumentCreateRequest, DocumentResponse
from app.schemas.document_permission import (
    AccessCheckResponse,
    PermissionGrantRequest,
    PermissionResponse,
)
from app.services.authorization import (
    authorize_document,
    enforce_authorization,
    record_authorization_decision,
)
from app.services.document_permissions import (
    grant_permission,
    revoke_permission,
)
from app.services.documents import (
    create_document,
    get_tenant_document,
    list_accessible_documents,
)
from app.services.security_audit import log_security_event

router = APIRouter(prefix="/documents", tags=["Documents"])


def _document_response(document: Document) -> DocumentResponse:
    return DocumentResponse(
        document_id=document.document_id,
        tenant_id=document.tenant_id,
        document_name=document.document_name,
        document_type=document.document_type,
        department=document.department,
        classification=document.classification,
        required_clearance_level=document.required_clearance_level,
        uploaded_by=document.uploaded_by,
        is_active=document.is_active,
        created_at=document.created_at,
        updated_at=document.updated_at,
    )


def _permission_response(permission: DocumentPermission) -> PermissionResponse:
    return PermissionResponse(
        permission_id=permission.permission_id,
        tenant_id=permission.tenant_id,
        document_id=permission.document_id,
        role_id=permission.role_id,
        user_id=permission.user_id,
        permission_type=permission.permission_type,
        granted_by=permission.granted_by,
        granted_at=permission.granted_at,
        expires_at=permission.expires_at,
        is_active=permission.is_active,
        revoked_at=permission.revoked_at,
        revoked_by=permission.revoked_by,
        cross_department_allowed=permission.cross_department_allowed,
    )


@router.post(
    "",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create document metadata (CREATE_DOCUMENT capability)",
)
def create_document_endpoint(
    payload: DocumentCreateRequest,
    context: AuthenticatedContext = Depends(
        require_capability(CAP_CREATE_DOCUMENT)
    ),
    db: Session = Depends(get_db),
    request_meta: RequestMeta = Depends(get_request_meta),
) -> DocumentResponse:
    document = create_document(
        db,
        context,
        document_name=payload.document_name,
        document_type=payload.document_type,
        department=payload.department,
        classification=payload.classification,
        required_clearance_level=payload.required_clearance_level,
    )
    log_security_event(
        db,
        tenant_id=context.tenant_id,
        user_id=context.user.user_id,
        resource_type="DOCUMENT",
        resource_id=document.document_id,
        action="CREATE",
        decision="ALLOW",
        reason="Document metadata created",
        policy=POLICY_ALLOW,
        request_meta=request_meta,
    )
    return _document_response(document)


@router.get(
    "",
    response_model=list[DocumentResponse],
    summary="List documents the caller may READ",
)
def list_documents(
    context: AuthenticatedContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
    request_meta: RequestMeta = Depends(get_request_meta),
) -> list[DocumentResponse]:
    documents = list_accessible_documents(db, context)
    log_security_event(
        db,
        tenant_id=context.tenant_id,
        user_id=context.user.user_id,
        resource_type="DOCUMENT",
        resource_id=None,
        action="LIST",
        decision="ALLOW",
        reason=f"Returned {len(documents)} documents after authorization filtering",
        policy=POLICY_ALLOW,
        request_meta=request_meta,
    )
    return [_document_response(document) for document in documents]


@router.get(
    "/{document_id}",
    response_model=DocumentResponse,
    summary="Read one authorized document",
)
def get_document(
    document_id: UUID,
    context: AuthenticatedContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
    request_meta: RequestMeta = Depends(get_request_meta),
) -> DocumentResponse:
    decision = authorize_document(db, context, document_id, "READ")
    enforce_authorization(db, context, decision, request_meta)
    document = get_tenant_document(db, context.tenant_id, document_id)
    return _document_response(document)


@router.get(
    "/{document_id}/access-check",
    response_model=AccessCheckResponse,
    summary="Evaluate one document action without returning the document",
)
def access_check(
    document_id: UUID,
    action: Annotated[str, Query(min_length=1, max_length=50)] = "READ",
    context: AuthenticatedContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
    request_meta: RequestMeta = Depends(get_request_meta),
) -> AccessCheckResponse:
    decision = authorize_document(db, context, document_id, action)
    record_authorization_decision(db, context, decision, request_meta)
    return AccessCheckResponse(
        document_id=document_id,
        action=decision.action,
        allowed=decision.allowed,
        policy=decision.policy,
        reason=decision.reason,
    )


@router.post(
    "/{document_id}/permissions",
    response_model=PermissionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Grant one exact document action to a role or user",
)
def grant_document_permission(
    document_id: UUID,
    payload: PermissionGrantRequest,
    context: AuthenticatedContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
    request_meta: RequestMeta = Depends(get_request_meta),
) -> PermissionResponse:
    decision = authorize_document(db, context, document_id, "MANAGE_PERMISSIONS")
    enforce_authorization(db, context, decision, request_meta)

    permission = grant_permission(
        db,
        tenant_id=context.tenant_id,
        document_id=document_id,
        actor_user_id=context.user.user_id,
        permission_type=payload.permission_type,
        role_name=payload.role_name,
        user_id=payload.user_id,
        expires_at=payload.expires_at,
        cross_department_allowed=payload.cross_department_allowed,
    )
    subject = (
        f"role {payload.role_name}"
        if payload.role_name is not None
        else f"user {payload.user_id}"
    )
    log_security_event(
        db,
        tenant_id=context.tenant_id,
        user_id=context.user.user_id,
        resource_type="DOCUMENT_PERMISSION",
        resource_id=permission.permission_id,
        action="PERMISSION_GRANT",
        decision="ALLOW",
        reason=f"Granted {permission.permission_type} to {subject}",
        policy=POLICY_ALLOW,
        request_meta=request_meta,
    )
    return _permission_response(permission)


@router.delete(
    "/{document_id}/permissions/{permission_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke a document permission (soft delete, effective immediately)",
)
def revoke_document_permission(
    document_id: UUID,
    permission_id: UUID,
    context: AuthenticatedContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
    request_meta: RequestMeta = Depends(get_request_meta),
) -> Response:
    decision = authorize_document(db, context, document_id, "MANAGE_PERMISSIONS")
    enforce_authorization(db, context, decision, request_meta)

    permission = revoke_permission(
        db,
        tenant_id=context.tenant_id,
        document_id=document_id,
        permission_id=permission_id,
        actor_user_id=context.user.user_id,
    )
    log_security_event(
        db,
        tenant_id=context.tenant_id,
        user_id=context.user.user_id,
        resource_type="DOCUMENT_PERMISSION",
        resource_id=permission.permission_id,
        action="PERMISSION_REVOKE",
        decision="ALLOW",
        reason=f"Revoked {permission.permission_type}",
        policy=POLICY_ALLOW,
        request_meta=request_meta,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
