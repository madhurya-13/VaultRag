"""Local-filesystem connector implementing the full discovery contract."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

from app.core.config import get_settings
from app.core.security_constants import PROVIDER_LOCAL_STORAGE
from app.integrations.base import (
    Capability,
    ChangeKind,
    ChangePage,
    ChangeRecord,
    ConnectionState,
    Connector,
    DownloadedDocument,
    FilePage,
    NormalizedDocument,
    RepositoryDescriptor,
)
from app.integrations.exceptions import (
    DownloadTooLargeError,
    ExternalPathError,
    IntegrationValidationError,
    ProviderAuthenticationError,
)
from app.integrations.providers.local_storage.mapper import (
    LocalFileMapper,
    sha256_file,
)
from app.integrations.registry import register_provider

#: Cursor snapshots are bounded so one huge directory cannot turn a cursor into
#: an unbounded blob in the database.
MAX_SNAPSHOT_ENTRIES = 5000
MAX_PAGE_SIZE = 500
_COPY_CHUNK = 1024 * 1024


@register_provider
class LocalStorageConnector(Connector):
    provider = PROVIDER_LOCAL_STORAGE
    display_name = "Local directory (reference connector)"
    capabilities = frozenset(
        {
            Capability.LIST_REPOSITORIES,
            Capability.LIST_FILES,
            Capability.GET_METADATA,
            Capability.DOWNLOAD,
            Capability.DETECT_CHANGES,
        }
    )

    def __init__(
        self,
        *,
        root: str | Path | None = None,
        max_download_bytes: int | None = None,
        transport: Any | None = None,
    ) -> None:
        super().__init__(transport=transport)
        self._root: Path | None = (
            Path(root).expanduser().resolve() if root is not None else None
        )
        self._max_download_bytes = max_download_bytes

    # -- lifecycle ------------------------------------------------------
    async def authenticate(self, credentials: Mapping[str, Any]) -> None:
        raw_root = credentials.get("root") if credentials else None
        if raw_root is not None:
            self._root = Path(str(raw_root)).expanduser().resolve()
        if self._root is None:
            raise IntegrationValidationError(
                "local_storage requires a 'root' setting in the integration configuration"
            )
        if not self._root.exists() or not self._root.is_dir():
            raise ProviderAuthenticationError(
                "The configured local source directory does not exist"
            )
        self._reject_private_storage_root()

    def _reject_private_storage_root(self) -> None:
        """Refuse to point the connector at VaultRAG's own document store."""
        assert self._root is not None
        private_root = Path(get_settings().document_storage_dir).expanduser().resolve()
        if self._root == private_root or private_root.is_relative_to(self._root):
            raise IntegrationValidationError(
                "The local source directory must not contain VaultRAG's private "
                "document storage"
            )

    async def validate_connection(self) -> ConnectionState:
        if self._root is None or not self._root.is_dir():
            return ConnectionState(
                ok=False,
                detail="The local source directory is missing",
                reauth_required=True,
            )
        if not os.access(self._root, os.R_OK):
            return ConnectionState(
                ok=False,
                detail="The local source directory is not readable",
                reauth_required=True,
            )
        return ConnectionState(ok=True)

    # -- discovery ------------------------------------------------------
    def _require_root(self) -> Path:
        if self._root is None:
            raise IntegrationValidationError(
                "local_storage has not been authenticated with a root directory"
            )
        return self._root

    @staticmethod
    def _clean_repository_id(repository_id: str) -> str:
        cleaned = (repository_id or "").strip()
        if not cleaned or cleaned in {".", ".."} or "/" in cleaned or "\\" in cleaned:
            raise ExternalPathError("The repository identifier is not a directory name")
        return cleaned

    def _repository_path(self, repository_id: str) -> Path:
        root = self._require_root()
        candidate = (root / self._clean_repository_id(repository_id)).resolve()
        if not candidate.is_relative_to(root) or not candidate.is_dir():
            raise ExternalPathError("The repository directory does not exist")
        return candidate

    def _resolve_file(self, external_file_id: str) -> Path:
        root = self._require_root()
        identifier = (external_file_id or "").strip()
        if not identifier or "\\" in identifier or "\x00" in identifier:
            raise ExternalPathError("The external file identifier is not valid")
        relative = PurePosixPath(identifier)
        if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
            raise ExternalPathError("The external file path is not allowed")
        if len(relative.parts) < 2:
            raise ExternalPathError("The external file identifier is incomplete")
        candidate = (root / Path(*relative.parts)).resolve()
        if not candidate.is_relative_to(root) or not candidate.is_file():
            raise ExternalPathError("The external file does not exist")
        return candidate

    def _iter_files(self, repository_path: Path) -> list[Path]:
        files: list[Path] = []
        for path in repository_path.rglob("*"):
            if path.name.startswith("."):
                continue
            if path.is_symlink():
                # Symlinks can escape the configured root; they are ignored.
                continue
            if path.is_file():
                files.append(path)
        return sorted(files, key=lambda item: item.as_posix())

    async def list_repositories(self) -> tuple[RepositoryDescriptor, ...]:
        root = self._require_root()
        descriptors: list[RepositoryDescriptor] = []
        for entry in sorted(root.iterdir(), key=lambda item: item.name):
            if entry.name.startswith(".") or entry.is_symlink() or not entry.is_dir():
                continue
            descriptors.append(
                RepositoryDescriptor(
                    external_repository_id=entry.name,
                    name=entry.name,
                    repository_kind="LOCAL",
                    metadata={"path": entry.name},
                )
            )
        return tuple(descriptors)

    async def list_files(
        self,
        external_repository_id: str,
        *,
        cursor: str | None = None,
        page_size: int = 100,
    ) -> FilePage:
        repository_path = self._repository_path(external_repository_id)
        mapper = LocalFileMapper(self._require_root())
        size = max(1, min(int(page_size), MAX_PAGE_SIZE))
        items: list[NormalizedDocument] = []
        last_seen: str | None = None
        for path in self._iter_files(repository_path):
            document = mapper.describe(
                repository_id=self._clean_repository_id(external_repository_id),
                path=path,
            )
            if cursor and document.external_file_id <= cursor:
                continue
            items.append(document)
            last_seen = document.external_file_id
            if len(items) >= size:
                break
        has_more = False
        if last_seen is not None:
            has_more = any(
                mapper.describe(
                    repository_id=self._clean_repository_id(external_repository_id),
                    path=path,
                ).external_file_id
                > last_seen
                for path in self._iter_files(repository_path)
            )
        return FilePage(
            items=tuple(items),
            next_cursor=last_seen if has_more else None,
            has_more=has_more,
        )

    async def get_metadata(self, external_file_id: str) -> NormalizedDocument:
        path = self._resolve_file(external_file_id)
        repository_id = PurePosixPath(external_file_id).parts[0]
        return LocalFileMapper(self._require_root()).describe(
            repository_id=repository_id, path=path
        )

    # -- incremental discovery -----------------------------------------
    @staticmethod
    def _decode_cursor(cursor: str | None) -> dict[str, list[Any]]:
        if not cursor:
            return {}
        try:
            payload = json.loads(cursor)
        except (TypeError, ValueError) as exc:
            raise IntegrationValidationError("The synchronization cursor is invalid") from exc
        files = payload.get("files") if isinstance(payload, dict) else None
        if not isinstance(files, dict):
            raise IntegrationValidationError("The synchronization cursor is invalid")
        if len(files) > MAX_SNAPSHOT_ENTRIES:
            raise IntegrationValidationError(
                "The synchronization cursor exceeds the local connector limit"
            )
        return files

    def _snapshot(self, repository_id: str) -> dict[str, list[Any]]:
        repository_path = self._repository_path(repository_id)
        snapshot: dict[str, list[Any]] = {}
        for path in self._iter_files(repository_path):
            relative = path.relative_to(repository_path).as_posix()
            stat = path.stat()
            snapshot[f"{repository_id}/{relative}"] = [
                stat.st_size,
                stat.st_mtime_ns,
                sha256_file(path),
            ]
            if len(snapshot) > MAX_SNAPSHOT_ENTRIES:
                raise IntegrationValidationError(
                    "The directory exceeds the local connector cursor limit of "
                    f"{MAX_SNAPSHOT_ENTRIES} files"
                )
        return snapshot

    async def detect_changes(
        self, external_repository_id: str, *, cursor: str | None = None
    ) -> ChangePage:
        repository_id = self._clean_repository_id(external_repository_id)
        previous = self._decode_cursor(cursor)
        current = self._snapshot(repository_id)
        changes: list[ChangeRecord] = []
        for identifier, state in sorted(current.items()):
            name = PurePosixPath(identifier).name
            previous_state = previous.get(identifier)
            if previous_state is None:
                kind = ChangeKind.CREATED
            elif list(previous_state) != list(state):
                kind = ChangeKind.UPDATED
            else:
                continue
            changes.append(
                ChangeRecord(
                    kind=kind,
                    external_file_id=identifier,
                    external_repository_id=repository_id,
                    external_version_id=str(state[2]),
                    file_name=name,
                    checksum=str(state[2]),
                )
            )
        for identifier in sorted(set(previous) - set(current)):
            changes.append(
                ChangeRecord(
                    kind=ChangeKind.DELETED,
                    external_file_id=identifier,
                    external_repository_id=repository_id,
                )
            )
        next_cursor = json.dumps(
            {"v": 1, "files": current}, separators=(",", ":"), sort_keys=True
        )
        return ChangePage(changes=tuple(changes), next_cursor=next_cursor, has_more=False)

    # -- content --------------------------------------------------------
    async def download(
        self,
        external_file_id: str,
        *,
        max_bytes: int,
        expected_version_id: str | None = None,
    ) -> DownloadedDocument:
        path = self._resolve_file(external_file_id)
        limit = int(max_bytes)
        if limit <= 0:
            raise IntegrationValidationError("max_bytes must be positive")
        stat = path.stat()
        if stat.st_size > limit:
            raise DownloadTooLargeError()
        temporary = Path(tempfile.gettempdir()) / f"vaultrag-integration-{uuid4().hex}"
        digest = hashlib.sha256()
        written = 0
        try:
            with path.open("rb") as source, temporary.open("wb") as destination:
                for block in iter(lambda: source.read(_COPY_CHUNK), b""):
                    written += len(block)
                    if written > limit:
                        raise DownloadTooLargeError()
                    digest.update(block)
                    destination.write(block)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        checksum = digest.hexdigest()
        if expected_version_id and expected_version_id != checksum:
            temporary.unlink(missing_ok=True)
            raise ProviderAuthenticationError(
                "The local file changed while it was being downloaded"
            )
        # The file is not executable and is outside the storage root; the sync
        # service hands it to ingestion, which copies it into private storage.
        temporary.chmod(0o600)
        return DownloadedDocument(
            temp_path=temporary,
            size=written,
            sha256=checksum,
            mime_type=(await self.get_metadata(external_file_id)).mime_type,
            external_version_id=checksum,
        )
