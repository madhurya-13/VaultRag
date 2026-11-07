"""Append-only security audit logging.

Audit writes deliberately accept an optional session:

* Allowed actions pass the request session so the audit row and the business
  change commit atomically.
* Denied authorization checks pass ``None`` so the denial is recorded even
  though the request transaction will be rolled back.
"""

import logging
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal, set_tenant_context
from app.core.request_context import RequestMeta
from app.models import SecurityAuditLog

logger = logging.getLogger(__name__)


def log_security_event(
    db: Session | None,
    *,
    tenant_id: UUID,
    user_id: UUID | None,
    resource_type: str,
    resource_id: UUID | None,
    action: str,
    decision: str,
    reason: str,
    policy: str,
    request_meta: RequestMeta | None = None,
) -> SecurityAuditLog:
    """Write one audit event.

    The caller is responsible for choosing the transaction. A denial should use
    an independent session because the request transaction will be aborted.
    """
    if decision not in {"ALLOW", "DENY"}:
        raise ValueError("decision must be ALLOW or DENY")

    meta = request_meta or RequestMeta(request_id=uuid4(), ip_address=None)
    event = SecurityAuditLog(
        tenant_id=tenant_id,
        user_id=user_id,
        resource_type=resource_type.strip().upper(),
        resource_id=resource_id,
        action=action.strip().upper(),
        decision=decision,
        reason=reason,
        policy=policy,
        ip_address=meta.ip_address,
        request_id=meta.request_id,
    )

    if db is not None:
        db.add(event)
        db.flush()
        return event

    try:
        with SessionLocal() as audit_db:
            set_tenant_context(audit_db, tenant_id)
            audit_db.add(event)
            # Refresh BEFORE commit: set_config(..., true) is transaction-local,
            # so a refresh after commit would run without tenant context and RLS
            # would legitimately hide the row we just wrote.
            audit_db.flush()
            audit_db.refresh(event)
            audit_db.commit()
            return event
    except Exception:
        # Security-critical audit failure must fail closed. The caller receives
        # a 500 rather than a silently unaudited decision.
        logger.exception(
            "Failed to write independent security audit event action=%s decision=%s",
            action,
            decision,
        )
        raise


def list_security_audit_logs(
    db: Session,
    *,
    tenant_id: UUID,
    user_id: UUID | None = None,
    resource_type: str | None = None,
    resource_id: UUID | None = None,
    action: str | None = None,
    decision: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[SecurityAuditLog]:
    """Return tenant-scoped audit events, newest first."""
    statement = select(SecurityAuditLog).where(
        SecurityAuditLog.tenant_id == tenant_id
    )
    if user_id is not None:
        statement = statement.where(SecurityAuditLog.user_id == user_id)
    if resource_type is not None:
        statement = statement.where(
            SecurityAuditLog.resource_type == resource_type.strip().upper()
        )
    if resource_id is not None:
        statement = statement.where(SecurityAuditLog.resource_id == resource_id)
    if action is not None:
        statement = statement.where(
            SecurityAuditLog.action == action.strip().upper()
        )
    if decision is not None:
        statement = statement.where(
            SecurityAuditLog.decision == decision.strip().upper()
        )

    statement = (
        statement.order_by(SecurityAuditLog.timestamp.desc(), SecurityAuditLog.event_id)
        .limit(limit)
        .offset(offset)
    )
    return list(db.scalars(statement).all())


def audit_timestamp() -> datetime:
    """Small indirection that keeps tests from having to know timestamp storage."""
    return datetime.now().astimezone()
