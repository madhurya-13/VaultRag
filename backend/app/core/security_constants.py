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
CAP_PROCESS_DOCUMENT: Final = "PROCESS_DOCUMENT"
CAP_VIEW_CHUNKS: Final = "VIEW_CHUNKS"
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
            CAP_PROCESS_DOCUMENT,
            CAP_VIEW_CHUNKS,
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
POLICY_INGESTION: Final = "INGESTION_POLICY"
POLICY_ALLOW: Final = "ALLOW"

# ---------------------------------------------------------------------------
# Milestone 3: document processing states and safe error codes
# ---------------------------------------------------------------------------

DOCUMENT_TYPE_PDF: Final = "PDF"

DOC_STATUS_UPLOADED: Final = "UPLOADED"
DOC_STATUS_PROCESSING: Final = "PROCESSING"
DOC_STATUS_COMPLETED: Final = "COMPLETED"
DOC_STATUS_FAILED: Final = "FAILED"
DOC_STATUS_ARCHIVED: Final = "ARCHIVED"

DOCUMENT_STATUSES: Final[frozenset[str]] = frozenset(
    {
        DOC_STATUS_UPLOADED,
        DOC_STATUS_PROCESSING,
        DOC_STATUS_COMPLETED,
        DOC_STATUS_FAILED,
        DOC_STATUS_ARCHIVED,
    }
)

PROCESSABLE_DOCUMENT_STATUSES: Final[frozenset[str]] = frozenset(
    {DOC_STATUS_UPLOADED, DOC_STATUS_FAILED}
)

INGESTION_STATUS_PENDING: Final = "PENDING"
INGESTION_STATUS_PROCESSING: Final = "PROCESSING"
INGESTION_STATUS_COMPLETED: Final = "COMPLETED"
INGESTION_STATUS_FAILED: Final = "FAILED"

INGESTION_STATUSES: Final[frozenset[str]] = frozenset(
    {
        INGESTION_STATUS_PENDING,
        INGESTION_STATUS_PROCESSING,
        INGESTION_STATUS_COMPLETED,
        INGESTION_STATUS_FAILED,
    }
)

ERROR_INVALID_FILE_TYPE: Final = "INVALID_FILE_TYPE"
ERROR_FILE_TOO_LARGE: Final = "FILE_TOO_LARGE"
ERROR_MALFORMED_PDF: Final = "MALFORMED_PDF"
ERROR_ENCRYPTED_PDF: Final = "ENCRYPTED_PDF_UNSUPPORTED"
ERROR_DUPLICATE_DOCUMENT: Final = "DUPLICATE_DOCUMENT"
ERROR_STORAGE_FAILURE: Final = "STORAGE_FAILURE"
ERROR_DOCUMENT_HAS_NO_FILE: Final = "DOCUMENT_HAS_NO_FILE"
ERROR_DOCUMENT_NOT_PROCESSABLE: Final = "DOCUMENT_NOT_PROCESSABLE"
ERROR_INGESTION_ALREADY_RUNNING: Final = "INGESTION_ALREADY_RUNNING"
ERROR_PDF_EXTRACTION_FAILED: Final = "PDF_EXTRACTION_FAILED"
ERROR_NO_EXTRACTABLE_TEXT: Final = "NO_EXTRACTABLE_TEXT"
ERROR_OCR_UNSUPPORTED: Final = "OCR_UNSUPPORTED"
ERROR_TEXT_CLEANING_FAILED: Final = "TEXT_CLEANING_FAILED"
ERROR_CHUNKING_FAILED: Final = "CHUNKING_FAILED"
ERROR_NO_CHUNKS_GENERATED: Final = "NO_CHUNKS_GENERATED"
ERROR_EMBEDDING_MODEL: Final = "EMBEDDING_MODEL_ERROR"
ERROR_INVALID_EMBEDDING: Final = "INVALID_EMBEDDING"
ERROR_PERMISSION_SNAPSHOT: Final = "PERMISSION_SNAPSHOT_FAILED"
ERROR_CHUNK_VALIDATION: Final = "CHUNK_VALIDATION_FAILED"
ERROR_CHUNK_STORAGE: Final = "CHUNK_STORAGE_FAILED"
ERROR_INGESTION_INTERNAL: Final = "INGESTION_INTERNAL_ERROR"
