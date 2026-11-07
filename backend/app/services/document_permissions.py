"""Document permission grants and revocations.

Permissions are exact-match: ``MANAGE_PERMISSIONS`` does not imply ``READ``,
``UPDATE`` or ``DELETE``. Revocation is soft so the grant history survives.
"""

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.core.security_constants import PERMISSION_TYPES
from app.models import Document, DocumentPermission, Role
from app.services.users import get_tenant_user


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_permission_type(permission_type: str) -> str:
    normalized = permission_type.strip().upper()
    if normalized not in PERMISSION_TYPES:
        raise BadRequestError("Unknown document permission type")
    return normalized


def _validate_expiry(expires_at: datetime | None) -> datetime | None:
    if expires_at is None:
        return None
    if expires_at.tzinfo is None:
        raise BadRequestError("expires_at must include a timezone")
    if expires_at <= _utcnow():
        raise BadRequestError("expires_at must be in the future")
    return expires_at


def _get_document(db: Session, tenant_id: UUID, document_id: UUID) -> Document:
    document = db.scalar(
        select(Document).where(
            Document.tenant_id == tenant_id,
            Document.document_id == document_id,
        )
    )
    if document is None:
        raise NotFoundError("Document not found")
    return document


def _role_by_name(db: Session, tenant_id: UUID, role_name: str) -> Role:
    role = db.scalar(
        select(Role).where(
            Role.tenant_id == tenant_id,
            Role.role_name == role_name.strip().upper(),
        )
    )
    if role is None:
        raise NotFoundError("Role not found in this organization")
    return role


def matching_permissions(
    db: Session,
    *,
    tenant_id: UUID,
    document_id: UUID,
    permission_type: str,
    user_id: UUID,
    role_ids: set[UUID] | frozenset[UUID],
) -> list[DocumentPermission]:
    """Return all grants for this subject, including expired/revoked history."""
    subject_conditions = [DocumentPermission.user_id == user_id]
    if role_ids:
        subject_conditions.append(DocumentPermission.role_id.in_(role_ids))

    return list(
        db.scalars(
            select(DocumentPermission)
            .where(
                DocumentPermission.tenant_id == tenant_id,
                DocumentPermission.document_id == document_id,
                DocumentPermission.permission_type == permission_type,
                or_(*subject_conditions),
            )
            .order_by(DocumentPermission.granted_at.desc())
        ).all()
    )


def grant_permission(
    db: Session,
    *,
    tenant_id: UUID,
    document_id: UUID,
    actor_user_id: UUID,
    permission_type: str,
    role_name: str | None = None,
    user_id: UUID | None = None,
    expires_at: datetime | None = None,
    cross_department_allowed: bool = False,
) -> DocumentPermission:
    """Grant an exact document action to one role or one user."""
    if (role_name is None) == (user_id is None):
        raise BadRequestError("Provide exactly one of role_name or user_id")

    normalized_type = _normalize_permission_type(permission_type)
    expiry = _validate_expiry(expires_at)

    document = _get_document(db, tenant_id, document_id)
    if not document.is_active:
        raise BadRequestError("Cannot grant permissions on an inactive document")

    role_id: UUID | None = None
    if role_name is not None:
        role = _role_by_name(db, tenant_id, role_name)
        role_id = role.role_id
    else:
        assert user_id is not None
        get_tenant_user(db, tenant_id, user_id)

    existing = db.scalar(
        select(DocumentPermission).where(
            DocumentPermission.tenant_id == tenant_id,
            DocumentPermission.document_id == document_id,
            DocumentPermission.permission_type == normalized_type,
            DocumentPermission.is_active.is_(True),
            (
                DocumentPermission.role_id == role_id
                if role_id is not None
                else DocumentPermission.user_id == user_id
            ),
        )
    )

    now = _utcnow()
    if existing is not None:
        # An expired row can be retired and replaced. An unexpired one is a
        # duplicate and the database's partial unique index is the final guard.
        if existing.expires_at is not None and existing.expires_at <= now:
            existing.is_active = False
            existing.revoked_at = now
            existing.revoked_by = actor_user_id
            db.flush()
        else:
            raise ConflictError(
                "An active permission already exists for this subject and action"
            )

    permission = DocumentPermission(
        tenant_id=tenant_id,
        document_id=document_id,
        role_id=role_id,
        user_id=user_id if role_id is None else None,
        permission_type=normalized_type,
        granted_by=actor_user_id,
        expires_at=expiry,
        is_active=True,
        cross_department_allowed=cross_department_allowed,
    )
    db.add(permission)

    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(
            "An active permission already exists for this subject and action"
        ) from exc

    db.refresh(permission)
    return permission


def revoke_permission(
    db: Session,
    *,
    tenant_id: UUID,
    document_id: UUID,
    permission_id: UUID,
    actor_user_id: UUID,
) -> DocumentPermission:
    """Soft-revoke a permission. The row remains as history."""
    permission = db.scalar(
        select(DocumentPermission).where(
            DocumentPermission.tenant_id == tenant_id,
            DocumentPermission.document_id == document_id,
            DocumentPermission.permission_id == permission_id,
        )
    )
    if permission is None:
        raise NotFoundError("Permission not found")
    if not permission.is_active:
        raise ConflictError("Permission is already revoked")

    permission.is_active = False
    permission.revoked_at = _utcnow()
    permission.revoked_by = actor_user_id
    db.flush()
    db.refresh(permission)
    return permission
