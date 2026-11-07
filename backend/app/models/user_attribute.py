"""User attributes used by ABAC policy evaluation.

These are security-relevant facts, not user-editable profile fields. Writes are
admin-only at the API layer, and the service layer will additionally reject a
user changing their own clearance. The database constraints keep the basic
value shape honest even if a script bypasses the API.

Missing required attributes must be treated as DENY by the authorization engine.
"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class UserAttribute(Base):
    __tablename__ = "user_attributes"

    attribute_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.tenant_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[UUID] = mapped_column(nullable=False)

    attribute_name: Mapped[str] = mapped_column(String(50), nullable=False)
    attribute_value: Mapped[str] = mapped_column(String(255), nullable=False)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    # Audit actor. Intentionally not a cascading FK: historical changes should
    # survive user lifecycle changes.
    updated_by: Mapped[UUID | None] = mapped_column(nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "user_id",
            "attribute_name",
            name="uq_user_attributes_tenant_user_name",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["users.tenant_id", "users.user_id"],
            name="fk_user_attributes_tenant_user",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "attribute_name IN ("
            "'department', 'clearance_level', 'employment_status', "
            "'location', 'designation'"
            ")",
            name="ck_user_attributes_name",
        ),
        CheckConstraint(
            "length(trim(attribute_value)) > 0",
            name="ck_user_attributes_value_not_blank",
        ),
        CheckConstraint(
            "attribute_name <> 'clearance_level' OR attribute_value ~ '^[0-5]$'",
            name="ck_user_attributes_clearance",
        ),
        CheckConstraint(
            "attribute_name <> 'employment_status' "
            "OR attribute_value IN ('ACTIVE', 'INACTIVE', 'SUSPENDED', 'TERMINATED')",
            name="ck_user_attributes_employment_status",
        ),
        Index("ix_user_attributes_tenant_user", "tenant_id", "user_id"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<UserAttribute tenant_id={self.tenant_id} user_id={self.user_id} "
            f"name={self.attribute_name!r}>"
        )
