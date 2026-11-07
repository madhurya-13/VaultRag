"""Attribute-based access control (ABAC) policy evaluation.

ABAC answers a different question from RBAC and document permissions:

* RBAC: does the caller hold a capability such as "manage users"?
* Document permissions: was this user/role granted this exact action on this
  document?
* ABAC: is this user, right now, allowed to touch a document with these
  attributes? Department, clearance and employment status are evaluated here.

All three must pass. Missing, malformed or unrecognized attributes fail closed.
"""

from dataclasses import dataclass
from typing import Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestError
from app.core.security_constants import (
    ALLOWED_DEPARTMENTS,
    ATTR_CLEARANCE_LEVEL,
    ATTR_DEPARTMENT,
    ATTR_DESIGNATION,
    ATTR_EMPLOYMENT_STATUS,
    ATTR_LOCATION,
    ATTRIBUTE_NAMES,
    CLASSIFICATION_CLEARANCE_FLOOR,
    CLASSIFICATIONS,
    EMPLOYMENT_ACTIVE,
    EMPLOYMENT_STATUSES,
    MAX_CLEARANCE_LEVEL,
    POLICY_ATTRIBUTE_VALIDATION,
    POLICY_CLEARANCE,
    POLICY_DEPARTMENT,
    POLICY_DOCUMENT_ACTIVE,
    POLICY_EMPLOYMENT_STATUS,
    REQUIRED_ATTRIBUTES,
)
from app.models import Document, UserAttribute


@dataclass(frozen=True, slots=True)
class UserAttributes:
    department: str
    clearance_level: int
    employment_status: str
    location: str | None = None
    designation: str | None = None


@dataclass(frozen=True, slots=True)
class AbacDecision:
    allowed: bool
    reason: str
    policy: str


def normalize_department(value: str) -> str:
    return value.strip().upper()


def normalize_classification(value: str) -> str:
    return value.strip().upper()


def load_user_attribute_map(
    db: Session, tenant_id, user_id
) -> dict[str, str]:
    """Load raw ABAC attributes for one user in one tenant."""
    rows = db.scalars(
        select(UserAttribute).where(
            UserAttribute.tenant_id == tenant_id,
            UserAttribute.user_id == user_id,
        )
    ).all()
    return {row.attribute_name: row.attribute_value for row in rows}


def _deny(reason: str, policy: str) -> AbacDecision:
    return AbacDecision(allowed=False, reason=reason, policy=policy)


def parse_user_attributes(
    raw: Mapping[str, str],
) -> tuple[UserAttributes | None, AbacDecision | None]:
    """Validate and parse raw attributes, returning a safe denial on failure."""
    missing = sorted(REQUIRED_ATTRIBUTES - set(raw))
    if missing:
        return None, _deny(
            "Missing required ABAC attribute(s): " + ", ".join(missing),
            POLICY_ATTRIBUTE_VALIDATION,
        )

    department = normalize_department(raw[ATTR_DEPARTMENT])
    if department not in ALLOWED_DEPARTMENTS:
        return None, _deny(
            "User department is not recognized",
            POLICY_ATTRIBUTE_VALIDATION,
        )

    clearance_raw = raw[ATTR_CLEARANCE_LEVEL].strip()
    if not clearance_raw.isdigit():
        return None, _deny(
            "User clearance level is not a valid integer",
            POLICY_ATTRIBUTE_VALIDATION,
        )
    clearance = int(clearance_raw)
    if not 0 <= clearance <= MAX_CLEARANCE_LEVEL:
        return None, _deny(
            "User clearance level is outside the allowed range",
            POLICY_ATTRIBUTE_VALIDATION,
        )

    employment_status = raw[ATTR_EMPLOYMENT_STATUS].strip().upper()
    if employment_status not in EMPLOYMENT_STATUSES:
        return None, _deny(
            "User employment status is not recognized",
            POLICY_ATTRIBUTE_VALIDATION,
        )

    return (
        UserAttributes(
            department=department,
            clearance_level=clearance,
            employment_status=employment_status,
            location=raw.get(ATTR_LOCATION),
            designation=raw.get(ATTR_DESIGNATION),
        ),
        None,
    )


