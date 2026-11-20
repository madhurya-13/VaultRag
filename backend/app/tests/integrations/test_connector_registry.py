"""The registry is the only extension point for providers."""

from __future__ import annotations

import pytest

from app.integrations import registry
from app.integrations.base import (
    Capability,
    ConnectionState,
    Connector,
    FilePage,
    NormalizedDocument,
)
from app.integrations.exceptions import (
    ConnectorNotFoundError,
    ConnectorUnavailableError,
)


class _StubConnector(Connector):
    provider = "stub_provider"
    display_name = "Stub provider"
    capabilities = frozenset({Capability.LIST_FILES})

    async def authenticate(self, credentials) -> None:  # noqa: ANN001
        return None

    async def validate_connection(self) -> ConnectionState:
        return ConnectionState(ok=True)

    async def list_files(
        self, external_repository_id: str, *, cursor: str | None = None, page_size: int = 100
    ) -> FilePage:
        return FilePage(
            items=(
                NormalizedDocument(
                    external_file_id=f"{external_repository_id}/one.pdf",
                    external_repository_id=external_repository_id,
                    file_name="one.pdf",
                    mime_type="application/pdf",
                ),
            )
        )


class _UnavailableConnector(_StubConnector):
    provider = "unavailable_provider"

    @classmethod
    def availability(cls) -> tuple[bool, str | None]:
        return False, "No client id configured"


@pytest.fixture()
def stub_provider():
    registry.register_provider(_StubConnector, replace=True)
    try:
        yield _StubConnector
    finally:
        registry.unregister_provider("stub_provider")


def test_builtin_connectors_are_registered():
    for provider in ("local_storage", "google_drive", "sharepoint"):
        assert registry.is_registered(provider)
    assert registry.provider_capabilities("google_drive").issuperset(
        {Capability.GET_PERMISSIONS, Capability.GET_VERSIONS}
    )
    assert registry.provider_capabilities("sharepoint").issuperset(
        {Capability.DETECT_CHANGES, Capability.GET_PERMISSIONS}
    )


def test_builtin_local_connector_is_registered():
    assert registry.is_registered("local_storage")
    capabilities = registry.provider_capabilities("local_storage")
    assert Capability.DOWNLOAD in capabilities
    assert Capability.WEBHOOKS not in capabilities
    info = {provider.provider: provider for provider in registry.list_providers()}
    assert info["local_storage"].available is True
    assert info["local_storage"].supports_oauth is False


def test_provider_listing_is_sorted_by_slug():
    providers = [info.provider for info in registry.list_providers()]
    assert providers == sorted(providers)


def test_unknown_and_malformed_providers_are_rejected():
    with pytest.raises(ConnectorNotFoundError):
        registry.get_connector_class("does_not_exist")
    with pytest.raises(ConnectorNotFoundError):
        registry.get_connector_class("../etc/passwd")
    with pytest.raises(ConnectorNotFoundError):
        registry.create_connector("dropbox")


def test_duplicate_registration_requires_explicit_replace():
    class Duplicate(Connector):
        provider = "duplicate_provider"

        async def authenticate(self, credentials) -> None:  # noqa: ANN001
            return None

        async def validate_connection(self) -> ConnectionState:
            return ConnectionState(ok=True)

    registry.register_provider(Duplicate)
    try:
        with pytest.raises(ValueError):
            registry.register_provider(Duplicate)
        registry.register_provider(Duplicate, replace=True)
    finally:
        registry.unregister_provider("duplicate_provider")


def test_registering_a_non_connector_is_refused():
    with pytest.raises(TypeError):
        registry.register_provider(object)  # type: ignore[arg-type]


def test_a_new_provider_is_usable_without_touching_existing_code(stub_provider):
    connector = registry.create_connector("stub_provider")
    assert registry.supports("stub_provider", Capability.LIST_FILES)
    assert not registry.supports("stub_provider", Capability.GET_PERMISSIONS)
    assert connector.supports(Capability.LIST_FILES)
    assert connector.require(Capability.LIST_FILES) is None


def test_unavailable_provider_refuses_construction():
    registry.register_provider(_UnavailableConnector, replace=True)
    try:
        available, reason = registry.is_available("unavailable_provider")
        assert available is False
        assert reason == "No client id configured"
        listed = {i.provider: i for i in registry.list_providers()}
        assert listed["unavailable_provider"].available is False
        with pytest.raises(ConnectorUnavailableError):
            registry.create_connector("unavailable_provider")
    finally:
        registry.unregister_provider("unavailable_provider")
