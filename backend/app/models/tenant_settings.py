"""Validated, versioned tenant product settings.

Only retrieval and product preferences live here. Authorization, RLS, document
permissions and citation validation are code invariants and are deliberately
not representable in this schema.
"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class TenantSettings(Base):
    __tablename__ = "tenant_settings"

    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.tenant_id", ondelete="CASCADE"), primary_key=True
    )
    settings: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))
    updated_by: Mapped[UUID] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "updated_by"],
            ["users.tenant_id", "users.user_id"],
            name="fk_tenant_settings_updated_by",
            ondelete="RESTRICT",
        ),
        CheckConstraint("version >= 1", name="ck_tenant_settings_version"),
    )


class TenantSettingsHistory(Base):
    __tablename__ = "tenant_settings_history"

    history_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    settings: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    changed_by: Mapped[UUID] = mapped_column(nullable=False)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "version", name="uq_tenant_settings_history_version"
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["tenant_settings.tenant_id"],
            name="fk_tenant_settings_history_settings",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "changed_by"],
            ["users.tenant_id", "users.user_id"],
            name="fk_tenant_settings_history_user",
            ondelete="RESTRICT",
        ),
        CheckConstraint("version >= 1", name="ck_tenant_settings_history_version"),
        Index("ix_tenant_settings_history_tenant_changed", "tenant_id", "changed_at"),
    )
