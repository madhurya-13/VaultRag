"""Organization request/response schemas."""

from datetime import datetime
from uuid import UUID

from pydantic import Field

from app.schemas.common import StrictRequestModel


class OrganizationRegisterRequest(StrictRequestModel):
    # extra="forbid" means a caller cannot smuggle in ``is_active`` or roles.
    organization_name: str = Field(min_length=2, max_length=200)


class OrganizationRegisterResponse(StrictRequestModel):
    tenant_id: UUID
    organization_name: str
    is_active: bool
    created_at: datetime
    roles: list[str]


class OrganizationResponse(StrictRequestModel):
    tenant_id: UUID
    organization_name: str
    is_active: bool
    created_at: datetime
