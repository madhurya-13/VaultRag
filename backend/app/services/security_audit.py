"""Append-only security audit logging.

Audit writes deliberately accept an optional session:

* Allowed actions pass the request session so the audit row and the business
  change commit atomically.
* Denied authorization checks pass ``None`` so the denial is recorded even
  though the request transaction will be rolled back.
"""

import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal, set_tenant_context
from app.core.request_context import RequestMeta
from app.models import SecurityAuditLog, User

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


def security_analytics(
    db: Session,
    *,
    tenant_id: UUID,
    days: int = 7,
) -> dict:
    """Aggregate tenant audit activity for the security dashboard.

    Only real audit rows are counted. The caller is responsible for enforcing the
    ``VIEW_AUDIT_LOGS`` capability.
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)
    window = and_(
        SecurityAuditLog.tenant_id == tenant_id,
        SecurityAuditLog.timestamp >= since,
    )

    decision_rows = db.execute(
        select(SecurityAuditLog.decision, func.count())
        .where(window)
        .group_by(SecurityAuditLog.decision)
    ).all()
    decisions = {decision: int(count) for decision, count in decision_rows}

    action_rows = db.execute(
        select(SecurityAuditLog.action, func.count())
        .where(window)
        .group_by(SecurityAuditLog.action)
        .order_by(func.count().desc(), SecurityAuditLog.action)
    ).all()

    resource_rows = db.execute(
        select(SecurityAuditLog.resource_type, func.count())
        .where(window)
        .group_by(SecurityAuditLog.resource_type)
        .order_by(func.count().desc(), SecurityAuditLog.resource_type)
    ).all()

    day_expr = func.date_trunc("day", SecurityAuditLog.timestamp)
    daily_rows = db.execute(
        select(day_expr, SecurityAuditLog.decision, func.count())
        .where(window)
        .group_by(day_expr, SecurityAuditLog.decision)
        .order_by(day_expr)
    ).all()
    daily: dict = {}
    for day, decision, count in daily_rows:
        point = daily.setdefault(
            day.date(),
            {"date": day.date(), "allowed": 0, "denied": 0, "total": 0},
        )
        if decision == "DENY":
            point["denied"] += int(count)
        else:
            point["allowed"] += int(count)
        point["total"] += int(count)

    actor_rows = db.execute(
        select(SecurityAuditLog.user_id, User.username, func.count())
        .outerjoin(
            User,
            and_(
                User.tenant_id == SecurityAuditLog.tenant_id,
                User.user_id == SecurityAuditLog.user_id,
            ),
        )
        .where(window, SecurityAuditLog.user_id.is_not(None))
        .group_by(SecurityAuditLog.user_id, User.username)
        .order_by(func.count().desc())
        .limit(10)
    ).all()

    return {
        "days": days,
        "since": since,
        "total_events": sum(decisions.values()),
        "allowed_events": decisions.get("ALLOW", 0),
        "denied_events": decisions.get("DENY", 0),
        "by_action": [
            {"key": action, "count": int(count)}
            for action, count in action_rows
        ],
        "by_resource_type": [
            {"key": resource_type, "count": int(count)}
            for resource_type, count in resource_rows
        ],
        "daily": [daily[day] for day in sorted(daily)],
        "top_actors": [
            {
                "user_id": user_id,
                "username": username,
                "count": int(count),
            }
            for user_id, username, count in actor_rows
        ],
    }


def audit_timestamp() -> datetime:
    """Small indirection that keeps tests from having to know timestamp storage."""
    return datetime.now().astimezone()
