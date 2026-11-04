"""User model.

Tenant integrity choices
------------------------
* ``tenant_id`` is NOT NULL: there is no such thing as a user outside a tenant.
* ``UNIQUE (tenant_id, user_id)`` looks redundant next to the primary key, but it
  is required so ``user_roles`` can reference ``(tenant_id, user_id)`` with a
  composite foreign key. That is what prevents a user from one tenant being
  linked to a role from another tenant.
* Email and username are unique *within a tenant* only. Two organizations may
  legitimately have ``hr@example.com`` as a user. Emails are lower-cased by the
  service layer before storage and lookup.
"""

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.organization import Organization


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.tenant_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    username: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)

    # Argon2id encoded hash (salt + parameters + digest). Never plaintext.
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    organization: Mapped["Organization"] = relationship(back_populates="users")

    __table_args__ = (
        # Referenced by the composite FK in user_roles.
        UniqueConstraint("tenant_id", "user_id", name="uq_users_tenant_user"),
        UniqueConstraint("tenant_id", "email", name="uq_users_tenant_email"),
        UniqueConstraint("tenant_id", "username", name="uq_users_tenant_username"),
        Index("ix_users_tenant_created_at", "tenant_id", "created_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<User user_id={self.user_id} tenant_id={self.tenant_id} email={self.email!r}>"
