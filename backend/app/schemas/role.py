"""Role catalogue response schemas."""

from uuid import UUID

from app.schemas.common import StrictRequestModel


class RoleResponse(StrictRequestModel):
    role_id: UUID
    role_name: str
    description: str | None = None
    capabilities: list[str]
    user_count: int
