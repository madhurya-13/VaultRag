"""Map a filesystem entry onto the normalized document contract."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from app.integrations.base import NormalizedDocument

MIME_TYPES: dict[str, str] = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".csv": "text/csv",
}

DEFAULT_MIME_TYPE = "application/octet-stream"


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    """Stream a file through SHA-256 without loading it into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(chunk_size), b""):
            digest.update(block)
    return digest.hexdigest()


def mime_type_for(path: Path) -> str:
    return MIME_TYPES.get(path.suffix.lower(), DEFAULT_MIME_TYPE)


class LocalFileMapper:
    """Build :class:`NormalizedDocument` values for files under one root."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def describe(
        self,
        *,
        repository_id: str,
        path: Path,
        checksum: str | None = None,
    ) -> NormalizedDocument:
        stat = path.stat()
        # Identity is repository-relative, then prefixed once with the repository
        # id, so ``<repo>/<relative path>`` round-trips through ``_resolve_file``.
        relative = path.relative_to(self._root / repository_id).as_posix()
        digest = checksum or sha256_file(path)
        return NormalizedDocument(
            external_file_id=f"{repository_id}/{relative}",
            external_repository_id=repository_id,
            file_name=path.name,
            mime_type=mime_type_for(path),
            file_size=stat.st_size,
            checksum=digest,
            # A content digest is the only meaningful version identity a plain
            # directory offers; re-uploading identical bytes is a no-op.
            external_version_id=digest,
            modified_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
            created_at=datetime.fromtimestamp(stat.st_ctime, tz=timezone.utc),
            # No fetchable URL: nothing downstream may try to fetch this, which
            # removes an SSRF surface from the reference connector.
            source_url=None,
            source_path=relative,
            permissions=(),
            permissions_resolved=False,
            metadata={"size": stat.st_size, "mtime_ns": stat.st_mtime_ns},
        )
