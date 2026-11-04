"""Organization (tenant) business logic."""

import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.models import Organization, Role

logger = logging.getLogger(__name__)

# Roles every new tenant receives. They are tenant-owned rows, not global rows.
DEFAULT_ROLES: tuple[tuple[str, str], ...] = (
    ("ADMIN", "Full administrative control inside this organization"),
    ("HR", "Human resources documents and employee data"),
    ("FINANCE", "Financial documents and reports"),
    ("ENGINEERING", "Engineering documentation and technical assets"),
)


def register_organization(db: Session, organization_name: str) -> Organization:
    """Create a tenant and its default role catalogue.

    The tenant UUID is generated server-side. Role rows are created in the same
    transaction, so a tenant can never exist with a partial role catalogue.
    """
    organization = Organization(organization_name=organization_name)
    db.add(organization)

    try:
        # flush() assigns the Python-side UUID so roles can reference it.
        db.flush()
        db.add_all(
            Role(
                tenant_id=organization.tenant_id,
                role_name=name,
                description=description,
            )
            for name, description in DEFAULT_ROLES
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        logger.exception("Organization registration failed")
        raise ConflictError("Organization could not be created") from exc

    db.refresh(organization)
    logger.info("Registered organization tenant_id=%s", organization.tenant_id)
    return organization


def get_organization_or_raise(
    db: Session,
    tenant_id,
    *,
    require_active: bool = True,
) -> Organization:
    """Load a tenant or fail with a safe error.

    ``require_active=True`` is used for authentication and provisioning. Inactive
    tenants must behave as if their users do not exist.
    """
    organization = db.get(Organization, tenant_id)
    if organization is None:
        raise NotFoundError("Organization not found")
    if require_active and not organization.is_active:
        raise ForbiddenError("Organization is inactive")
    return organization


def get_role_names(db: Session, tenant_id) -> list[str]:
    """Return the tenant's role catalogue, alphabetically."""
    return list(
        db.scalars(
            select(Role.role_name)
            .where(Role.tenant_id == tenant_id)
            .order_by(Role.role_name)
        ).all()
    )
