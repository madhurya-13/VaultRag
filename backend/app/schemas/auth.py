"""Authentication request/response schemas."""

from datetime import datetime
from uuid import UUID

from pydantic import Field

from app.schemas.common import (
    NormalizedEmail,
    PasswordStr,
    StrictRequestModel,
    StrongPassword,
    Username,
)


class PublicRegisterRequest(StrictRequestModel):
    """Self-service signup.

    There is deliberately no ``roles``/``role_id``/``is_active`` field. The
    account is created inactive and role-less; an administrator must approve it
    and assign a role inside the same tenant.
    """

    tenant_id: UUID
    username: Username
    email: NormalizedEmail
    password: StrongPassword


class LoginRequest(StrictRequestModel):
    tenant_id: UUID
    email: NormalizedEmail
    password: PasswordStr


class TokenResponse(StrictRequestModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(description="Seconds until the token expires")


class PublicRegisterResponse(StrictRequestModel):
    """Signup creates a pending account; it does not grant access."""

    user_id: UUID
    tenant_id: UUID
    username: str
    email: str
    is_active: bool
    roles: list[str]
    message: str


class MeResponse(StrictRequestModel):
    user_id: UUID
    username: str
    email: str
    tenant_id: UUID
    organization_name: str
    roles: list[str]
    is_active: bool
    created_at: datetime
