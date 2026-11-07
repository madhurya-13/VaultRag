"""Tenant-scoped user administration. Every route requires a CURRENT ADMIN role.

None of these endpoints accept a tenant identifier. The only tenant used is
``context.tenant_id``, which comes from the authenticated user's row.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.exceptions import NotFoundError
from app.core.request_context import RequestMeta, get_request_meta
from app.core.security_constants import POLICY_ALLOW, POLICY_RESOURCE_NOT_FOUND
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
from app.services.security_audit import log_security_event

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


def _audit_cross_tenant_user_attempt(
    context: AuthenticatedContext,
    request_meta: RequestMeta,
    *,
    user_id: UUID,
    action: str,
) -> None:
    """Record a denied foreign-user reference in the actor's own audit trail.

    The target is deliberately absent from this tenant. The independent audit
    session is required because the request transaction is about to roll back.
    """
    log_security_event(
        None,
        tenant_id=context.tenant_id,
        user_id=context.user.user_id,
        resource_type="USER",
        resource_id=user_id,
        action=action,
        decision="DENY",
        reason="Target user not found in this tenant",
        policy=POLICY_RESOURCE_NOT_FOUND,
        request_meta=request_meta,
    )


def _require_tenant_user(
    db: Session,
    context: AuthenticatedContext,
    request_meta: RequestMeta,
    *,
    user_id: UUID,
    action: str,
) -> User:
    try:
        return get_tenant_user(db, context.tenant_id, user_id)
    except NotFoundError:
        _audit_cross_tenant_user_attempt(
            context, request_meta, user_id=user_id, action=action
        )
        raise


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
    request_meta: RequestMeta = Depends(get_request_meta),
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
    log_security_event(
        db,
        tenant_id=context.tenant_id,
        user_id=context.user.user_id,
        resource_type="USER",
        resource_id=user.user_id,
        action="USER_CREATE",
        decision="ALLOW",
        reason="Administrator created a user",
        policy=POLICY_ALLOW,
        request_meta=request_meta,
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
    request_meta: RequestMeta = Depends(get_request_meta),
) -> UserResponse:
    # Cross-tenant IDs return 404, identical to a non-existent ID.
    user = _require_tenant_user(
        db, context, request_meta, user_id=user_id, action="USER_READ"
    )
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
    request_meta: RequestMeta = Depends(get_request_meta),
) -> UserResponse:
    _require_tenant_user(
        db, context, request_meta, user_id=user_id, action="ROLE_ASSIGN"
    )
    assign_role(
        db,
        tenant_id=context.tenant_id,
        user_id=user_id,
        role_name=payload.role_name,
    )
    log_security_event(
        db,
        tenant_id=context.tenant_id,
        user_id=context.user.user_id,
        resource_type="USER",
        resource_id=user_id,
        action="ROLE_ASSIGN",
        decision="ALLOW",
        reason=f"Assigned role {payload.role_name}",
        policy=POLICY_ALLOW,
        request_meta=request_meta,
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
    request_meta: RequestMeta = Depends(get_request_meta),
) -> Response:
    _require_tenant_user(
        db, context, request_meta, user_id=user_id, action="ROLE_REVOKE"
    )
    revoke_role(
        db,
        tenant_id=context.tenant_id,
        user_id=user_id,
        role_name=role_name.strip().upper(),
        actor_user_id=context.user.user_id,
    )
    log_security_event(
        db,
        tenant_id=context.tenant_id,
        user_id=context.user.user_id,
        resource_type="USER",
        resource_id=user_id,
        action="ROLE_REVOKE",
        decision="ALLOW",
        reason=f"Revoked role {role_name.strip().upper()}",
        policy=POLICY_ALLOW,
        request_meta=request_meta,
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
    request_meta: RequestMeta = Depends(get_request_meta),
) -> UserResponse:
    _require_tenant_user(
        db, context, request_meta, user_id=user_id, action="USER_STATUS_UPDATE"
    )
    user = set_user_active(
        db,
        tenant_id=context.tenant_id,
        user_id=user_id,
        is_active=payload.is_active,
        actor_user_id=context.user.user_id,
    )
    log_security_event(
        db,
        tenant_id=context.tenant_id,
        user_id=context.user.user_id,
        resource_type="USER",
        resource_id=user_id,
        action="USER_STATUS_UPDATE",
        decision="ALLOW",
        reason=f"Set is_active={payload.is_active}",
        policy=POLICY_ALLOW,
        request_meta=request_meta,
    )
    return _to_response(user, role_names_for_user(db, context.tenant_id, user.user_id))
