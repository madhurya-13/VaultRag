"""Document permission grants for role-based and direct-user access.

A grant has exactly one subject:

* ``role_id`` for every user currently holding that tenant-owned role;
* ``user_id`` for a direct grant to one user.

The single shared ``tenant_id`` plus composite foreign keys make cross-tenant
grants impossible at the database level. Partial unique indexes prevent
duplicate *active* grants while still allowing a revoked or expired row to
remain as history.

Revocation is soft: ``is_active`` becomes false and ``revoked_at`` is set. The
row is never deleted, so historical grants remain inspectable.
"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class DocumentPermission(Base):
    __tablename__ = "document_permissions"

    permission_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.tenant_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    document_id: Mapped[UUID] = mapped_column(nullable=False)

    # Exactly one of role_id / user_id is present.
    role_id: Mapped[UUID | None] = mapped_column(nullable=True)
    user_id: Mapped[UUID | None] = mapped_column(nullable=True)

    permission_type: Mapped[str] = mapped_column(String(20), nullable=False)
    granted_by: Mapped[UUID] = mapped_column(nullable=False)
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_by: Mapped[UUID | None] = mapped_column(nullable=True)

    # ABAC department check requires an explicit, auditable override when the
    # user's department differs from the document's department.
    cross_department_allowed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "permission_id",
            name="uq_document_permissions_tenant_permission",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["documents.tenant_id", "documents.document_id"],
            name="fk_document_permissions_tenant_document",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "role_id"],
            ["roles.tenant_id", "roles.role_id"],
            name="fk_document_permissions_tenant_role",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["users.tenant_id", "users.user_id"],
            name="fk_document_permissions_tenant_user",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "granted_by"],
            ["users.tenant_id", "users.user_id"],
            name="fk_document_permissions_tenant_granted_by",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "num_nonnulls(role_id, user_id) = 1",
            name="ck_document_permissions_one_subject",
        ),
        CheckConstraint(
            "permission_type IN ('READ', 'UPDATE', 'DELETE', 'MANAGE_PERMISSIONS')",
            name="ck_document_permissions_type",
        ),
        CheckConstraint(
            "expires_at IS NULL OR expires_at > granted_at",
            name="ck_document_permissions_expiry",
        ),
        # Inactive rows must record when they were revoked. Active rows may
        # still have revoked_at set during an edge-case cleanup; the important
        # rule is that a silent inactive row is impossible.
        CheckConstraint(
            "is_active OR revoked_at IS NOT NULL",
            name="ck_document_permissions_revoked",
        ),
        # Duplicate active role grants are blocked by the database, including
        # concurrent requests that bypass application checks.
        Index(
            "uq_document_permissions_active_role",
            "tenant_id",
            "document_id",
            "role_id",
            "permission_type",
            unique=True,
            postgresql_where=text("is_active AND role_id IS NOT NULL"),
        ),
        Index(
            "uq_document_permissions_active_user",
            "tenant_id",
            "document_id",
            "user_id",
            "permission_type",
            unique=True,
            postgresql_where=text("is_active AND user_id IS NOT NULL"),
        ),
        Index(
            "ix_document_permissions_tenant_document",
            "tenant_id",
            "document_id",
        ),
        Index(
            "ix_document_permissions_tenant_role",
            "tenant_id",
            "role_id",
            postgresql_where=text("role_id IS NOT NULL"),
        ),
        Index(
            "ix_document_permissions_tenant_user",
            "tenant_id",
            "user_id",
            postgresql_where=text("user_id IS NOT NULL"),
        ),
        Index(
            "ix_document_permissions_tenant_expires_at",
            "tenant_id",
            "expires_at",
            postgresql_where=text("is_active"),
        ),
        Index(
            "ix_document_permissions_tenant_active",
            "tenant_id",
            "is_active",
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<DocumentPermission permission_id={self.permission_id} "
            f"tenant_id={self.tenant_id} type={self.permission_type!r}>"
        )
