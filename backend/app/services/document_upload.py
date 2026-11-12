"""Secure PDF upload and initial permission assignment.

The uploader identity, tenant, hash, storage path, processing status and
permission snapshot are all derived server-side. Client form fields are limited
to document name, department and classification.

The service commits its own short transaction because the private file write and
the database row must agree: a failed commit removes the final file, and a
failed file finalisation rolls back the database.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import BinaryIO
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import set_tenant_context
from app.core.exceptions import (
    ConflictError,
    ServiceUnavailableError,
    UnprocessableDocumentError,
    UnsupportedMediaTypeError,
)
from app.core.request_context import RequestMeta
from app.core.security_constants import (
    DOC_STATUS_UPLOADED,
    DOCUMENT_TYPE_PDF,
    PERMISSION_MANAGE,
    PERMISSION_READ,
    POLICY_ALLOW,
    ROLE_ADMIN,
)
from app.dependencies.authentication import AuthenticatedContext
from app.models import Document, DocumentPermission, Role
from app.services.abac import validate_document_attributes
from app.services.document_processor import (
    DocumentProcessingError,
    extract_pdf_text,
)
from app.services.security_audit import log_security_event
from app.services.storage import PrivateStorage, get_private_storage

logger = logging.getLogger(__name__)


def _grant_initial_permission(
    db: Session,
    *,
    tenant_id: UUID,
    document_id: UUID,
    actor_user_id: UUID,
    role: Role,
    permission_type: str,
    request_meta: RequestMeta,
) -> DocumentPermission | None:
    """Insert one initial grant inside a savepoint, skipping duplicates."""
    existing = db.scalar(
        select(DocumentPermission).where(
            DocumentPermission.tenant_id == tenant_id,
            DocumentPermission.document_id == document_id,
            DocumentPermission.role_id == role.role_id,
            DocumentPermission.permission_type == permission_type,
            DocumentPermission.is_active.is_(True),
        )
    )
    if existing is not None:
        return None

    permission = DocumentPermission(
        tenant_id=tenant_id,
        document_id=document_id,
        role_id=role.role_id,
        permission_type=permission_type,
        granted_by=actor_user_id,
        is_active=True,
        cross_department_allowed=False,
    )
    try:
        with db.begin_nested():
            db.add(permission)
            db.flush()
    except IntegrityError:
        # The partial unique index is the final duplicate guard. A savepoint
        # rollback leaves the document transaction intact.
        return None

    log_security_event(
        db,
        tenant_id=tenant_id,
        user_id=actor_user_id,
        resource_type="DOCUMENT_PERMISSION",
        resource_id=permission.permission_id,
        action="PERMISSION_GRANT",
        decision="ALLOW",
        reason=f"Initial {permission_type} grant to role {role.role_name}",
        policy=POLICY_ALLOW,
        request_meta=request_meta,
    )
    return permission


def assign_initial_permissions(
    db: Session,
    *,
    tenant_id: UUID,
    document_id: UUID,
    actor_user_id: UUID,
    department: str,
    request_meta: RequestMeta,
) -> tuple[DocumentPermission, ...]:
    """Assign server-derived initial grants.

    ADMIN receives READ and MANAGE_PERMISSIONS. The document's department role
    receives READ when that tenant role exists. No direct uploader grant is
    created. Nothing is accepted from the client beyond the validated
    department.
    """
    roles = {
        role.role_name: role
        for role in db.scalars(
            select(Role).where(
                Role.tenant_id == tenant_id,
                Role.role_name.in_({ROLE_ADMIN, department}),
            )
        ).all()
    }

    grants: list[DocumentPermission] = []
    admin_role = roles.get(ROLE_ADMIN)
    if admin_role is not None:
        for permission_type in (PERMISSION_READ, PERMISSION_MANAGE):
            permission = _grant_initial_permission(
                db,
                tenant_id=tenant_id,
                document_id=document_id,
                actor_user_id=actor_user_id,
                role=admin_role,
                permission_type=permission_type,
                request_meta=request_meta,
            )
            if permission is not None:
                grants.append(permission)

    department_role = roles.get(department)
    if (
        department_role is not None
        and department != ROLE_ADMIN
        and department_role.role_id != (admin_role.role_id if admin_role else None)
    ):
        permission = _grant_initial_permission(
            db,
            tenant_id=tenant_id,
            document_id=document_id,
            actor_user_id=actor_user_id,
            role=department_role,
            permission_type=PERMISSION_READ,
            request_meta=request_meta,
        )
        if permission is not None:
            grants.append(permission)

    return tuple(grants)


def upload_document(
    db: Session,
    context: AuthenticatedContext,
    *,
    stream: BinaryIO,
    original_filename: str | None,
    content_type: str | None,
    document_name: str,
    department: str,
    classification: str,
    request_meta: RequestMeta,
    storage: PrivateStorage | None = None,
) -> Document:
    """Validate, store and register a PDF owned by the authenticated tenant."""
    normalized_department, normalized_classification, clearance = (
        validate_document_attributes(
            department=department,
            classification=classification,
            required_clearance_level=None,
        )
    )
    if not document_name.strip():
        raise UnprocessableDocumentError("document_name must not be blank")
    if len(document_name.strip()) > 255:
        raise UnprocessableDocumentError("document_name is too long")

    if not original_filename:
        raise UnsupportedMediaTypeError("A .pdf filename is required")
    if Path(original_filename).suffix.lower() != ".pdf":
        raise UnsupportedMediaTypeError("Only PDF files are accepted")
    if content_type and content_type.lower() not in {
        "application/pdf",
        "application/x-pdf",
        "application/octet-stream",
        "binary/octet-stream",
    }:
        raise UnsupportedMediaTypeError("Only PDF files are accepted")

    backend = storage or get_private_storage()
    set_tenant_context(db, context.tenant_id)

    final_key: str | None = None
    stored = backend.save_stream(
        stream,
        original_filename=original_filename,
        max_bytes=get_settings().max_upload_size_bytes,
    )

    try:
        if stored.size == 0:
            raise UnsupportedMediaTypeError("The uploaded file is empty")

        try:
            extract_pdf_text(
                stored.temp_path,
                min_extractable_chars=get_settings().pdf_min_extractable_chars,
                max_pages=get_settings().max_pdf_pages,
            )
        except DocumentProcessingError as exc:
            raise UnprocessableDocumentError(exc.safe_message) from exc

        duplicate = db.scalar(
            select(Document).where(
                Document.tenant_id == context.tenant_id,
                Document.file_hash == stored.sha256,
            )
        )
        if duplicate is not None:
            raise ConflictError(
                "A document with the same content already exists in this organization"
            )

        document_id = uuid4()
        final_key = backend.build_key(context.tenant_id, document_id)
        document = Document(
            document_id=document_id,
            tenant_id=context.tenant_id,
            document_name=document_name.strip(),
            document_type=DOCUMENT_TYPE_PDF,
            department=normalized_department,
            classification=normalized_classification,
            required_clearance_level=clearance,
            uploaded_by=context.user.user_id,
            file_hash=stored.sha256,
            storage_path=final_key,
            file_size=stored.size,
            processing_status=DOC_STATUS_UPLOADED,
            total_pages=0,
            total_chunks=0,
            embedding_model=None,
            is_active=True,
        )
        db.add(document)
        db.flush()
        db.refresh(document)

        assign_initial_permissions(
            db,
            tenant_id=context.tenant_id,
            document_id=document.document_id,
            actor_user_id=context.user.user_id,
            department=normalized_department,
            request_meta=request_meta,
        )

        log_security_event(
            db,
            tenant_id=context.tenant_id,
            user_id=context.user.user_id,
            resource_type="DOCUMENT",
            resource_id=document.document_id,
            action="UPLOAD",
            decision="ALLOW",
            reason="PDF uploaded to private storage and queued for processing",
            policy=POLICY_ALLOW,
            request_meta=request_meta,
        )

        try:
            backend.finalize(stored.temp_path, final_key)
        except Exception as exc:
            logger.exception(
                "Failed to finalize private storage document_id=%s",
                document.document_id,
            )
            raise ServiceUnavailableError("Private document storage failed") from exc

        try:
            db.commit()
        except Exception:
            db.rollback()
            if final_key is not None:
                backend.delete_key(final_key)
            raise
        return document
    except Exception:
        db.rollback()
        if final_key is not None:
            backend.delete_key(final_key)
        raise
    finally:
        if stored is not None:
            backend.delete_path(stored.temp_path)
