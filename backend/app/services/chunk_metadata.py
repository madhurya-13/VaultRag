"""Trusted security metadata snapshots for document chunks.

The snapshot is derived from the document row and the current active READ
grants in ``document_permissions``. It is stored in JSONB for observability and
future retrieval optimisation only. It is not an authorization source of truth:
future retrieval must recheck the current database permissions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.core.security_constants import PERMISSION_READ
from app.models import Document, DocumentPermission, Role


@dataclass(frozen=True, slots=True)
class PermissionSnapshot:
    allowed_roles: tuple[str, ...]
    allowed_role_ids: tuple[UUID, ...]
    allowed_user_ids: tuple[UUID, ...]
    snapshot_at: datetime


def load_permission_snapshot(
    db: Session,
    *,
    tenant_id: UUID,
    document_id: UUID,
) -> PermissionSnapshot:
    """Load active, unexpired READ grants for one document.

    Role names are included for readable snapshots; role IDs and direct user
    IDs are included so the snapshot remains precise if a role is renamed.
    """
    rows = db.execute(
        select(DocumentPermission, Role.role_name)
        .outerjoin(
            Role,
            and_(
                Role.tenant_id == DocumentPermission.tenant_id,
                Role.role_id == DocumentPermission.role_id,
            ),
        )
        .where(
            DocumentPermission.tenant_id == tenant_id,
            DocumentPermission.document_id == document_id,
            DocumentPermission.permission_type == PERMISSION_READ,
            DocumentPermission.is_active.is_(True),
            or_(
                DocumentPermission.expires_at.is_(None),
                DocumentPermission.expires_at > func.now(),
            ),
        )
    ).all()

    role_names: set[str] = set()
    role_ids: set[UUID] = set()
    user_ids: set[UUID] = set()
    for permission, role_name in rows:
        if permission.role_id is not None:
            role_ids.add(permission.role_id)
            if role_name:
                role_names.add(role_name)
        if permission.user_id is not None:
            user_ids.add(permission.user_id)

    return PermissionSnapshot(
        allowed_roles=tuple(sorted(role_names)),
        allowed_role_ids=tuple(sorted(role_ids, key=str)),
        allowed_user_ids=tuple(sorted(user_ids, key=str)),
        snapshot_at=datetime.now(timezone.utc),
    )


def build_chunk_metadata(
    document: Document,
    snapshot: PermissionSnapshot,
    *,
    embedding_model: str,
    embedding_model_version: str,
) -> dict:
    """Build the JSONB snapshot attached to every chunk."""
    return {
        "tenant_id": str(document.tenant_id),
        "document_id": str(document.document_id),
        "department": document.department,
        "classification": document.classification,
        "required_clearance_level": document.required_clearance_level,
        "allowed_roles": list(snapshot.allowed_roles),
        "allowed_role_ids": [str(value) for value in snapshot.allowed_role_ids],
        "allowed_user_ids": [str(value) for value in snapshot.allowed_user_ids],
        "permission_snapshot_at": snapshot.snapshot_at.isoformat(),
        "embedding_model": embedding_model,
        "embedding_model_version": embedding_model_version,
    }


def validate_metadata_against_document(
    metadata: dict,
    document: Document,
    snapshot: PermissionSnapshot,
) -> None:
    """Raise ValueError when a chunk snapshot disagrees with authoritative data."""
    expected = {
        "tenant_id": str(document.tenant_id),
        "document_id": str(document.document_id),
        "department": document.department,
        "classification": document.classification,
        "required_clearance_level": document.required_clearance_level,
        "allowed_roles": list(snapshot.allowed_roles),
        "allowed_role_ids": [str(value) for value in snapshot.allowed_role_ids],
        "allowed_user_ids": [str(value) for value in snapshot.allowed_user_ids],
    }
    for key, value in expected.items():
        if metadata.get(key) != value:
            raise ValueError(f"Chunk metadata field {key!r} is inconsistent")
