"""Authentication endpoints: self-service signup, login and current identity."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import create_access_token
from app.dependencies.authentication import AuthenticatedContext, get_current_auth
from app.schemas.auth import (
    LoginRequest,
    MeResponse,
    PublicRegisterRequest,
    PublicRegisterResponse,
    TokenResponse,
)
from app.services.users import (
    authenticate_user,
    register_public_user,
    role_names_for_user,
)

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post(
    "/register",
    response_model=PublicRegisterResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Request an account in an organization (pending admin approval)",
)
def register(payload: PublicRegisterRequest, db: Session = Depends(get_db)) -> PublicRegisterResponse:
    """Public signup.

    The account is created inactive and without roles. It cannot log in until an
    administrator in the same tenant activates it and assigns an appropriate
    role. This endpoint accepts no role or tenant-role information at all.
    """
    user = register_public_user(
        db,
        tenant_id=payload.tenant_id,
        username=payload.username,
        email=payload.email,
        password=payload.password,
    )
    return PublicRegisterResponse(
        user_id=user.user_id,
        tenant_id=user.tenant_id,
        username=user.username,
        email=user.email,
        is_active=user.is_active,
        roles=[],
        message=(
            "Account created in pending state. An administrator of this "
            "organization must activate it and assign a role."
        ),
    )


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Exchange email + password + tenant for a JWT access token",
)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    user = authenticate_user(
        db,
        tenant_id=payload.tenant_id,
        email=payload.email,
        password=payload.password,
    )

    # Roles come from the database at issuance time so the token is useful for
    # diagnostics. Authorisation still reloads them on every request.
    roles = role_names_for_user(db, user.tenant_id, user.user_id)
    token, expires_at = create_access_token(
        user_id=user.user_id,
        tenant_id=user.tenant_id,
        roles=roles,
    )
    now = int(datetime.now(timezone.utc).timestamp())
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        expires_in=max(0, expires_at - now),
    )


@router.get(
    "/me",
    response_model=MeResponse,
    summary="Return the identity derived from the bearer token + database",
)
def me(context: AuthenticatedContext = Depends(get_current_auth)) -> MeResponse:
    return MeResponse(
        user_id=context.user.user_id,
        username=context.user.username,
        email=context.user.email,
        tenant_id=context.tenant_id,
        organization_name=context.organization.organization_name,
        roles=list(context.roles),
        is_active=context.user.is_active,
        created_at=context.user.created_at,
    )
