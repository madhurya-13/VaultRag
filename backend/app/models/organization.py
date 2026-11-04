"""Organization (tenant) model.

``organizations`` is the tenant root. Every other business table carries a
``tenant_id`` foreign key pointing here, which is what makes Milestone 2's
Row-Level Security policy possible.
"""

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, String, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:  # avoids runtime circular imports; SQLAlchemy resolves the strings
    from app.models.role import Role
    from app.models.user import User


class Organization(Base):
    __tablename__ = "organizations"

    tenant_id: Mapped[UUID] = mapped_column(
        primary_key=True, default=uuid4
    )
    organization_name: Mapped[str] = mapped_column(String(200), nullable=False)

    # timezone=True stores timestamptz in PostgreSQL. Never use naive timestamps
    # for security-relevant data.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )

    users: Mapped[list["User"]] = relationship(
        back_populates="organization", passive_deletes=True
    )
    roles: Mapped[list["Role"]] = relationship(
        back_populates="organization", passive_deletes=True
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Organization tenant_id={self.tenant_id} name={self.organization_name!r}>"
