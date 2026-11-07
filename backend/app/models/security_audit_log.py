"""Append-only security audit events.

Audit rows are intentionally not linked to users or resources with cascading
foreign keys. Deleting or deactivating a user must not erase the historical
record of what happened.

The application runtime role will receive SELECT and INSERT only. UPDATE and
DELETE privileges are withheld, so application users cannot rewrite history.
"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SecurityAuditLog(Base):
    __tablename__ = "security_audit_logs"

    event_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    user_id: Mapped[UUID | None] = mapped_column(nullable=True)

    resource_type: Mapped[str] = mapped_column(String(50), nullable=False)
    resource_id: Mapped[UUID | None] = mapped_column(nullable=True)

    action: Mapped[str] = mapped_column(String(50), nullable=False)
    decision: Mapped[str] = mapped_column(String(10), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    policy: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default=text("'UNKNOWN'")
    )

    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    request_id: Mapped[UUID] = mapped_column(nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "decision IN ('ALLOW', 'DENY')",
            name="ck_security_audit_logs_decision",
        ),
        CheckConstraint(
            "length(trim(action)) > 0",
            name="ck_security_audit_logs_action_not_blank",
        ),
        CheckConstraint(
            "length(trim(resource_type)) > 0",
            name="ck_security_audit_logs_resource_type_not_blank",
        ),
        Index(
            "ix_security_audit_logs_tenant_timestamp",
            "tenant_id",
            "timestamp",
        ),
        Index(
            "ix_security_audit_logs_tenant_user_timestamp",
            "tenant_id",
            "user_id",
            "timestamp",
        ),
        Index(
            "ix_security_audit_logs_tenant_resource",
            "tenant_id",
            "resource_type",
            "resource_id",
        ),
        Index(
            "ix_security_audit_logs_tenant_action_decision_timestamp",
            "tenant_id",
            "action",
            "decision",
            "timestamp",
        ),
        Index("ix_security_audit_logs_request_id", "request_id"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<SecurityAuditLog event_id={self.event_id} "
            f"tenant_id={self.tenant_id} action={self.action!r} "
            f"decision={self.decision!r}>"
        )
