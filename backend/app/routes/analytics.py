"""Unified, tenant-scoped enterprise analytics API."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.request_context import RequestMeta, get_request_meta
from app.core.security_constants import CAP_VIEW_ANALYTICS, POLICY_ALLOW
from app.dependencies.authentication import AuthenticatedContext, require_capability
from app.services import analytics
from app.services.security_audit import log_security_event

router = APIRouter(prefix="/analytics", tags=["Enterprise Analytics"])
admin_only = require_capability(CAP_VIEW_ANALYTICS)
Days = Annotated[int, Query(ge=1, le=365)]


def _audit(
    db: Session,
    context: AuthenticatedContext,
    request_meta: RequestMeta,
    section: str,
    days: int,
) -> None:
    log_security_event(
        db,
        tenant_id=context.tenant_id,
        user_id=context.user.user_id,
        resource_type="ANALYTICS",
        resource_id=None,
        action="ANALYTICS_VIEW",
        decision="ALLOW",
        reason=f"section={section};days={days}",
        policy=POLICY_ALLOW,
        request_meta=request_meta,
    )


@router.get("/overview")
def get_overview(
    days: Days = 30,
    context: AuthenticatedContext = Depends(admin_only),
    db: Session = Depends(get_db),
    request_meta: RequestMeta = Depends(get_request_meta),
) -> dict:
    _audit(db, context, request_meta, "overview", days)
    return analytics.overview(db, context.tenant_id, days)


@router.get("/usage")
def get_usage(
    days: Days = 30,
    context: AuthenticatedContext = Depends(admin_only),
    db: Session = Depends(get_db),
) -> dict:
    return analytics.usage(db, context.tenant_id, days)


@router.get("/documents")
def get_documents(
    days: Days = 30,
    context: AuthenticatedContext = Depends(admin_only),
    db: Session = Depends(get_db),
) -> dict:
    return analytics.documents(db, context.tenant_id, days)


@router.get("/retrieval")
def get_retrieval(
    days: Days = 30,
    context: AuthenticatedContext = Depends(admin_only),
    db: Session = Depends(get_db),
) -> dict:
    return analytics.retrieval(db, context.tenant_id, days)


@router.get("/integrations")
def get_integrations(
    days: Days = 30,
    context: AuthenticatedContext = Depends(admin_only),
    db: Session = Depends(get_db),
) -> dict:
    return analytics.integrations(db, context.tenant_id, days)


@router.get("/feedback")
def get_feedback(
    days: Days = 30,
    context: AuthenticatedContext = Depends(admin_only),
    db: Session = Depends(get_db),
) -> dict:
    return analytics.feedback(db, context.tenant_id, days)


@router.get("/knowledge-gaps")
def get_gaps(
    days: Days = 30,
    context: AuthenticatedContext = Depends(admin_only),
    db: Session = Depends(get_db),
) -> dict:
    return analytics.gaps(db, context.tenant_id, days)


@router.get("/security")
def get_security(
    days: Days = 30,
    context: AuthenticatedContext = Depends(admin_only),
    db: Session = Depends(get_db),
) -> dict:
    return analytics.security(db, context.tenant_id, days)
