"""Connector registry.

One process-wide registry maps a provider slug (``google_drive``,
``sharepoint``, ``local_storage``) to its connector class. Routes and workers
only ever resolve connectors through this module, so adding a provider is a
registration, not an edit to the sync engine, the API or the database.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

from app.integrations.base import Capability, Connector
from app.integrations.exceptions import (
    ConnectorNotFoundError,
    ConnectorUnavailableError,
)


@dataclass(frozen=True, slots=True)
class ProviderInfo:
    provider: str
    display_name: str
    capabilities: tuple[str, ...]
    available: bool
    unavailable_reason: str | None
    supports_oauth: bool


_REGISTRY: dict[str, type[Connector]] = {}


def normalize_provider(provider: str) -> str:
    """Provider slugs are lowercase, ``[a-z0-9_]`` only, max 32 characters."""
    if not isinstance(provider, str):
        raise ConnectorNotFoundError()
    slug = provider.strip().lower()
    if not slug or len(slug) > 32:
        raise ConnectorNotFoundError()
    if not all(character.isalnum() or character == "_" for character in slug):
        raise ConnectorNotFoundError()
    return slug


def register_provider(
    connector_class: type[Connector], *, replace: bool = False
) -> type[Connector]:
    """Register a connector class. Returns the class so it can be used as a decorator."""
    if not isinstance(connector_class, type) or not issubclass(connector_class, Connector):
        raise TypeError("register_provider expects a Connector subclass")
    provider = normalize_provider(connector_class.provider)
    if provider in _REGISTRY and not replace:
        raise ValueError(f"provider {provider!r} is already registered")
    declared = frozenset(connector_class.capabilities)
    if not all(isinstance(capability, Capability) for capability in declared):
        raise TypeError("connector capabilities must be Capability members")
    connector_class.provider = provider
    _REGISTRY[provider] = connector_class
    return connector_class


def unregister_provider(provider: str) -> None:
    """Test helper: remove a provider registration if present."""
    _REGISTRY.pop(normalize_provider(provider), None)


def is_registered(provider: str) -> bool:
    try:
        return normalize_provider(provider) in _REGISTRY
    except ConnectorNotFoundError:
        return False


def get_connector_class(provider: str) -> type[Connector]:
    slug = normalize_provider(provider)
    connector_class = _REGISTRY.get(slug)
    if connector_class is None:
        raise ConnectorNotFoundError(
            f"No connector is registered for provider {slug!r}"
        )
    return connector_class


def provider_capabilities(provider: str) -> frozenset[Capability]:
    return frozenset(get_connector_class(provider).capabilities)


def supports(provider: str, capability: Capability) -> bool:
    return capability in provider_capabilities(provider)


def is_available(provider: str) -> tuple[bool, str | None]:
    """Deployment availability, as reported by the connector class itself."""
    connector_class = get_connector_class(provider)
    try:
        available, reason = connector_class.availability()
    except Exception:  # pragma: no cover - defensive: never trust provider code
        return False, "The connector could not report its availability"
    return bool(available), None if available else (reason or "Not configured")


def create_connector(provider: str, **kwargs: Any) -> Connector:
    """Instantiate a registered, available connector.

    ``kwargs`` are forwarded to the connector constructor (``transport`` for
    HTTP providers); credentials are loaded separately via ``authenticate`` so
    a connector can be constructed before its secrets are decrypted.
    """
    connector_class = get_connector_class(provider)
    available, reason = is_available(provider)
    if not available:
        raise ConnectorUnavailableError(reason)
    return connector_class(**kwargs)


def list_providers() -> tuple[ProviderInfo, ...]:
    """All registered providers, sorted by slug. Never raises for one bad provider."""
    providers: list[ProviderInfo] = []
    for provider in sorted(_REGISTRY):
        connector_class = _REGISTRY[provider]
        available, reason = is_available(provider)
        providers.append(
            ProviderInfo(
                provider=provider,
                display_name=connector_class.display_name or provider,
                capabilities=tuple(
                    sorted(capability.value for capability in connector_class.capabilities)
                ),
                available=available,
                unavailable_reason=reason,
                supports_oauth=bool(connector_class.supports_oauth),
            )
        )
    return tuple(providers)


class _RegistryView:
    """Read-only snapshot for diagnostics and tests."""

    providers: ClassVar[type[dict[str, type[Connector]]]] = dict

    def as_dict(self) -> dict[str, type[Connector]]:
        return dict(_REGISTRY)
