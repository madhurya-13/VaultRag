"""Document metadata services.

Contents, embeddings, vector search and LLM features are intentionally out of
scope for this milestone. These functions manage metadata and delegate every
access decision to ``app.services.authorization``.
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.dependencies.authentication import AuthenticatedContext
from app.models import Document
from app.services.abac import (
    load_user_attribute_map,
    validate_document_attributes,
)
from app.services.authorization import (
    authorize_loaded_document,
    load_role_ids,
)


def create_document(
    db: Session,
    context: AuthenticatedContext,
    *,
    document_name: str,
    document_type: str,
    department: str,
    classification: str,
    required_clearance_level: int | None,
) -> Document:
    """Create a document owned by the authenticated tenant and user."""
    normalized_department, normalized_classification, clearance = (
        validate_document_attributes(
            department=department,
            classification=classification,
            required_clearance_level=required_clearance_level,
        )
    )

    document = Document(
        tenant_id=context.tenant_id,
        document_name=document_name.strip(),
        document_type=document_type.strip(),
        department=normalized_department,
        classification=normalized_classification,
        required_clearance_level=clearance,
        uploaded_by=context.user.user_id,
        is_active=True,
    )
    db.add(document)
    db.flush()
    db.refresh(document)
    return document


def get_tenant_document(
    db: Session, tenant_id: UUID, document_id: UUID
) -> Document:
    document = db.scalar(
        select(Document).where(
            Document.tenant_id == tenant_id,
            Document.document_id == document_id,
        )
    )
    if document is None:
        raise NotFoundError("Document not found")
    return document


def list_accessible_documents(
    db: Session, context: AuthenticatedContext
) -> list[Document]:
    """Return documents the caller may READ, filtered by the central engine.

    Role IDs and ABAC attributes are loaded once for the whole list. Permission
    grants are still checked per document because they are document-specific.
    """
    role_ids = load_role_ids(db, context.tenant_id, context.roles)
    raw_attributes = load_user_attribute_map(
        db, context.tenant_id, context.user.user_id
    )

    candidates = list(
        db.scalars(
            select(Document)
            .where(
                Document.tenant_id == context.tenant_id,
                Document.is_active.is_(True),
            )
            .order_by(Document.created_at.desc(), Document.document_id)
        ).all()
    )

    return [
        document
        for document in candidates
        if authorize_loaded_document(
            db,
            context,
            document,
            "READ",
            role_ids=role_ids,
            raw_attributes=raw_attributes,
        ).allowed
    ]
