"""Document permission and access-check schemas."""

from datetime import datetime
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.schemas.common import RoleName, StrictRequestModel


class PermissionGrantRequest(StrictRequestModel):
    permission_type: str = Field(min_length=1, max_length=20)
    role_name: RoleName | None = None
    user_id: UUID | None = None
    expires_at: datetime | None = None
    cross_department_allowed: bool = False

    @field_validator("permission_type")
    @classmethod
    def _normalize_permission_type(cls, value: str) -> str:
        return value.strip().upper()

    @model_validator(mode="after")
    def _exactly_one_subject(self) -> "PermissionGrantRequest":
        if (self.role_name is None) == (self.user_id is None):
            raise ValueError("Provide exactly one of role_name or user_id")
        return self


class PermissionResponse(StrictRequestModel):
    permission_id: UUID
    tenant_id: UUID
    document_id: UUID
    role_id: UUID | None
    user_id: UUID | None
    permission_type: str
    granted_by: UUID
    granted_at: datetime
    expires_at: datetime | None
    is_active: bool
    revoked_at: datetime | None
    revoked_by: UUID | None
    cross_department_allowed: bool


class AccessCheckResponse(StrictRequestModel):
    document_id: UUID
    action: str
    allowed: bool
    policy: str
    reason: str
