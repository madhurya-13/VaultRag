"""Admin-only tenant settings API."""

from __future__ import annotations


from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.request_context import RequestMeta, get_request_meta
from app.core.security_constants import CAP_MANAGE_TENANT_SETTINGS, POLICY_ALLOW
from app.dependencies.authentication import AuthenticatedContext, require_capability
from app.schemas.tenant_settings import (
    TenantSettingsHistoryItem,
    TenantSettingsHistoryResponse,
    TenantSettingsResponse,
    TenantSettingsRollbackRequest,
    TenantSettingsUpdateRequest,
)
from app.services import tenant_settings as service
from app.services.security_audit import log_security_event

router = APIRouter(prefix="/admin/settings", tags=["Tenant Settings"])
admin_only = require_capability(CAP_MANAGE_TENANT_SETTINGS)


def _audit(
    db: Session,
    context: AuthenticatedContext,
    request_meta: RequestMeta,
    *,
    action: str,
    reason: str,
) -> None:
    log_security_event(
        db,
        tenant_id=context.tenant_id,
        user_id=context.user.user_id,
        resource_type="TENANT_SETTINGS",
        resource_id=context.tenant_id,
        action=action,
        decision="ALLOW",
        reason=reason,
        policy=POLICY_ALLOW,
        request_meta=request_meta,
    )


@router.get("", response_model=TenantSettingsResponse)
def read_settings(
    context: AuthenticatedContext = Depends(admin_only),
    db: Session = Depends(get_db),
) -> TenantSettingsResponse:
    settings, version = service.merged(db, context.tenant_id)
    return TenantSettingsResponse(settings=settings, version=version)


@router.put("", response_model=TenantSettingsResponse)
def update_settings(
    payload: TenantSettingsUpdateRequest,
    context: AuthenticatedContext = Depends(admin_only),
    db: Session = Depends(get_db),
    request_meta: RequestMeta = Depends(get_request_meta),
) -> TenantSettingsResponse:
    settings, version = service.update(
        db,
        tenant_id=context.tenant_id,
        user_id=context.user.user_id,
        changes=payload.model_dump(),
    )
    _audit(
        db,
        context,
        request_meta,
        action="SETTINGS_UPDATE",
        reason=f"version={version};keys={','.join(sorted(payload.model_dump(exclude_none=True)))}",
    )
    return TenantSettingsResponse(settings=settings, version=version)


@router.get("/history", response_model=TenantSettingsHistoryResponse)
def read_history(
    context: AuthenticatedContext = Depends(admin_only),
    db: Session = Depends(get_db),
) -> TenantSettingsHistoryResponse:
    return TenantSettingsHistoryResponse(
        history=[
            TenantSettingsHistoryItem(
                version=item.version,
                settings=item.settings,
                changed_by=item.changed_by,
                changed_at=item.changed_at,
            )
            for item in service.history(db, context.tenant_id)
        ]
    )


@router.post("/rollback", response_model=TenantSettingsResponse)
def rollback(
    payload: TenantSettingsRollbackRequest,
    context: AuthenticatedContext = Depends(admin_only),
    db: Session = Depends(get_db),
    request_meta: RequestMeta = Depends(get_request_meta),
) -> TenantSettingsResponse:
    settings, version = service.rollback(
        db,
        tenant_id=context.tenant_id,
        user_id=context.user.user_id,
        version=payload.version,
    )
    _audit(
        db,
        context,
        request_meta,
        action="SETTINGS_ROLLBACK",
        reason=f"restored_version={payload.version};new_version={version}",
    )
    return TenantSettingsResponse(settings=settings, version=version)
