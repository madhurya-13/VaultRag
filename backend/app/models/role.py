"""Role model. Roles are tenant-owned, never global.

A role called ``ADMIN`` in Organization A is a different database row from
``ADMIN`` in Organization B. Role names are therefore only unique per tenant.
This is the foundation for tenant-scoped RBAC and, later, ABAC policies.
"""

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.organization import Organization


class Role(Base):
    __tablename__ = "roles"

    role_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.tenant_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role_name: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    organization: Mapped["Organization"] = relationship(back_populates="roles")

    __table_args__ = (
        # Referenced by the composite FK in user_roles.
        UniqueConstraint("tenant_id", "role_id", name="uq_roles_tenant_role"),
        UniqueConstraint("tenant_id", "role_name", name="uq_roles_tenant_name"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Role role_id={self.role_id} tenant_id={self.tenant_id} name={self.role_name!r}>"
