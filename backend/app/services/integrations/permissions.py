"""Mirror external document permissions into VaultRAG grants.

Rules
-----
* Only ``EXTERNAL_MIRROR`` integrations change document permissions. The other
  modes leave VaultRAG's manual grants untouched.
* A principal is resolved only through an explicit, active
  ``integration_identity_mappings`` row. There is no automatic email or name
  matching: an unmapped principal yields no grant.
* A provider role of ``UNKNOWN`` never becomes READ.
* Only grants with ``permission_origin = 'EXTERNAL'`` and this integration's id
  are revoked. A manual grant is never touched, even when it targets the same
  subject.
* Every change is audited without a principal name, email or document text.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.request_context import RequestMeta
from app.core.security_constants import (
    EXTERNAL_PERMISSION_STATE_NOT_APPLICABLE,
    EXTERNAL_PERMISSION_STATE_RESOLVED,
    EXTERNAL_PERMISSION_STATE_UNRESOLVED,
    PERMISSION_ORIGIN_EXTERNAL,
    PERMISSION_READ,
    PERMISSION_SYNC_MODE_MIRROR,
    POLICY_INTEGRATION_PERMISSION,
)
from app.integrations.base import ExternalRole, NormalizedPermission
from app.integrations.models import (
    ExternalDocument,
    Integration,
    IntegrationIdentityMapping,
)
from app.models import Document, DocumentPermission
from app.services.security_audit import log_security_event

logger = logging.getLogger(__name__)

_READ_ROLES = frozenset({ExternalRole.OWNER, ExternalRole.WRITER, ExternalRole.READER})

PERMISSION_REASONS = {
    "DOCUMENT_READ": "External grants mirrored as READ; write roles do not grant "
    "UPDATE or DELETE inside VaultRAG.",
}


@dataclass(frozen=True, slots=True)
class PermissionSyncResult:
    resolved: int = 0
    unresolved: int = 0
    granted: int = 0
    revoked: int = 0
    applicable: bool = True


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _mapping_key(principal_type: str, principal_id: str) -> tuple[str, str]:
    return principal_type.upper(), principal_id.strip().lower()


def _load_mappings(
    db: Session, *, integration: Integration
) -> dict[tuple[str, str], tuple[UUID | None, UUID | None]]:
    rows = db.scalars(
        select(IntegrationIdentityMapping).where(
            IntegrationIdentityMapping.tenant_id == integration.tenant_id,
            IntegrationIdentityMapping.integration_id == integration.integration_id,
            IntegrationIdentityMapping.is_active.is_(True),
        )
    ).all()
    return {
        _mapping_key(row.external_principal_type, row.external_principal_id): (
            row.user_id,
            row.role_id,
        )
        for row in rows
    }


def _desired_subjects(
    permissions: tuple[NormalizedPermission, ...],
    mappings: dict[tuple[str, str], tuple[UUID | None, UUID | None]],
) -> tuple[set[tuple[str, UUID]], int]:
    desired: set[tuple[str, UUID]] = set()
    unresolved = 0
    for permission in permissions:
        if permission.role is ExternalRole.UNKNOWN or permission.role not in _READ_ROLES:
            # COMMENTER and UNKNOWN never grant document READ.
            unresolved += 1
            continue
        target = mappings.get(
            _mapping_key(permission.principal_type.value, permission.principal_id)
        )
        if target is None:
            unresolved += 1
            continue
        user_id, role_id = target
        if user_id is not None:
            desired.add(("user", user_id))
        elif role_id is not None:
            desired.add(("role", role_id))
    return desired, unresolved


def mirror_document_permissions(
    db: Session,
    *,
    integration: Integration,
    document: Document,
    external_document: ExternalDocument,
    permissions: tuple[NormalizedPermission, ...],
    actor_user_id: UUID,
    request_meta: RequestMeta | None = None,
) -> PermissionSyncResult:
    """Apply one document's external ACL, revoking only stale mirrored grants."""
    if integration.permission_sync_mode != PERMISSION_SYNC_MODE_MIRROR:
        external_document.permission_state = (
            EXTERNAL_PERMISSION_STATE_NOT_APPLICABLE
        )
        return PermissionSyncResult(applicable=False)

    mappings = _load_mappings(db, integration=integration)
    desired, unresolved = _desired_subjects(permissions, mappings)

    active_external = list(
        db.scalars(
            select(DocumentPermission).where(
                DocumentPermission.tenant_id == integration.tenant_id,
                DocumentPermission.document_id == document.document_id,
                DocumentPermission.permission_origin == PERMISSION_ORIGIN_EXTERNAL,
                DocumentPermission.managed_by_integration_id
                == integration.integration_id,
                DocumentPermission.is_active.is_(True),
            )
        ).all()
    )
    active_keys = {
        (
            "user" if row.user_id is not None else "role",
            row.user_id or row.role_id,
        )
        for row in active_external
    }
    revoked = 0
    for row in active_external:
        key = (
            "user" if row.user_id is not None else "role",
            row.user_id or row.role_id,
        )
        if key not in desired:
            row.is_active = False
            row.revoked_at = _utcnow()
            revoked += 1

    # A previously revoked mirrored grant is revived rather than duplicated;
    # the partial unique index only covers active rows.
    history = list(
        db.scalars(
            select(DocumentPermission).where(
                DocumentPermission.tenant_id == integration.tenant_id,
                DocumentPermission.document_id == document.document_id,
                DocumentPermission.permission_origin == PERMISSION_ORIGIN_EXTERNAL,
                DocumentPermission.managed_by_integration_id
                == integration.integration_id,
                DocumentPermission.is_active.is_(False),
            )
        ).all()
    )
    history_by_key = {
        (
            "user" if row.user_id is not None else "role",
            row.user_id or row.role_id,
        ): row
        for row in history
    }

    granted = 0
    for subject_type, subject_id in sorted(
        desired - active_keys, key=lambda item: (item[0], str(item[1]))
    ):
        existing = history_by_key.get((subject_type, subject_id))
        if existing is not None:
            existing.is_active = True
            existing.revoked_at = None
            existing.granted_by = actor_user_id
            existing.granted_at = _utcnow()
        else:
            db.add(
                DocumentPermission(
                    tenant_id=integration.tenant_id,
                    document_id=document.document_id,
                    user_id=subject_id if subject_type == "user" else None,
                    role_id=subject_id if subject_type == "role" else None,
                    permission_type=PERMISSION_READ,
                    permission_origin=PERMISSION_ORIGIN_EXTERNAL,
                    managed_by_integration_id=integration.integration_id,
                    granted_by=actor_user_id,
                    is_active=True,
                    cross_department_allowed=False,
                    expires_at=None,
                )
            )
        granted += 1

    external_document.permission_state = (
        EXTERNAL_PERMISSION_STATE_RESOLVED
        if desired
        else EXTERNAL_PERMISSION_STATE_UNRESOLVED
    )
    db.flush()

    if granted or revoked:
        log_security_event(
            db,
            tenant_id=integration.tenant_id,
            user_id=actor_user_id,
            resource_type="DOCUMENT_PERMISSION",
            resource_id=document.document_id,
            action="INTEGRATION_PERMISSION_SYNC",
            decision="ALLOW",
            reason=(
                f"granted={granted};revoked={revoked};unresolved={unresolved};"
                "external_permissions_only"
            ),
            policy=POLICY_INTEGRATION_PERMISSION,
            request_meta=request_meta,
        )
    return PermissionSyncResult(
        resolved=len(desired),
        unresolved=unresolved,
        granted=granted,
        revoked=revoked,
    )


def revoke_all_external_permissions(
    db: Session,
    *,
    integration: Integration,
    actor_user_id: UUID,
    request_meta: RequestMeta | None = None,
) -> int:
    """Revoke every active mirrored grant when an integration is disconnected."""
    rows = list(
        db.scalars(
            select(DocumentPermission).where(
                DocumentPermission.tenant_id == integration.tenant_id,
                DocumentPermission.permission_origin == PERMISSION_ORIGIN_EXTERNAL,
                DocumentPermission.managed_by_integration_id
                == integration.integration_id,
                DocumentPermission.is_active.is_(True),
            )
        ).all()
    )
    now = _utcnow()
    for row in rows:
        row.is_active = False
        row.revoked_at = now
    if rows:
        log_security_event(
            db,
            tenant_id=integration.tenant_id,
            user_id=actor_user_id,
            resource_type="INTEGRATION",
            resource_id=integration.integration_id,
            action="INTEGRATION_PERMISSION_REVOKE_ALL",
            decision="ALLOW",
            reason=f"revoked={len(rows)}",
            policy=POLICY_INTEGRATION_PERMISSION,
            request_meta=request_meta,
        )
    return len(rows)
