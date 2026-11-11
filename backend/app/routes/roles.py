"""Tenant role catalogue (ADMIN-only management view)."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security_constants import CAP_MANAGE_ROLES
from app.dependencies.authentication import AuthenticatedContext, require_capability
from app.schemas.role import RoleResponse
from app.services.roles import list_tenant_roles

router = APIRouter(prefix="/roles", tags=["Roles (ADMIN only)"])


@router.get(
    "",
    response_model=list[RoleResponse],
    summary="List tenant roles, capabilities and assigned-user counts",
)
def list_roles(
    context: AuthenticatedContext = Depends(require_capability(CAP_MANAGE_ROLES)),
    db: Session = Depends(get_db),
) -> list[RoleResponse]:
    return [
        RoleResponse(**role)
        for role in list_tenant_roles(db, context.tenant_id)
    ]
