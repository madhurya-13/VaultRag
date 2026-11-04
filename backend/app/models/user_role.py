"""User <-> Role association with database-enforced tenant consistency.

This table is the heart of cross-tenant protection in Milestone 1.

A naive schema would use two independent foreign keys::

    user_id -> users.user_id
    role_id -> roles.role_id

Nothing in that schema stops a row from linking Alice (tenant A) to
``ADMIN`` (tenant B). The application might check it, but one forgotten check,
one future script, or one SQL console is enough to create an escalation.

Instead, this table stores a single ``tenant_id`` and uses two *composite*
foreign keys:

    (tenant_id, user_id) -> users(tenant_id, user_id)
    (tenant_id, role_id) -> roles(tenant_id, role_id)

PostgreSQL now rejects the bad row even if the application contains a bug. The
user half ``(tenant_A, user_A)`` exists, but the role half ``(tenant_A, role_B)``
does not: the single shared ``tenant_id`` must satisfy both foreign keys, so a
cross-tenant link cannot be inserted.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    DateTime,
    ForeignKeyConstraint,
    Index,
    PrimaryKeyConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class UserRole(Base):
    __tablename__ = "user_roles"

    user_id: Mapped[UUID] = mapped_column(nullable=False)
    role_id: Mapped[UUID] = mapped_column(nullable=False)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)

    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        # A user cannot hold the same role twice.
        PrimaryKeyConstraint("user_id", "role_id", name="pk_user_roles"),
        # THE tenant-isolation constraints.
        ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["users.tenant_id", "users.user_id"],
            name="fk_user_roles_tenant_user",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "role_id"],
            ["roles.tenant_id", "roles.role_id"],
            name="fk_user_roles_tenant_role",
            ondelete="CASCADE",
        ),
        # Keeps the redundant tenant_id honest even before the composite checks.
        ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.tenant_id"],
            name="fk_user_roles_tenant_organization",
            ondelete="CASCADE",
        ),
        Index("ix_user_roles_tenant_user", "tenant_id", "user_id"),
        Index("ix_user_roles_tenant_role", "tenant_id", "role_id"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<UserRole tenant_id={self.tenant_id} "
            f"user_id={self.user_id} role_id={self.role_id}>"
        )
