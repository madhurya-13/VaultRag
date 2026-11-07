"""Import every model so SQLAlchemy's registry and Alembic see them all."""

from app.models.document import Document
from app.models.document_permission import DocumentPermission
from app.models.organization import Organization
from app.models.role import Role
from app.models.security_audit_log import SecurityAuditLog
from app.models.user import User
from app.models.user_attribute import UserAttribute
from app.models.user_role import UserRole

__all__ = [
    "Document",
    "DocumentPermission",
    "Organization",
    "Role",
    "SecurityAuditLog",
    "User",
    "UserAttribute",
    "UserRole",
]
