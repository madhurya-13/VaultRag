"""Import every model so SQLAlchemy's registry and Alembic see them all."""

from app.models.conversation import Conversation
from app.models.conversation_message import ConversationMessage
from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.models.document_ingestion_job import DocumentIngestionJob
from app.models.document_permission import DocumentPermission
from app.models.organization import Organization
from app.models.retrieval_analytics import RetrievalAnalytics
from app.models.role import Role
from app.models.security_audit_log import SecurityAuditLog
from app.models.user import User
from app.models.user_attribute import UserAttribute
from app.models.user_role import UserRole

__all__ = [
    "Conversation",
    "ConversationMessage",
    "Document",
    "DocumentChunk",
    "DocumentIngestionJob",
    "DocumentPermission",
    "Organization",
    "RetrievalAnalytics",
    "Role",
    "SecurityAuditLog",
    "User",
    "UserAttribute",
    "UserRole",
]
