"""User management schemas (administrator-controlled provisioning)."""

from datetime import datetime
from uuid import UUID

from pydantic import Field, field_validator

from app.schemas.common import (
    NormalizedEmail,
    RoleName,
    StrictRequestModel,
    StrongPassword,
    Username,
)


class UserCreateRequest(StrictRequestModel):
    """Admin-created user. Roles are names, resolved inside the admin's tenant.

    A caller can never supply a ``role_id`` or ``tenant_id``: the tenant comes
    from the authenticated database record, and roles are resolved by name
    against that same tenant.
    """

    username: Username
    email: NormalizedEmail
    password: StrongPassword
    role_names: list[RoleName] = Field(default_factory=list, max_length=10)

    @field_validator("role_names")
    @classmethod
    def _deduplicate(cls, value: list[str]) -> list[str]:
        # Preserve order while removing duplicates.
        return list(dict.fromkeys(value))


class UserResponse(StrictRequestModel):
    user_id: UUID
    tenant_id: UUID
    username: str
    email: str
    is_active: bool
    created_at: datetime
    roles: list[str]


class RoleAssignmentRequest(StrictRequestModel):
    role_name: RoleName


class UserStatusUpdateRequest(StrictRequestModel):
    is_active: bool
