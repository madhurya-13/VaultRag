"""Password policy shared by API schemas and the CLI bootstrap path.

Keeping the policy in the core layer means every entry point - public signup,
admin-created users and the operator CLI - enforces exactly the same rules.
"""

import re

PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 128

_USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,99}$")
_ROLE_NAME_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{1,49}$")

# Tiny deny-list. Production systems should also query a breached-password
# corpus (for example the Have I Been Pwned k-anonymity range API).
_COMMON_PASSWORDS = {
    "password1234",
    "password12345",
    "qwerty123456",
    "letmein123456",
    "admin1234567",
}


def validate_password_strength(value: str) -> str:
    """Raise ``ValueError`` when a password does not meet the policy."""
    if len(value) < PASSWORD_MIN_LENGTH:
        raise ValueError(f"Password must be at least {PASSWORD_MIN_LENGTH} characters")
    if len(value) > PASSWORD_MAX_LENGTH:
        raise ValueError(f"Password must be at most {PASSWORD_MAX_LENGTH} characters")
    if not any(ch.isalpha() for ch in value):
        raise ValueError("Password must contain at least one letter")
    if not any(ch.isdigit() for ch in value):
        raise ValueError("Password must contain at least one digit")
    if value.lower() in _COMMON_PASSWORDS:
        raise ValueError("Password is too common")
    return value


def normalize_email(value: str) -> str:
    return str(value).strip().lower()


def normalize_username(value: str) -> str:
    value = value.strip()
    if not _USERNAME_PATTERN.fullmatch(value):
        raise ValueError(
            "Username must be 3-100 characters and contain only letters, digits, "
            "underscore, dot or hyphen (starting with a letter or digit)"
        )
    return value


def normalize_role_name(value: str) -> str:
    value = value.strip().upper()
    if not _ROLE_NAME_PATTERN.fullmatch(value):
        raise ValueError(
            "Role names must be 2-50 uppercase letters/digits/underscores "
            "and start with a letter"
        )
    return value
