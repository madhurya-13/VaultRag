"""Password hashing and JWT helpers.

Password storage
----------------
Argon2id is used through ``argon2-cffi`` (the library's default is Argon2id).
Argon2 is memory-hard, which makes offline GPU cracking expensive. A random salt
is generated per hash and embedded in the encoded hash string.

JWT design
----------
* Only an explicit allow-list of HMAC algorithms is accepted when decoding. This
  prevents the classic ``alg: none`` / algorithm-confusion attack.
* Tokens are typed (``type: access``) so another token type can never be replayed
  as an access token later.
* ``sub`` and ``tenant_id`` are UUIDs and are validated again after decoding.
* Roles are included for observability, but authentication/authorisation NEVER
  trusts them. The dependency layer reloads roles from PostgreSQL on every
  request so a revoked role takes effect immediately.
"""

import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from pydantic import BaseModel, ConfigDict, ValidationError

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# Argon2id with the library defaults (memory_cost=64 MiB, time_cost=3,
# parallelism=4). Tune only after measuring on your deployment hardware.
_hasher = PasswordHasher()

# A real hash of a throwaway value. Login verifies against this when the account
# does not exist so that "unknown email" and "wrong password" take similar time.
_DUMMY_HASH = _hasher.hash("vaultrag-timing-equalisation-placeholder")


class TokenExpiredError(Exception):
    """Raised when a structurally valid token is past its expiry."""


class TokenInvalidError(Exception):
    """Raised when a token is malformed, wrongly signed or has bad claims."""


class AccessTokenClaims(BaseModel):
    """The trusted, validated contents of an access token."""

    model_config = ConfigDict(extra="ignore")

    sub: UUID
    tenant_id: UUID
    roles: list[str] = []
    type: str
    jti: str | None = None


def hash_password(password: str) -> str:
    """Return an Argon2id encoded hash. Never store the plaintext."""
    return _hasher.hash(password)


def verify_password(plain_password: str, password_hash: str) -> bool:
    """Constant-time-ish Argon2 verification with a safe failure mode."""
    try:
        return _hasher.verify(password_hash, plain_password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def perform_dummy_verify(plain_password: str) -> None:
    """Burn roughly the same CPU time as a real verification (anti-enumeration)."""
    try:
        _hasher.verify(_DUMMY_HASH, plain_password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        pass


def password_needs_rehash(password_hash: str) -> bool:
    """True when the hash was produced with older Argon2 parameters."""
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def create_access_token(
    *,
    user_id: UUID,
    tenant_id: UUID,
    roles: list[str],
    expires_delta: timedelta | None = None,
) -> tuple[str, int]:
    """Create a signed access token.

    Returns ``(token, expires_at_epoch_seconds)``.
    """
    settings = get_settings()
    now = datetime.now(timezone.utc)
    expires_at = now + (
        expires_delta
        if expires_delta is not None
        else timedelta(minutes=settings.access_token_expire_minutes)
    )

    payload = {
        "sub": str(user_id),
        "tenant_id": str(tenant_id),
        "roles": roles,
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
        "jti": uuid4().hex,
    }

    token = jwt.encode(
        payload,
        settings.jwt_secret_key.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )
    return token, int(expires_at.timestamp())


def decode_access_token(token: str) -> AccessTokenClaims:
    """Verify signature/expiry and validate the claim shape.

    Raises ``TokenExpiredError`` or ``TokenInvalidError``. Callers translate
    these into HTTP 401 responses; internal details are not returned to clients.
    """
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key.get_secret_value(),
            algorithms=[settings.jwt_algorithm],  # explicit allow-list
            options={
                "require": ["exp", "iat", "sub", "tenant_id", "type"],
                "verify_exp": True,
                "verify_iat": True,
                "verify_signature": True,
            },
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenExpiredError from exc
    except jwt.InvalidTokenError as exc:
        # Covers bad signature, malformed token, unsupported algorithm, etc.
        raise TokenInvalidError from exc

    try:
        claims = AccessTokenClaims.model_validate(payload)
    except ValidationError as exc:
        raise TokenInvalidError from exc

    if claims.type != "access":
        raise TokenInvalidError

    return claims
