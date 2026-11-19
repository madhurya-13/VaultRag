"""Import every model so SQLAlchemy's registry and Alembic see them all."""

from app.models.conversation import Conversation
from app.models.conversation_message import ConversationMessage
from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.models.document_ingestion_job import DocumentIngestionJob
from app.models.document_permission import DocumentPermission
from app.models.entity_alias import EntityAlias
from app.models.knowledge_edge import KnowledgeEdge
from app.models.knowledge_evidence import KnowledgeEvidence
from app.models.knowledge_extraction_job import KnowledgeExtractionJob
from app.models.knowledge_governance import (
    KnowledgeConflict,
    KnowledgeEntityMerge,
    KnowledgeEntityReview,
)
from app.models.knowledge_node import KnowledgeNode
from app.models.knowledge_query_analytics import KnowledgeQueryAnalytics
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
    "EntityAlias",
    "KnowledgeConflict",
    "KnowledgeEdge",
    "KnowledgeEvidence",
    "KnowledgeEntityMerge",
    "KnowledgeEntityReview",
    "KnowledgeExtractionJob",
    "KnowledgeNode",
    "KnowledgeQueryAnalytics",
    "Organization",
    "RetrievalAnalytics",
    "Role",
    "SecurityAuditLog",
    "User",
    "UserAttribute",
    "UserRole",
]
