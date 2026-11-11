"""Tenant-scoped security audit log access (ADMIN capability)."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.request_context import RequestMeta, get_request_meta
from app.core.security_constants import CAP_VIEW_AUDIT_LOGS, POLICY_ALLOW
from app.dependencies.authentication import (
    AuthenticatedContext,
    require_capability,
)
from app.schemas.security import SecurityAnalyticsResponse, SecurityAuditLogResponse
from app.services.security_audit import (
    list_security_audit_logs,
    log_security_event,
    security_analytics,
)

router = APIRouter(prefix="/security", tags=["Security audit"])


@router.get(
    "/audit-logs",
    response_model=list[SecurityAuditLogResponse],
    summary="Read tenant-scoped security audit events (ADMIN only)",
)
def get_audit_logs(
    user_id: UUID | None = None,
    resource_type: Annotated[str | None, Query(max_length=50)] = None,
    resource_id: UUID | None = None,
    action: Annotated[str | None, Query(max_length=50)] = None,
    decision: Literal["ALLOW", "DENY"] | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    context: AuthenticatedContext = Depends(
        require_capability(CAP_VIEW_AUDIT_LOGS)
    ),
    db: Session = Depends(get_db),
    request_meta: RequestMeta = Depends(get_request_meta),
) -> list[SecurityAuditLogResponse]:
    events = list_security_audit_logs(
        db,
        tenant_id=context.tenant_id,
        user_id=user_id,
        resource_type=resource_type,
        resource_id=resource_id,
        action=action,
        decision=decision,
        limit=limit,
        offset=offset,
    )
    log_security_event(
        db,
        tenant_id=context.tenant_id,
        user_id=context.user.user_id,
        resource_type="SECURITY_AUDIT_LOG",
        resource_id=None,
        action="VIEW",
        decision="ALLOW",
        reason=f"Returned {len(events)} audit events",
        policy=POLICY_ALLOW,
        request_meta=request_meta,
    )
    return [
        SecurityAuditLogResponse(
            event_id=event.event_id,
            tenant_id=event.tenant_id,
            user_id=event.user_id,
            resource_type=event.resource_type,
            resource_id=event.resource_id,
            action=event.action,
            decision=event.decision,
            reason=event.reason,
            policy=event.policy,
            ip_address=event.ip_address,
            request_id=event.request_id,
            timestamp=event.timestamp,
        )
        for event in events
    ]


@router.get(
    "/analytics",
    response_model=SecurityAnalyticsResponse,
    summary="Aggregate tenant audit activity (ADMIN only)",
)
def get_analytics(
    days: Annotated[int, Query(ge=1, le=90)] = 7,
    context: AuthenticatedContext = Depends(
        require_capability(CAP_VIEW_AUDIT_LOGS)
    ),
    db: Session = Depends(get_db),
    request_meta: RequestMeta = Depends(get_request_meta),
) -> SecurityAnalyticsResponse:
    analytics = security_analytics(
        db,
        tenant_id=context.tenant_id,
        days=days,
    )
    log_security_event(
        db,
        tenant_id=context.tenant_id,
        user_id=context.user.user_id,
        resource_type="SECURITY_AUDIT_LOG",
        resource_id=None,
        action="ANALYTICS_VIEW",
        decision="ALLOW",
        reason=f"Aggregated {analytics['total_events']} events over {days} days",
        policy=POLICY_ALLOW,
        request_meta=request_meta,
    )
    return SecurityAnalyticsResponse(**analytics)
