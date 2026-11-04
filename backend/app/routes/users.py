"""Tenant-scoped user administration. Every route requires a CURRENT ADMIN role.

None of these endpoints accept a tenant identifier. The only tenant used is
``context.tenant_id``, which comes from the authenticated user's row.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.dependencies.authentication import AuthenticatedContext, require_admin
from app.models import User
from app.schemas.user import (
    RoleAssignmentRequest,
    UserCreateRequest,
    UserResponse,
    UserStatusUpdateRequest,
)
from app.services.users import (
    assign_role,
    create_user,
    get_tenant_user,
    list_tenant_users,
    revoke_role,
    role_names_by_user_ids,
    role_names_for_user,
    set_user_active,
)

router = APIRouter(prefix="/users", tags=["Users (ADMIN only)"])


def _to_response(user: User, roles: list[str]) -> UserResponse:
    return UserResponse(
        user_id=user.user_id,
        tenant_id=user.tenant_id,
        username=user.username,
        email=user.email,
        is_active=user.is_active,
        created_at=user.created_at,
        roles=roles,
    )


@router.get(
    "",
    response_model=list[UserResponse],
    summary="List users in the authenticated tenant",
)
def list_users(
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    context: AuthenticatedContext = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[UserResponse]:
    users = list_tenant_users(db, context.tenant_id, limit=limit, offset=offset)
    roles_map = role_names_by_user_ids(
        db, context.tenant_id, [user.user_id for user in users]
    )
    return [_to_response(user, roles_map.get(user.user_id, [])) for user in users]


@router.post(
    "",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an active user and assign roles (ADMIN only)",
)
def create_tenant_user(
    payload: UserCreateRequest,
    context: AuthenticatedContext = Depends(require_admin),
    db: Session = Depends(get_db),
) -> UserResponse:
    user = create_user(
        db,
        tenant_id=context.tenant_id,
        username=payload.username,
        email=payload.email,
        password=payload.password,
        role_names=payload.role_names,
        is_active=True,
    )
    return _to_response(user, role_names_for_user(db, context.tenant_id, user.user_id))


@router.get(
    "/{user_id}",
    response_model=UserResponse,
    summary="Get one user in the authenticated tenant",
)
def get_user(
    user_id: UUID,
    context: AuthenticatedContext = Depends(require_admin),
    db: Session = Depends(get_db),
) -> UserResponse:
    # Cross-tenant IDs return 404, identical to a non-existent ID.
    user = get_tenant_user(db, context.tenant_id, user_id)
    return _to_response(user, role_names_for_user(db, context.tenant_id, user.user_id))


@router.post(
    "/{user_id}/roles",
    response_model=UserResponse,
    summary="Assign a role by name within the tenant (ADMIN only)",
)
def add_role(
    user_id: UUID,
    payload: RoleAssignmentRequest,
    context: AuthenticatedContext = Depends(require_admin),
    db: Session = Depends(get_db),
) -> UserResponse:
    assign_role(
        db,
        tenant_id=context.tenant_id,
        user_id=user_id,
        role_name=payload.role_name,
    )
    user = get_tenant_user(db, context.tenant_id, user_id)
    return _to_response(user, role_names_for_user(db, context.tenant_id, user.user_id))


@router.delete(
    "/{user_id}/roles/{role_name}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke a role (effective immediately)",
)
def remove_role(
    user_id: UUID,
    role_name: str,
    context: AuthenticatedContext = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response:
    revoke_role(
        db,
        tenant_id=context.tenant_id,
        user_id=user_id,
        role_name=role_name.strip().upper(),
        actor_user_id=context.user.user_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch(
    "/{user_id}/status",
    response_model=UserResponse,
    summary="Activate or deactivate a user (ADMIN only)",
)
def update_status(
    user_id: UUID,
    payload: UserStatusUpdateRequest,
    context: AuthenticatedContext = Depends(require_admin),
    db: Session = Depends(get_db),
) -> UserResponse:
    user = set_user_active(
        db,
        tenant_id=context.tenant_id,
        user_id=user_id,
        is_active=payload.is_active,
        actor_user_id=context.user.user_id,
    )
    return _to_response(user, role_names_for_user(db, context.tenant_id, user.user_id))
