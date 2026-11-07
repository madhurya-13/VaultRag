"""Central document authorization engine.

Every document operation flows through this module. Routes do not inspect roles,
query ``document_permissions`` or compare clearance levels themselves. The
engine evaluates, in order:

1. trusted identity and organization state;
2. tenant isolation;
3. document active state and action validity;
4. RBAC (ADMIN implicit capability, otherwise an exact active permission);
5. ABAC (attributes, employment status, department, clearance).

The engine also records the decision in the security audit log. Allowed events
join the request transaction; denied events use an independent transaction so
they survive the request rollback.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import ForbiddenError, NotFoundError
from app.core.request_context import RequestMeta
from app.core.security_constants import (
    ADMIN_DOCUMENT_ACTIONS,
    PERMISSION_TYPES,
    POLICY_ACTION,
    POLICY_ALLOW,
    POLICY_DOCUMENT_ACTIVE,
    POLICY_IDENTITY,
    POLICY_ORGANIZATION_ACTIVE,
    POLICY_PERMISSION_VALIDITY,
    POLICY_RBAC,
    POLICY_RESOURCE_NOT_FOUND,
    POLICY_TENANT_ISOLATION,
    ROLE_ADMIN,
)
from app.dependencies.authentication import AuthenticatedContext
from app.models import Document, Role
from app.services.abac import (
    AbacDecision,
    evaluate_abac,
    load_user_attribute_map,
)
from app.services.document_permissions import matching_permissions
from app.services.security_audit import log_security_event


@dataclass(frozen=True, slots=True)
class AuthorizationDecision:
    allowed: bool
    action: str
    document_id: UUID | None
    policy: str
    reason: str

    @property
    def decision(self) -> str:
        return "ALLOW" if self.allowed else "DENY"


def _decision(
    *,
    allowed: bool,
    action: str,
    document_id: UUID | None,
    policy: str,
    reason: str,
) -> AuthorizationDecision:
    return AuthorizationDecision(
        allowed=allowed,
        action=action,
        document_id=document_id,
        policy=policy,
        reason=reason,
    )


def load_role_ids(
    db: Session, tenant_id: UUID, role_names: tuple[str, ...] | list[str]
) -> frozenset[UUID]:
    if not role_names:
        return frozenset()
    return frozenset(
        db.scalars(
            select(Role.role_id).where(
                Role.tenant_id == tenant_id,
                Role.role_name.in_(list(role_names)),
            )
        ).all()
    )


def _permission_result(
    db: Session,
    context: AuthenticatedContext,
    document: Document,
    action: str,
    role_ids: frozenset[UUID],
) -> tuple[bool, bool, str, str]:
    """Return (granted, cross_department_allowed, reason, policy)."""
    if ROLE_ADMIN in context.roles and action in ADMIN_DOCUMENT_ACTIONS:
        return (
            True,
            True,
            "ADMIN grants tenant-wide document access",
            POLICY_ALLOW,
        )

    rows = matching_permissions(
        db,
        tenant_id=context.tenant_id,
        document_id=document.document_id,
        permission_type=action,
        user_id=context.user.user_id,
        role_ids=role_ids,
    )
    now = datetime.now(timezone.utc)
    valid = [
        row
        for row in rows
        if row.is_active and (row.expires_at is None or row.expires_at > now)
    ]
    if valid:
        return (
            True,
            any(row.cross_department_allowed for row in valid),
            "An active document permission grants this action",
            POLICY_ALLOW,
        )

    if rows:
        if any(
            row.is_active
            and row.expires_at is not None
            and row.expires_at <= now
            for row in rows
        ):
            return (
                False,
                False,
                "Document permission has expired",
                POLICY_PERMISSION_VALIDITY,
            )
        if any(not row.is_active for row in rows):
            return (
                False,
                False,
                "Document permission was revoked",
                POLICY_PERMISSION_VALIDITY,
            )

    return (
        False,
        False,
        "No document permission grants this action",
        POLICY_RBAC,
    )


def authorize_loaded_document(
    db: Session,
    context: AuthenticatedContext,
    document: Document,
    action: str,
    *,
    role_ids: frozenset[UUID] | None = None,
    raw_attributes: dict[str, str] | None = None,
) -> AuthorizationDecision:
    """Authorize an already-loaded document without auditing yet."""
    normalized_action = action.strip().upper()
    document_id = document.document_id

    if normalized_action not in PERMISSION_TYPES:
        return _decision(
            allowed=False,
            action=normalized_action,
            document_id=document_id,
            policy=POLICY_ACTION,
            reason="Unknown document action",
        )

    if not context.user.is_active:
        return _decision(
            allowed=False,
            action=normalized_action,
            document_id=document_id,
            policy=POLICY_IDENTITY,
            reason="User is inactive",
        )

    if not context.organization.is_active:
        return _decision(
            allowed=False,
            action=normalized_action,
            document_id=document_id,
            policy=POLICY_ORGANIZATION_ACTIVE,
            reason="Organization is inactive",
        )

    if document.tenant_id != context.tenant_id:
        return _decision(
            allowed=False,
            action=normalized_action,
            document_id=document_id,
            policy=POLICY_TENANT_ISOLATION,
            reason="Document belongs to a different tenant",
        )

    if not document.is_active:
        return _decision(
            allowed=False,
            action=normalized_action,
            document_id=document_id,
            policy=POLICY_DOCUMENT_ACTIVE,
            reason="Document is inactive",
        )

    resolved_role_ids = (
        role_ids
        if role_ids is not None
        else load_role_ids(db, context.tenant_id, context.roles)
    )
    granted, cross_department, reason, policy = _permission_result(
        db, context, document, normalized_action, resolved_role_ids
    )
    if not granted:
        return _decision(
            allowed=False,
            action=normalized_action,
            document_id=document_id,
            policy=policy,
            reason=reason,
        )

    attributes = (
        raw_attributes
        if raw_attributes is not None
        else load_user_attribute_map(db, context.tenant_id, context.user.user_id)
    )
    abac: AbacDecision = evaluate_abac(
        attributes,
        document,
        cross_department_allowed=cross_department,
        is_admin=ROLE_ADMIN in context.roles,
    )
    if not abac.allowed:
        return _decision(
            allowed=False,
            action=normalized_action,
            document_id=document_id,
            policy=abac.policy,
            reason=abac.reason,
        )

    return _decision(
        allowed=True,
        action=normalized_action,
        document_id=document_id,
        policy=POLICY_ALLOW,
        reason="RBAC, document permission and ABAC all allow this action",
    )


def authorize_document(
    db: Session,
    context: AuthenticatedContext,
    document_id: UUID,
    action: str,
) -> AuthorizationDecision:
    """Load one tenant-scoped document and evaluate an action."""
    normalized_action = action.strip().upper()
    document = db.scalar(
        select(Document).where(
            Document.tenant_id == context.tenant_id,
            Document.document_id == document_id,
        )
    )
    if document is None:
        return _decision(
            allowed=False,
            action=normalized_action,
            document_id=document_id,
            policy=POLICY_RESOURCE_NOT_FOUND,
            reason="Document not found in this tenant",
        )

    return authorize_loaded_document(db, context, document, normalized_action)


def record_authorization_decision(
    db: Session,
    context: AuthenticatedContext,
    decision: AuthorizationDecision,
    request_meta: RequestMeta,
) -> None:
    """Persist one authorization decision.

    Allowed decisions join the request transaction. Denials use a separate
    session because the request transaction is about to be rolled back.
    """
    log_security_event(
        db if decision.allowed else None,
        tenant_id=context.tenant_id,
        user_id=context.user.user_id,
        resource_type="DOCUMENT",
        resource_id=decision.document_id,
        action=decision.action,
        decision=decision.decision,
        reason=decision.reason,
        policy=decision.policy,
        request_meta=request_meta,
    )


def enforce_authorization(
    db: Session,
    context: AuthenticatedContext,
    decision: AuthorizationDecision,
    request_meta: RequestMeta,
) -> None:
    """Record and translate a decision into a safe HTTP error if denied."""
    record_authorization_decision(db, context, decision, request_meta)

    if decision.allowed:
        return

    if decision.policy == POLICY_RESOURCE_NOT_FOUND:
        raise NotFoundError("Document not found")
    raise ForbiddenError("You do not have permission to perform this action")
