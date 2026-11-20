"""Provider-neutral contract for enterprise knowledge-source connectors.

Design rules enforced here:

* A connector never sees VaultRAG ORM models, sessions or the authorization
  engine. It speaks only in the immutable value objects below, so a new
  provider cannot bypass tenant isolation, ingestion or the permission engine.
* Capabilities are declared, not assumed. Calling an operation the provider
  did not declare raises :class:`UnsupportedCapabilityError` instead of
  silently returning empty data.
* Everything the provider reports is *untrusted*. Tenant identity is never
  accepted from a provider payload; it is supplied by the caller from the
  authenticated integration record.
* Providers return bytes as a temporary file with a server-computed size and
  SHA-256 digest. The sync layer decides what to ingest; the connector never
  writes into VaultRAG storage.

Adding a provider means adding a module that subclasses :class:`Connector` and
registers itself with :func:`app.integrations.registry.register_provider`.
No existing connector, route or model changes.
"""

from __future__ import annotations

import abc
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar

from app.integrations.exceptions import UnsupportedCapabilityError

MAX_EXTERNAL_ID_LENGTH = 512
MAX_NAME_LENGTH = 512
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

#: Ingestible payload types for Milestone 9. PDFs cover ordinary uploads and
#: Google-native exports (Docs/Sheets/Slides are exported as PDF); DOCX/TXT are
#: deliberately out of scope until ingestion supports them.
INGESTIBLE_MIME_TYPES: frozenset[str] = frozenset({"application/pdf"})


class Capability(str, Enum):
    """What a provider can do. Declared per connector class."""

    LIST_REPOSITORIES = "LIST_REPOSITORIES"
    LIST_FILES = "LIST_FILES"
    GET_METADATA = "GET_METADATA"
    DOWNLOAD = "DOWNLOAD"
    GET_PERMISSIONS = "GET_PERMISSIONS"
    DETECT_CHANGES = "DETECT_CHANGES"
    GET_VERSIONS = "GET_VERSIONS"
    WEBHOOKS = "WEBHOOKS"
    NATIVE_EXPORT = "NATIVE_EXPORT"


class ChangeKind(str, Enum):
    """Change classes reported by incremental discovery."""

    CREATED = "CREATED"
    UPDATED = "UPDATED"
    RENAMED = "RENAMED"
    MOVED = "MOVED"
    DELETED = "DELETED"
    PERMISSION_CHANGED = "PERMISSION_CHANGED"


class PrincipalType(str, Enum):
    """External principal kinds the permission mapper understands."""

    USER = "USER"
    GROUP = "GROUP"
    ROLE = "ROLE"
    DOMAIN = "DOMAIN"
    ANYONE = "ANYONE"


class ExternalRole(str, Enum):
    """Provider permission levels normalized onto VaultRAG's grant vocabulary.

    ``UNKNOWN`` is intentionally explicit: an unrecognised provider role must
    never be upgraded to READ. The mapper treats UNKNOWN as "unresolved", and
    per the deny-by-default rule the document receives no external grant.
    """

    OWNER = "OWNER"
    WRITER = "WRITER"
    COMMENTER = "COMMENTER"
    READER = "READER"
    UNKNOWN = "UNKNOWN"


def _require_text(value: Any, field_name: str, *, max_length: int) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    if len(value) > max_length:
        raise ValueError(f"{field_name} must be at most {max_length} characters")


def _optional_checksum(value: str | None) -> None:
    if value is not None and not _SHA256_RE.match(value):
        raise ValueError("checksum must be a lowercase hexadecimal SHA-256 digest")


@dataclass(frozen=True, slots=True)
class ConnectionState:
    """Result of validating stored credentials against the provider."""

    ok: bool
    detail: str | None = None
    reauth_required: bool = False


@dataclass(frozen=True, slots=True)
class RepositoryDescriptor:
    """A selectable container: a Drive, a SharePoint document library, a folder."""

    external_repository_id: str
    name: str
    repository_kind: str = "FOLDER"
    description: str | None = None
    selectable: bool = True
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_text(
            self.external_repository_id,
            "external_repository_id",
            max_length=MAX_EXTERNAL_ID_LENGTH,
        )
        _require_text(self.name, "name", max_length=MAX_NAME_LENGTH)
        _require_text(self.repository_kind, "repository_kind", max_length=32)


