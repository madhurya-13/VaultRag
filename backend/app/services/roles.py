"""Tenant-scoped role catalogue queries."""

from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.core.security_constants import ROLE_CAPABILITIES
from app.models import Role, UserRole


def list_tenant_roles(db: Session, tenant_id: UUID) -> list[dict]:
    """Return every role in the tenant with capabilities and assigned-user counts.

    Capabilities remain code-defined policy (see ``ROLE_CAPABILITIES``); the role
    catalogue itself is tenant-owned database state.
    """

    rows = db.execute(
        select(Role, func.count(UserRole.user_id))
        .outerjoin(
            UserRole,
            and_(
                UserRole.tenant_id == Role.tenant_id,
                UserRole.role_id == Role.role_id,
            ),
        )
        .where(Role.tenant_id == tenant_id)
        .group_by(Role.role_id)
        .order_by(Role.role_name)
    ).all()

    return [
        {
            "role_id": role.role_id,
            "role_name": role.role_name,
            "description": role.description,
            "capabilities": sorted(ROLE_CAPABILITIES.get(role.role_name, frozenset())),
            "user_count": int(user_count),
        }
        for role, user_count in rows
    ]
