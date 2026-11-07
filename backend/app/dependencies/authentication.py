"""Reusable authentication / authorisation dependencies.

Trust model
-----------
The JWT proves "this request was issued to user X". It does NOT prove that user
X is still active, still in that tenant, or still holds the roles written into
the token. Those facts are reloaded from PostgreSQL on every request.

Consequences:
* Deactivating a user invalidates their existing tokens immediately.
* Removing a role takes effect on the next request, without token expiry.
* A token whose ``tenant_id`` claim does not match the user row is rejected.
* ``tenant_id`` is never read from query parameters, bodies or custom headers.
"""

import logging
from dataclasses import dataclass
from uuid import UUID

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db, set_tenant_context
from app.core.exceptions import ForbiddenError, UnauthorizedError
from app.core.security import (
    TokenExpiredError,
    TokenInvalidError,
    decode_access_token,
)
from app.core.security_constants import CAP_MANAGE_USERS
from app.models import Organization, User
from app.services.rbac import has_capability
from app.services.users import role_names_for_user

logger = logging.getLogger(__name__)

# ``auto_error=False`` lets us return a consistent JSON 401 with a
# WWW-Authenticate header instead of Starlette's default 403 for a missing
# header. Swagger's Authorize button accepts a pasted bearer token.
bearer_scheme = HTTPBearer(
    auto_error=False,
    scheme_name="JWTBearer",
    description="Paste the access_token returned by POST /auth/login",
)


@dataclass(frozen=True, slots=True)
class AuthenticatedContext:
    """Everything downstream code is allowed to trust about the caller."""

    user: User
    organization: Organization
    roles: tuple[str, ...]

    @property
    def tenant_id(self) -> UUID:
        # Always the database value. There is no code path where this can come
        # from the request.
        return self.user.tenant_id

    def has_role(self, role_name: str) -> bool:
        return role_name in self.roles

    def has_capability(self, capability: str) -> bool:
        return has_capability(self.roles, capability)


def get_current_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> AuthenticatedContext:
    """Verify the bearer token and build a trusted caller context."""
    if credentials is None or not credentials.credentials:
        raise UnauthorizedError("Not authenticated")

    try:
        claims = decode_access_token(credentials.credentials)
    except TokenExpiredError as exc:
        raise UnauthorizedError("Access token has expired") from exc
    except TokenInvalidError as exc:
        raise UnauthorizedError("Invalid access token") from exc

    # Establish the RLS tenant context from the signed claim BEFORE any table
    # lookup. The row lookup below then re-validates that the user really
    # belongs to this tenant; a forged claim cannot select another tenant.
    set_tenant_context(db, claims.tenant_id)

    user = db.scalar(
        select(User).where(
            User.user_id == claims.sub,
            User.tenant_id == claims.tenant_id,
        )
    )
    if user is None or not user.is_active:
        raise UnauthorizedError("Authentication required")

    # Defence in depth: a correctly signed token should never disagree with the
    # database. If it does, something is wrong with token issuance or an old
    # token belongs to a re-created user. Deny and log it.
    if user.tenant_id != claims.tenant_id:
        logger.warning(
            "Rejected token tenant mismatch user_id=%s token_tenant=%s db_tenant=%s",
            user.user_id,
            claims.tenant_id,
            user.tenant_id,
        )
        raise UnauthorizedError("Invalid access token")

    organization = db.scalar(
        select(Organization).where(Organization.tenant_id == user.tenant_id)
    )
    if organization is None or not organization.is_active:
        raise UnauthorizedError("Authentication required")

    # Loaded fresh from the database on every request -> revocation is instant.
    roles = tuple(role_names_for_user(db, user.tenant_id, user.user_id))

    return AuthenticatedContext(
        user=user,
        organization=organization,
        roles=roles,
    )


def require_admin(
    context: AuthenticatedContext = Depends(get_current_auth),
) -> AuthenticatedContext:
    """Allow only callers whose CURRENT database roles grant MANAGE_USERS."""
    if not has_capability(context.roles, CAP_MANAGE_USERS):
        raise ForbiddenError("ADMIN role required")
    return context


def require_capability(capability: str):
    """Build a FastAPI dependency for a non-document RBAC capability."""

    def _dependency(
        context: AuthenticatedContext = Depends(get_current_auth),
    ) -> AuthenticatedContext:
        if not has_capability(context.roles, capability):
            raise ForbiddenError("Insufficient role capability")
        return context

    return _dependency
