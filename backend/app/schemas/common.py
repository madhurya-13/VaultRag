"""Shared schema primitives: strict models and normalised field types."""

from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field

from app.core.passwords import (
    PASSWORD_MAX_LENGTH,
    PASSWORD_MIN_LENGTH,
    normalize_email,
    normalize_role_name,
    normalize_username,
    validate_password_strength,
)


class StrictRequestModel(BaseModel):
    """Reject unknown fields instead of silently ignoring them.

    This blocks mass-assignment attacks such as sending ``is_active``,
    ``role_id`` or ``tenant_id`` to an endpoint that was never designed to
    accept them. A 422 is safer than guessing the caller's intent.
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )


# Reusable annotated types so every endpoint applies the same policy.
StrongPassword = Annotated[
    str,
    Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH),
    AfterValidator(validate_password_strength),
]
NormalizedEmail = Annotated[
    EmailStr,
    Field(max_length=320),
    AfterValidator(normalize_email),
]
Username = Annotated[str, Field(min_length=3, max_length=100), AfterValidator(normalize_username)]
RoleName = Annotated[str, Field(min_length=2, max_length=50), AfterValidator(normalize_role_name)]

# Login must NOT apply the password-strength policy: existing accounts created
# under an older policy must still be able to sign in. It only bounds the input
# to protect the hasher from absurdly large payloads.
PasswordStr = Annotated[str, Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)]