def evaluate_abac(
    raw_attributes: Mapping[str, str],
    document: Document,
    *,
    cross_department_allowed: bool = False,
    is_admin: bool = False,
) -> AbacDecision:
    """Evaluate the document-level ABAC policies.

    Policy order is intentional: document state, identity attributes,
    employment status, department, then clearance. The first failure is the
    auditable reason.
    """
    if not document.is_active:
        return _deny("Document is inactive", POLICY_DOCUMENT_ACTIVE)

    attributes, parse_denial = parse_user_attributes(raw_attributes)
    if parse_denial is not None:
        return parse_denial
    assert attributes is not None

    if attributes.employment_status != EMPLOYMENT_ACTIVE:
        return _deny(
            "User is not in ACTIVE employment status",
            POLICY_EMPLOYMENT_STATUS,
        )

    document_department = normalize_department(document.department)
    if document_department not in ALLOWED_DEPARTMENTS:
        # Fail closed if a document somehow contains an unrecognized
        # department, even if it was inserted by a script.
        return _deny(
            "Document department is not recognized",
            POLICY_ATTRIBUTE_VALIDATION,
        )

    if (
        attributes.department != document_department
        and not cross_department_allowed
        and not is_admin
    ):
        return _deny(
            "User department does not match the document department",
            POLICY_DEPARTMENT,
        )

    if attributes.clearance_level < document.required_clearance_level:
        return _deny(
            "User clearance level is below the document requirement",
            POLICY_CLEARANCE,
        )

    return AbacDecision(
        allowed=True,
        reason="All ABAC conditions satisfied",
        policy="ABAC",
    )


def validate_document_attributes(
    *,
    department: str,
    classification: str,
    required_clearance_level: int | None,
) -> tuple[str, str, int]:
    """Validate document attributes before insert and apply the clearance floor."""
    normalized_department = normalize_department(department)
    if normalized_department not in ALLOWED_DEPARTMENTS:
        raise BadRequestError("Unknown document department")

    normalized_classification = normalize_classification(classification)
    if normalized_classification not in CLASSIFICATIONS:
        raise BadRequestError("Unknown document classification")

    floor = CLASSIFICATION_CLEARANCE_FLOOR[normalized_classification]
    if required_clearance_level is None:
        level = floor
    else:
        if not 0 <= required_clearance_level <= MAX_CLEARANCE_LEVEL:
            raise BadRequestError("required_clearance_level must be between 0 and 5")
        if required_clearance_level < floor:
            raise BadRequestError(
                "required_clearance_level cannot be below the classification floor"
            )
        level = required_clearance_level

    return normalized_department, normalized_classification, level


def validate_attribute_value(name: str, value: str) -> str:
    """Validate and normalize one user attribute value for storage."""
    if name not in ATTRIBUTE_NAMES:
        raise BadRequestError(f"Unknown ABAC attribute: {name}")

    normalized = value.strip()

    if name == ATTR_CLEARANCE_LEVEL:
        if not normalized.isdigit():
            raise BadRequestError("clearance_level must be an integer from 0 to 5")
        level = int(normalized)
        if not 0 <= level <= MAX_CLEARANCE_LEVEL:
            raise BadRequestError("clearance_level must be between 0 and 5")
        return str(level)

    if name == ATTR_DEPARTMENT:
        department = normalize_department(normalized)
        if department not in ALLOWED_DEPARTMENTS:
            raise BadRequestError("Unknown department")
        return department

    if name == ATTR_EMPLOYMENT_STATUS:
        status = normalized.upper()
        if status not in EMPLOYMENT_STATUSES:
            raise BadRequestError("Unknown employment status")
        return status

    if name in {ATTR_LOCATION, ATTR_DESIGNATION}:
        if not normalized:
            raise BadRequestError(f"{name} cannot be blank")
        if len(normalized) > 255:
            raise BadRequestError(f"{name} is too long")
        return normalized

    raise BadRequestError(f"Unknown ABAC attribute: {name}")
