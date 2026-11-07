"""Tenant-scoped ABAC attribute endpoints.

Users may read their own attributes. Only ADMIN (MANAGE_ATTRIBUTES capability)
may write attributes, and an administrator still cannot change their own
clearance.
"""

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.exceptions import ForbiddenError
from app.core.request_context import RequestMeta, get_request_meta
from app.core.security_constants import CAP_MANAGE_ATTRIBUTES, POLICY_ALLOW
from app.dependencies.authentication import (
    AuthenticatedContext,
    get_current_auth,
    require_capability,
)
from app.schemas.user_attribute import (
    UserAttributeResponse,
    UserAttributesResponse,
    UserAttributeUpsertRequest,
)
from app.services.rbac import has_capability
from app.services.security_audit import log_security_event
from app.services.user_attributes import (
    list_user_attributes,
    upsert_user_attributes,
)
from app.services.users import get_tenant_user

router = APIRouter(prefix="/users", tags=["User attributes"])


def _response(
    tenant_id: UUID, user_id: UUID, rows
) -> UserAttributesResponse:
    return UserAttributesResponse(
        tenant_id=tenant_id,
        user_id=user_id,
        attributes=[
            UserAttributeResponse(
                attribute_name=row.attribute_name,
                attribute_value=row.attribute_value,
                updated_at=row.updated_at,
            )
            for row in rows
        ],
    )


@router.get(
    "/{user_id}/attributes",
    response_model=UserAttributesResponse,
    summary="Read a user's ABAC attributes (self or ADMIN)",
)
def get_attributes(
    user_id: UUID,
    context: AuthenticatedContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
) -> UserAttributesResponse:
    if (
        context.user.user_id != user_id
        and not has_capability(context.roles, CAP_MANAGE_ATTRIBUTES)
    ):
        raise ForbiddenError("Insufficient role capability")

    get_tenant_user(db, context.tenant_id, user_id)
    rows = list_user_attributes(db, context.tenant_id, user_id)
    return _response(context.tenant_id, user_id, rows)


@router.put(
    "/{user_id}/attributes",
    response_model=UserAttributesResponse,
    summary="Create or update a user's ABAC attributes (ADMIN only)",
)
def put_attributes(
    user_id: UUID,
    payload: UserAttributeUpsertRequest,
    context: AuthenticatedContext = Depends(
        require_capability(CAP_MANAGE_ATTRIBUTES)
    ),
    db: Session = Depends(get_db),
    request_meta: RequestMeta = Depends(get_request_meta),
) -> UserAttributesResponse:
    rows = upsert_user_attributes(
        db,
        tenant_id=context.tenant_id,
        target_user_id=user_id,
        actor_user_id=context.user.user_id,
        attributes=payload.attributes,
    )
    log_security_event(
        db,
        tenant_id=context.tenant_id,
        user_id=context.user.user_id,
        resource_type="USER",
        resource_id=user_id,
        action="ATTRIBUTE_UPDATE",
        decision="ALLOW",
        reason="Updated attributes: " + ", ".join(sorted(payload.attributes)),
        policy=POLICY_ALLOW,
        request_meta=request_meta,
    )
    return _response(context.tenant_id, user_id, rows)