@dataclass(frozen=True, slots=True)
class NormalizedPermission:
    """One external grant, before it is mapped to a VaultRAG user or role.

    ``principal_id`` is an opaque provider identifier (an object id, an email or
    a group id). Resolution to internal principals happens in the permission
    sync service, never in the connector.
    """

    principal_id: str
    principal_type: PrincipalType
    role: ExternalRole
    inherited: bool = False
    principal_display_name: str | None = None
    raw: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_text(
            self.principal_id,
            "principal_id",
            max_length=MAX_EXTERNAL_ID_LENGTH,
        )
        if not isinstance(self.principal_type, PrincipalType):
            raise ValueError("principal_type must be a PrincipalType")
        if not isinstance(self.role, ExternalRole):
            raise ValueError("role must be an ExternalRole")


@dataclass(frozen=True, slots=True)
class NormalizedDocument:
    """A provider document described in VaultRAG terms.

    ``external_file_id`` is only unique within ``(integration, provider)``; the
    mapped row also carries the tenant id taken from the integration record.
    """

    external_file_id: str
    external_repository_id: str
    file_name: str
    mime_type: str | None = None
    file_size: int | None = None
    checksum: str | None = None
    external_version_id: str | None = None
    modified_at: datetime | None = None
    created_at: datetime | None = None
    source_url: str | None = None
    source_path: str | None = None
    native_export_mime_type: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    permissions: tuple[NormalizedPermission, ...] = ()
    permissions_resolved: bool = False

    def __post_init__(self) -> None:
        _require_text(
            self.external_file_id,
            "external_file_id",
            max_length=MAX_EXTERNAL_ID_LENGTH,
        )
        _require_text(
            self.external_repository_id,
            "external_repository_id",
            max_length=MAX_EXTERNAL_ID_LENGTH,
        )
        _require_text(self.file_name, "file_name", max_length=MAX_NAME_LENGTH)
        if self.file_size is not None and self.file_size < 0:
            raise ValueError("file_size must not be negative")
        _optional_checksum(self.checksum)
        for permission in self.permissions:
            if not isinstance(permission, NormalizedPermission):
                raise ValueError("permissions must contain NormalizedPermission")

    @property
    def ingestible_mime_type(self) -> str | None:
        """The type VaultRAG will actually ingest, or ``None`` if unsupported."""
        candidate = self.native_export_mime_type or self.mime_type
        if candidate is None:
            return None
        return candidate if candidate in INGESTIBLE_MIME_TYPES else None


@dataclass(frozen=True, slots=True)
class ExternalVersion:
    """A historical revision advertised by the provider."""

    external_version_id: str
    modified_at: datetime | None = None
    file_size: int | None = None
    checksum: str | None = None
    is_current: bool = False

    def __post_init__(self) -> None:
        _require_text(
            self.external_version_id,
            "external_version_id",
            max_length=MAX_EXTERNAL_ID_LENGTH,
        )
        if self.file_size is not None and self.file_size < 0:
            raise ValueError("file_size must not be negative")
        _optional_checksum(self.checksum)


@dataclass(frozen=True, slots=True)
class FilePage:
    items: tuple[NormalizedDocument, ...]
    next_cursor: str | None = None
    has_more: bool = False


@dataclass(frozen=True, slots=True)
class ChangeRecord:
    """One item of an incremental change feed."""

    kind: ChangeKind
    external_file_id: str
    external_repository_id: str
    external_version_id: str | None = None
    file_name: str | None = None
    modified_at: datetime | None = None
    checksum: str | None = None
    deleted: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ChangeKind):
            raise ValueError("kind must be a ChangeKind")
        _require_text(
            self.external_file_id,
            "external_file_id",
            max_length=MAX_EXTERNAL_ID_LENGTH,
        )
        _require_text(
            self.external_repository_id,
            "external_repository_id",
            max_length=MAX_EXTERNAL_ID_LENGTH,
        )
        if self.kind is ChangeKind.DELETED and not self.deleted:
            object.__setattr__(self, "deleted", True)


@dataclass(frozen=True, slots=True)
class ChangePage:
    changes: tuple[ChangeRecord, ...]
    next_cursor: str | None = None
    has_more: bool = False


