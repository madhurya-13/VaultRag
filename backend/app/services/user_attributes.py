"""Administrator-managed ABAC attributes for tenant users."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestError
from app.core.security_constants import ATTR_CLEARANCE_LEVEL
from app.models import UserAttribute
from app.services.abac import validate_attribute_value
from app.services.users import get_tenant_user


def list_user_attributes(
    db: Session, tenant_id: UUID, user_id: UUID
) -> list[UserAttribute]:
    return list(
        db.scalars(
            select(UserAttribute)
            .where(
                UserAttribute.tenant_id == tenant_id,
                UserAttribute.user_id == user_id,
            )
            .order_by(UserAttribute.attribute_name)
        ).all()
    )


def upsert_user_attributes(
    db: Session,
    *,
    tenant_id: UUID,
    target_user_id: UUID,
    actor_user_id: UUID,
    attributes: dict[str, str],
) -> list[UserAttribute]:
    """Create or update attributes, validating every value before writing."""
    if not attributes:
        raise BadRequestError("At least one attribute is required")

    get_tenant_user(db, tenant_id, target_user_id)  # tenant-scoped existence check

    # A user must never be able to raise their own clearance through an
    # administrator endpoint (even if they somehow hold the ADMIN role).
    if target_user_id == actor_user_id and ATTR_CLEARANCE_LEVEL in attributes:
        raise BadRequestError("You cannot change your own clearance level")

    normalized: dict[str, str] = {}
    for name, value in attributes.items():
        if not isinstance(value, str):
            raise BadRequestError(f"Attribute {name} must be a string")
        normalized[name] = validate_attribute_value(name, value)

    for name, value in normalized.items():
        row = db.scalar(
            select(UserAttribute).where(
                UserAttribute.tenant_id == tenant_id,
                UserAttribute.user_id == target_user_id,
                UserAttribute.attribute_name == name,
            )
        )
        if row is None:
            db.add(
                UserAttribute(
                    tenant_id=tenant_id,
                    user_id=target_user_id,
                    attribute_name=name,
                    attribute_value=value,
                    updated_by=actor_user_id,
                )
            )
        else:
            row.attribute_value = value
            row.updated_by = actor_user_id

    db.flush()

    rows = list_user_attributes(db, tenant_id, target_user_id)
    for row in rows:
        db.refresh(row)
    return rows
