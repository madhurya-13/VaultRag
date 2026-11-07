"""Shared security vocabulary for RBAC, ABAC and document authorization.

Keeping these values in one module prevents role names, permission strings and
classification levels from drifting between routes, services, models and tests.
"""

from typing import Final

# ---------------------------------------------------------------------------
# Roles and capabilities (RBAC)
# ---------------------------------------------------------------------------

ROLE_ADMIN: Final = "ADMIN"
ROLE_HR: Final = "HR"
ROLE_FINANCE: Final = "FINANCE"
ROLE_ENGINEERING: Final = "ENGINEERING"
ROLE_EMPLOYEE: Final = "EMPLOYEE"

DEFAULT_ROLE_NAMES: Final[frozenset[str]] = frozenset(
    {ROLE_ADMIN, ROLE_HR, ROLE_FINANCE, ROLE_ENGINEERING, ROLE_EMPLOYEE}
)

CAP_MANAGE_USERS: Final = "MANAGE_USERS"
CAP_MANAGE_ROLES: Final = "MANAGE_ROLES"
CAP_CREATE_DOCUMENT: Final = "CREATE_DOCUMENT"
CAP_VIEW_AUDIT_LOGS: Final = "VIEW_AUDIT_LOGS"
CAP_MANAGE_ATTRIBUTES: Final = "MANAGE_ATTRIBUTES"

# Non-document capabilities. Document access for non-admin roles is always
# driven by document_permissions rows, never by an implicit role capability.
ROLE_CAPABILITIES: Final[dict[str, frozenset[str]]] = {
    ROLE_ADMIN: frozenset(
        {
            CAP_MANAGE_USERS,
            CAP_MANAGE_ROLES,
            CAP_CREATE_DOCUMENT,
            CAP_VIEW_AUDIT_LOGS,
            CAP_MANAGE_ATTRIBUTES,
        }
    ),
    ROLE_HR: frozenset(),
    ROLE_FINANCE: frozenset(),
    ROLE_ENGINEERING: frozenset(),
    ROLE_EMPLOYEE: frozenset(),
}

# ---------------------------------------------------------------------------
# Documents and permissions
# ---------------------------------------------------------------------------

CLASSIFICATION_PUBLIC: Final = "PUBLIC"
CLASSIFICATION_INTERNAL: Final = "INTERNAL"
CLASSIFICATION_CONFIDENTIAL: Final = "CONFIDENTIAL"
CLASSIFICATION_RESTRICTED: Final = "RESTRICTED"

CLASSIFICATIONS: Final[frozenset[str]] = frozenset(
    {
        CLASSIFICATION_PUBLIC,
        CLASSIFICATION_INTERNAL,
        CLASSIFICATION_CONFIDENTIAL,
        CLASSIFICATION_RESTRICTED,
    }
)

CLASSIFICATION_CLEARANCE_FLOOR: Final[dict[str, int]] = {
    CLASSIFICATION_PUBLIC: 0,
    CLASSIFICATION_INTERNAL: 1,
    CLASSIFICATION_CONFIDENTIAL: 3,
    CLASSIFICATION_RESTRICTED: 4,
}

PERMISSION_READ: Final = "READ"
PERMISSION_UPDATE: Final = "UPDATE"
PERMISSION_DELETE: Final = "DELETE"
PERMISSION_MANAGE: Final = "MANAGE_PERMISSIONS"

PERMISSION_TYPES: Final[frozenset[str]] = frozenset(
    {PERMISSION_READ, PERMISSION_UPDATE, PERMISSION_DELETE, PERMISSION_MANAGE}
)

# ADMIN has an implicit tenant-wide document capability. Other roles must hold
# an explicit document_permissions row. Exact-action matching is intentional:
# MANAGE_PERMISSIONS does not imply READ, UPDATE or DELETE.
ADMIN_DOCUMENT_ACTIONS: Final[frozenset[str]] = PERMISSION_TYPES

MAX_CLEARANCE_LEVEL: Final = 5

# ---------------------------------------------------------------------------
# ABAC attributes
# ---------------------------------------------------------------------------

ATTR_DEPARTMENT: Final = "department"
ATTR_CLEARANCE_LEVEL: Final = "clearance_level"
ATTR_EMPLOYMENT_STATUS: Final = "employment_status"
ATTR_LOCATION: Final = "location"
ATTR_DESIGNATION: Final = "designation"

ATTRIBUTE_NAMES: Final[frozenset[str]] = frozenset(
    {
        ATTR_DEPARTMENT,
        ATTR_CLEARANCE_LEVEL,
        ATTR_EMPLOYMENT_STATUS,
        ATTR_LOCATION,
        ATTR_DESIGNATION,
    }
)

REQUIRED_ATTRIBUTES: Final[frozenset[str]] = frozenset(
    {ATTR_DEPARTMENT, ATTR_CLEARANCE_LEVEL, ATTR_EMPLOYMENT_STATUS}
)

# Keep departments deliberately small for this milestone. Extend this set when
# an organization needs a new department; document values are validated against
# it before storage.
ALLOWED_DEPARTMENTS: Final[frozenset[str]] = frozenset(
    {
        "ADMIN",
        "ENGINEERING",
        "FINANCE",
        "HR",
        "IT",
        "LEGAL",
        "OPERATIONS",
        "SALES",
        "SECURITY",
    }
)

EMPLOYMENT_ACTIVE: Final = "ACTIVE"
EMPLOYMENT_STATUSES: Final[frozenset[str]] = frozenset(
    {EMPLOYMENT_ACTIVE, "INACTIVE", "SUSPENDED", "TERMINATED"}
)

# ---------------------------------------------------------------------------
# Policy identifiers used in authorization decisions and audit rows
# ---------------------------------------------------------------------------

POLICY_IDENTITY: Final = "IDENTITY_POLICY"
POLICY_ORGANIZATION_ACTIVE: Final = "ORGANIZATION_ACTIVE_POLICY"
POLICY_TENANT_ISOLATION: Final = "TENANT_ISOLATION_POLICY"
POLICY_DOCUMENT_ACTIVE: Final = "DOCUMENT_ACTIVE_POLICY"
POLICY_RBAC: Final = "RBAC_POLICY"
POLICY_ACTION: Final = "ACTION_POLICY"
POLICY_ATTRIBUTE_VALIDATION: Final = "ATTRIBUTE_VALIDATION_POLICY"
POLICY_EMPLOYMENT_STATUS: Final = "EMPLOYMENT_STATUS_POLICY"
POLICY_DEPARTMENT: Final = "DEPARTMENT_POLICY"
POLICY_CLEARANCE: Final = "CLEARANCE_POLICY"
POLICY_PERMISSION_VALIDITY: Final = "PERMISSION_VALIDITY_POLICY"
POLICY_RESOURCE_NOT_FOUND: Final = "RESOURCE_NOT_FOUND_POLICY"
POLICY_ALLOW: Final = "ALLOW"
