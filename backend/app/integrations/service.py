"""Integration connection lifecycle.

Routes never touch ORM rows directly. Every mutation goes through this module so
the same validation, audit event and tenant scoping apply to the API, the worker
and the tests.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import BadRequestError
from app.core.request_context import RequestMeta
from app.core.security_constants import (
    INTEGRATION_STATUS_CONNECTED,
    INTEGRATION_STATUS_DISCONNECTED,
    INTEGRATION_STATUS_REAUTH_REQUIRED,
    PERMISSION_SYNC_MODES,
    POLICY_INTEGRATION,
    PROVIDER_LOCAL_STORAGE,
    REPOSITORY_KINDS,
)
from app.integrations import registry
from app.integrations.base import Connector
from app.integrations.credentials import (
    has_credentials,
    load_credentials,
    require_integration,
    revoke_credentials,
    store_credentials,
)
from app.integrations.exceptions import (
    IntegrationNotConfiguredError,
    IntegrationValidationError,
)
from app.integrations.models import (
    Integration,
    IntegrationIdentityMapping,
    IntegrationRepository,
)
from app.models import Role, User
from app.services.security_audit import log_security_event

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def ensure_enabled() -> None:
    if not get_settings().integrations_enabled:
        raise IntegrationNotConfiguredError(
            "Enterprise integrations are disabled on this deployment"
        )


def audit(
    db: Session,
    *,
    tenant_id: UUID,
    user_id: UUID,
    integration_id: UUID,
    action: str,
    reason: str,
    request_meta: RequestMeta | None = None,
    decision: str = "ALLOW",
) -> None:
    """Record an integration decision. The reason never contains a secret."""
    log_security_event(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        resource_type="INTEGRATION",
        resource_id=integration_id,
        action=action,
        decision=decision,
        reason=reason[:500],
        policy=POLICY_INTEGRATION,
        request_meta=request_meta,
    )


def _connector_credentials(
    integration: Integration, stored: Mapping[str, Any]
) -> dict[str, Any]:
    """Merge non-secret configuration with decrypted credentials."""
    merged: dict[str, Any] = dict(integration.configuration or {})
    merged.update({key: value for key, value in stored.items() if value is not None})
    return merged


def build_connector(
    db: Session,
    *,
    integration: Integration,
    transport: Any | None = None,
) -> Connector:
    """Construct an authenticated connector for one integration."""
    ensure_enabled()
    stored = load_credentials(db, integration=integration)
    if not stored and integration.provider != PROVIDER_LOCAL_STORAGE:
        raise IntegrationNotConfiguredError(
            "This integration has no stored credentials"
        )
    connector = registry.create_connector(
        integration.provider, transport=transport
    )
    credentials = _connector_credentials(integration, stored)
    try:
        _run(connector.authenticate(credentials))
    except Exception:
        _run(connector.disconnect())
        raise
    return connector


def run_async(coroutine):
    """Run one connector coroutine from synchronous service code."""
    import asyncio

    return asyncio.run(coroutine)


_run = run_async


def validate_name(name: str) -> str:
    cleaned = (name or "").strip()
    if not cleaned:
        raise BadRequestError("Integration name is required")
    if len(cleaned) > 120:
        raise BadRequestError("Integration name is too long")
    return cleaned


def create_integration(
    db: Session,
    *,
    tenant_id: UUID,
    actor_user_id: UUID,
    provider: str,
    name: str,
    permission_sync_mode: str = "VAULTRAG_MANAGED",
    sync_interval_minutes: int | None = None,
    configuration: Mapping[str, Any] | None = None,
    credentials: Mapping[str, Any] | None = None,
    request_meta: RequestMeta | None = None,
) -> Integration:
    ensure_enabled()
    slug = registry.normalize_provider(provider)
    available, reason = registry.is_available(slug)
    if not available:
        raise IntegrationValidationError(
            f"{reason or 'Provider is unavailable on this deployment'}"
        )
    if permission_sync_mode not in PERMISSION_SYNC_MODES:
        raise BadRequestError("Unknown permission sync mode")
    settings = get_settings()
    interval = (
        sync_interval_minutes or settings.integration_sync_default_interval_minutes
    )
    safe_configuration = _validate_configuration(configuration or {})
    integration = Integration(
        tenant_id=tenant_id,
        provider=slug,
        integration_name=validate_name(name),
        permission_sync_mode=permission_sync_mode,
        sync_interval_minutes=interval,
        configuration=safe_configuration,
        created_by=actor_user_id,
    )
    db.add(integration)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise BadRequestError("An integration with that name already exists") from exc
    if credentials:
        store_credentials(
            db, integration=integration, payload=dict(credentials), scopes=None
        )
    audit(
        db,
        tenant_id=tenant_id,
        user_id=actor_user_id,
        integration_id=integration.integration_id,
        action="INTEGRATION_CREATE",
        reason=f"provider={slug};permission_mode={permission_sync_mode}",
        request_meta=request_meta,
    )
    return integration


def _validate_configuration(values: Mapping[str, Any]) -> dict[str, Any]:
    """Only known, non-secret keys are accepted into the JSONB column."""
    allowed = {
        "root",
        "department",
        "classification",
        "webhook_token",
        "site_id",
        "drive_id",
        "folder_id",
        "delta_link",
    }
    cleaned: dict[str, Any] = {}
    for key, value in values.items():
        if key not in allowed:
            raise BadRequestError(f"Unsupported integration setting: {key}")
        if value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            if isinstance(value, str) and len(value) > 2048:
                raise BadRequestError(f"Integration setting {key} is too long")
            cleaned[key] = value
        else:
            raise BadRequestError(f"Integration setting {key} has an invalid type")
    return cleaned


def update_integration(
    db: Session,
    *,
    integration: Integration,
    actor_user_id: UUID,
    name: str | None = None,
    permission_sync_mode: str | None = None,
    sync_enabled: bool | None = None,
    sync_interval_minutes: int | None = None,
    request_meta: RequestMeta | None = None,
) -> Integration:
    ensure_enabled()
    if name is not None:
        integration.integration_name = validate_name(name)
    if permission_sync_mode is not None:
        if permission_sync_mode not in PERMISSION_SYNC_MODES:
            raise BadRequestError("Unknown permission sync mode")
        integration.permission_sync_mode = permission_sync_mode
    if sync_enabled is not None:
        integration.sync_enabled = bool(sync_enabled)
        integration.next_sync_at = (
            _utcnow() + timedelta(minutes=integration.sync_interval_minutes)
            if sync_enabled
            else None
        )
    if sync_interval_minutes is not None:
        if not 1 <= int(sync_interval_minutes) <= 10080:
            raise BadRequestError("Sync interval must be between 1 and 10080 minutes")
        integration.sync_interval_minutes = int(sync_interval_minutes)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise BadRequestError("An integration with that name already exists") from exc
    audit(
        db,
        tenant_id=integration.tenant_id,
        user_id=actor_user_id,
        integration_id=integration.integration_id,
        action="INTEGRATION_UPDATE",
        reason="Settings updated",
        request_meta=request_meta,
    )
    return integration


def set_credentials(
    db: Session,
    *,
    integration: Integration,
    actor_user_id: UUID,
    credentials: Mapping[str, Any],
    scopes: str | None = None,
    request_meta: RequestMeta | None = None,
) -> None:
    ensure_enabled()
    if not credentials:
        raise BadRequestError("Credential payload is required")
    store_credentials(
        db, integration=integration, payload=dict(credentials), scopes=scopes
    )
    integration.connection_status = INTEGRATION_STATUS_DISCONNECTED
    audit(
        db,
        tenant_id=integration.tenant_id,
        user_id=actor_user_id,
        integration_id=integration.integration_id,
        action="INTEGRATION_CREDENTIAL_UPDATE",
        reason="Provider credentials replaced",
        request_meta=request_meta,
    )


def connect_integration(
    db: Session,
    *,
    integration: Integration,
    actor_user_id: UUID,
    request_meta: RequestMeta | None = None,
    transport: Any | None = None,
) -> tuple[bool, str | None]:
    """Validate the stored credential and mark the connection state."""
    ensure_enabled()
    stored = load_credentials(db, integration=integration)
    if not stored and integration.provider != PROVIDER_LOCAL_STORAGE:
        raise IntegrationNotConfiguredError(
            "Store provider credentials before connecting"
        )
    connector = build_connector(db, integration=integration, transport=transport)
    try:
        state = run_async(connector.validate_connection())
    finally:
        run_async(connector.disconnect())

    if state.ok:
        integration.connection_status = INTEGRATION_STATUS_CONNECTED
        integration.next_sync_at = _utcnow() + timedelta(
            minutes=integration.sync_interval_minutes
        )
    else:
        integration.connection_status = INTEGRATION_STATUS_REAUTH_REQUIRED
        integration.next_sync_at = None
    audit(
        db,
        tenant_id=integration.tenant_id,
        user_id=actor_user_id,
        integration_id=integration.integration_id,
        action="INTEGRATION_CONNECT",
        reason=f"ok={state.ok}",
        decision="ALLOW" if state.ok else "DENY",
        request_meta=request_meta,
    )
    return state.ok, state.detail


def disconnect_integration(
    db: Session,
    *,
    integration: Integration,
    actor_user_id: UUID,
    request_meta: RequestMeta | None = None,
    purge_credentials: bool = True,
) -> None:
    """Stop synchronization and make stored credentials unreadable.

    The row and all history are retained: the runtime role deliberately has no
    DELETE grant on integration tables, so an audit trail cannot be destroyed
    through the API.
    """
    if purge_credentials:
        revoke_credentials(db, integration=integration)
    integration.connection_status = INTEGRATION_STATUS_DISCONNECTED
    integration.sync_enabled = False
    integration.next_sync_at = None
    audit(
        db,
        tenant_id=integration.tenant_id,
        user_id=actor_user_id,
        integration_id=integration.integration_id,
        action="INTEGRATION_DISCONNECT",
        reason="Connection disabled and credentials revoked",
        request_meta=request_meta,
    )


def discover_repositories(
    db: Session,
    *,
    integration: Integration,
    actor_user_id: UUID,
    request_meta: RequestMeta | None = None,
    transport: Any | None = None,
) -> list[IntegrationRepository]:
    """List provider containers and persist new ones as unselected rows."""
    connector = build_connector(db, integration=integration, transport=transport)
    try:
        descriptors = run_async(connector.list_repositories())
    finally:
        run_async(connector.disconnect())

    existing = {
        row.external_repository_id: row
        for row in db.scalars(
            select(IntegrationRepository).where(
                IntegrationRepository.tenant_id == integration.tenant_id,
                IntegrationRepository.integration_id == integration.integration_id,
            )
        ).all()
    }
    result: list[IntegrationRepository] = []
    for descriptor in descriptors:
        if descriptor.repository_kind not in REPOSITORY_KINDS:
            continue
        row = existing.get(descriptor.external_repository_id)
        if row is None:
            row = IntegrationRepository(
                tenant_id=integration.tenant_id,
                integration_id=integration.integration_id,
                external_repository_id=descriptor.external_repository_id,
                name=descriptor.name,
                repository_kind=descriptor.repository_kind,
                is_selected=False,
            )
            db.add(row)
        else:
            row.name = descriptor.name
            row.repository_kind = descriptor.repository_kind
        result.append(row)
    db.flush()
    audit(
        db,
        tenant_id=integration.tenant_id,
        user_id=actor_user_id,
        integration_id=integration.integration_id,
        action="INTEGRATION_DISCOVER_REPOSITORIES",
        reason=f"repositories={len(result)}",
        request_meta=request_meta,
    )
    return result


def select_repository(
    db: Session,
    *,
    integration: Integration,
    actor_user_id: UUID,
    repository_id: UUID,
    selected: bool,
    sync_mode: str | None = None,
    request_meta: RequestMeta | None = None,
) -> IntegrationRepository:
    repository = db.scalar(
        select(IntegrationRepository).where(
            IntegrationRepository.tenant_id == integration.tenant_id,
            IntegrationRepository.integration_id == integration.integration_id,
            IntegrationRepository.repository_id == repository_id,
        )
    )
    if repository is None:
        raise BadRequestError("Repository not found for this integration")
    repository.is_selected = bool(selected)
    if sync_mode is not None:
        if sync_mode not in {"FULL", "INCREMENTAL"}:
            raise BadRequestError("Unknown repository sync mode")
        repository.sync_mode = sync_mode
        if sync_mode == "FULL":
            repository.cursor = None
    audit(
        db,
        tenant_id=integration.tenant_id,
        user_id=actor_user_id,
        integration_id=integration.integration_id,
        action="INTEGRATION_SELECT_REPOSITORY",
        reason=f"selected={repository.is_selected};sync_mode={repository.sync_mode}",
        request_meta=request_meta,
    )
    return repository


def create_identity_mapping(
    db: Session,
    *,
    integration: Integration,
    actor_user_id: UUID,
    external_principal_id: str,
    external_principal_type: str,
    user_id: UUID | None,
    role_id: UUID | None,
    request_meta: RequestMeta | None = None,
) -> IntegrationIdentityMapping:
    if (user_id is None) == (role_id is None):
        raise BadRequestError("Provide exactly one of user_id or role_id")
    principal_id = (external_principal_id or "").strip()
    if not principal_id or len(principal_id) > 512:
        raise BadRequestError("External principal id is invalid")
    if external_principal_type not in {
        "USER",
        "GROUP",
        "ROLE",
        "DOMAIN",
        "ANYONE",
    }:
        raise BadRequestError("Unknown external principal type")
    if user_id is not None:
        user = db.scalar(
            select(User).where(
                User.tenant_id == integration.tenant_id, User.user_id == user_id
            )
        )
        if user is None:
            raise BadRequestError("User not found in this organization")
    if role_id is not None:
        role = db.scalar(
            select(Role).where(
                Role.tenant_id == integration.tenant_id, Role.role_id == role_id
            )
        )
        if role is None:
            raise BadRequestError("Role not found in this organization")
    mapping = IntegrationIdentityMapping(
        tenant_id=integration.tenant_id,
        integration_id=integration.integration_id,
        external_principal_id=principal_id,
        external_principal_type=external_principal_type,
        user_id=user_id,
        role_id=role_id,
        created_by=actor_user_id,
    )
    db.add(mapping)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise BadRequestError("That external principal is already mapped") from exc
    audit(
        db,
        tenant_id=integration.tenant_id,
        user_id=actor_user_id,
        integration_id=integration.integration_id,
        action="INTEGRATION_IDENTITY_MAPPING_CREATE",
        reason=f"principal_type={external_principal_type}",
        request_meta=request_meta,
    )
    return mapping


def delete_identity_mapping(
    db: Session,
    *,
    integration: Integration,
    actor_user_id: UUID,
    mapping_id: UUID,
    request_meta: RequestMeta | None = None,
) -> None:
    mapping = db.scalar(
        select(IntegrationIdentityMapping).where(
            IntegrationIdentityMapping.tenant_id == integration.tenant_id,
            IntegrationIdentityMapping.integration_id == integration.integration_id,
            IntegrationIdentityMapping.mapping_id == mapping_id,
        )
    )
    if mapping is None:
        raise BadRequestError("Identity mapping not found")
    mapping.is_active = False
    audit(
        db,
        tenant_id=integration.tenant_id,
        user_id=actor_user_id,
        integration_id=integration.integration_id,
        action="INTEGRATION_IDENTITY_MAPPING_DELETE",
        reason="Mapping deactivated",
        request_meta=request_meta,
    )


def get_integration(
    db: Session, *, tenant_id: UUID, integration_id: UUID
) -> Integration:
    return require_integration(
        db, tenant_id=tenant_id, integration_id=integration_id
    )


def credentials_configured(db: Session, *, integration: Integration) -> bool:
    return integration.provider == PROVIDER_LOCAL_STORAGE or has_credentials(
        db, integration=integration
    )


def provider_status(provider: str) -> dict[str, Any]:
    slug = registry.normalize_provider(provider)
    available, reason = registry.is_available(slug)
    info = registry.get_connector_class(slug)
    return {
        "provider": slug,
        "display_name": info.display_name,
        "available": available,
        "unavailable_reason": reason,
        "capabilities": sorted(capability.value for capability in info.capabilities),
        "supports_oauth": info.supports_oauth,
    }