@dataclass(slots=True)
class DownloadedDocument:
    """Downloaded bytes in a server-owned temporary file.

    The sync service must ``cleanup()`` (or use the context manager) after
    ingestion. The digest is computed by the connector from the bytes it wrote,
    never taken from provider headers.
    """

    temp_path: Path
    size: int
    sha256: str
    mime_type: str | None = None
    external_version_id: str | None = None

    def cleanup(self) -> None:
        try:
            self.temp_path.unlink(missing_ok=True)
        except OSError:  # pragma: no cover - best-effort cleanup
            pass

    def __enter__(self) -> "DownloadedDocument":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.cleanup()


class Connector(abc.ABC):
    """Base class for every provider connector.

    Only :meth:`authenticate` and :meth:`validate_connection` are abstract.
    Every optional operation defaults to a hard failure so a provider that
    forgets to implement an advertised capability cannot return empty data that
    looks like "no documents" or "no permissions".
    """

    provider: ClassVar[str] = ""
    display_name: ClassVar[str] = ""
    capabilities: ClassVar[frozenset[Capability]] = frozenset()
    #: Providers that complete an OAuth authorization-code flow. The local
    #: reference connector and future token-based connectors stay False.
    supports_oauth: ClassVar[bool] = False

    def __init__(self, *, transport: Any | None = None) -> None:
        # ``transport`` lets HTTP providers accept a deterministic
        # ``httpx.AsyncBaseTransport`` in tests. Local connectors ignore it.
        self._transport = transport

    # -- capabilities ---------------------------------------------------
    @classmethod
    def supports(cls, capability: Capability) -> bool:
        return capability in cls.capabilities

    def require(self, capability: Capability) -> None:
        if not self.supports(capability):
            raise UnsupportedCapabilityError(capability)

    @classmethod
    def availability(cls) -> tuple[bool, str | None]:
        """Whether this connector can run on this deployment.

        Providers that need deployment-wide configuration (an OAuth client id,
        a service account) override this. A connector is never silently
        downgraded to a stub: an unavailable provider is refused explicitly.
        """
        return True, None

    # -- lifecycle ------------------------------------------------------
    @abc.abstractmethod
    async def authenticate(self, credentials: Mapping[str, Any]) -> None:
        """Load and check credentials. Must not perform network discovery."""

    @abc.abstractmethod
    async def validate_connection(self) -> ConnectionState:
        """Return whether the loaded credentials still work."""

    async def disconnect(self) -> None:
        """Release provider-side resources. Never deletes provider data."""

    # -- discovery ------------------------------------------------------
    async def list_repositories(self) -> tuple[RepositoryDescriptor, ...]:
        self.require(Capability.LIST_REPOSITORIES)
        raise UnsupportedCapabilityError(Capability.LIST_REPOSITORIES)

    async def list_files(
        self,
        external_repository_id: str,
        *,
        cursor: str | None = None,
        page_size: int = 100,
    ) -> FilePage:
        self.require(Capability.LIST_FILES)
        raise UnsupportedCapabilityError(Capability.LIST_FILES)

    async def get_metadata(self, external_file_id: str) -> NormalizedDocument:
        self.require(Capability.GET_METADATA)
        raise UnsupportedCapabilityError(Capability.GET_METADATA)

    async def get_permissions(
        self, external_file_id: str
    ) -> tuple[NormalizedPermission, ...]:
        self.require(Capability.GET_PERMISSIONS)
        raise UnsupportedCapabilityError(Capability.GET_PERMISSIONS)

    async def detect_changes(
        self, external_repository_id: str, *, cursor: str | None = None
    ) -> ChangePage:
        self.require(Capability.DETECT_CHANGES)
        raise UnsupportedCapabilityError(Capability.DETECT_CHANGES)

    async def get_versions(self, external_file_id: str) -> tuple[ExternalVersion, ...]:
        self.require(Capability.GET_VERSIONS)
        raise UnsupportedCapabilityError(Capability.GET_VERSIONS)

    # -- content --------------------------------------------------------
    async def download(
        self,
        external_file_id: str,
        *,
        max_bytes: int,
        expected_version_id: str | None = None,
    ) -> DownloadedDocument:
        self.require(Capability.DOWNLOAD)
        raise UnsupportedCapabilityError(Capability.DOWNLOAD)
