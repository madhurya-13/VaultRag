"""Map Google Drive API payloads onto the provider-neutral contract.

Everything here treats the API response as untrusted input: identifiers are
validated by the value objects, sizes are parsed defensively, and an
unrecognised permission role becomes ``ExternalRole.UNKNOWN`` rather than being
upgraded to READ.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from app.integrations.base import (
    ExternalRole,
    ExternalVersion,
    NormalizedDocument,
    NormalizedPermission,
    PrincipalType,
    RepositoryDescriptor,
)

GOOGLE_NATIVE_MIME_PREFIX = "application/vnd.google-apps."

_ROLE_MAP = {
    "owner": ExternalRole.OWNER,
    "organizer": ExternalRole.OWNER,
    "writer": ExternalRole.WRITER,
    "fileOrganizer": ExternalRole.WRITER,
    "commenter": ExternalRole.COMMENTER,
    "reader": ExternalRole.READER,
    "publishedReader": ExternalRole.READER,
}

_PRINCIPAL_MAP = {
    "user": PrincipalType.USER,
    "group": PrincipalType.GROUP,
    "domain": PrincipalType.DOMAIN,
    "anyone": PrincipalType.ANYONE,
}


def _int_or_none(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _datetime_or_none(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _str_or_none(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    return None


def map_role(raw: Any) -> ExternalRole:
    if not isinstance(raw, str):
        return ExternalRole.UNKNOWN
    return _ROLE_MAP.get(raw, ExternalRole.UNKNOWN)


def map_repository(payload: Mapping[str, Any]) -> RepositoryDescriptor:
    identifier = _str_or_none(payload.get("id")) or "root"
    name = _str_or_none(payload.get("name")) or identifier
    return RepositoryDescriptor(
        external_repository_id=identifier,
        name=name[:512],
        repository_kind="DRIVE",
        metadata={},
    )


def map_document(payload: Mapping[str, Any], *, repository_id: str) -> NormalizedDocument:
    identifier = _str_or_none(payload.get("id"))
    if identifier is None:
        raise ValueError("Google Drive file payload has no id")
    mime_type = _str_or_none(payload.get("mimeType"))
    native_export = None
    if mime_type and mime_type.startswith(GOOGLE_NATIVE_MIME_PREFIX):
        # Docs/Sheets/Slides are exported as PDF; ingestion only accepts PDF.
        native_export = "application/pdf"
    return NormalizedDocument(
        external_file_id=identifier,
        external_repository_id=repository_id,
        file_name=(
            _str_or_none(payload.get("name"))
            or f"drive-document-{identifier[:32]}"
        ),
        mime_type=mime_type,
        file_size=_int_or_none(payload.get("size")),
        # Drive's md5 is not a SHA-256. It is used only as a change signal; the
        # sync layer always computes the real digest from the bytes it stores.
        checksum=None,
        external_version_id=_str_or_none(payload.get("version"))
        or _str_or_none(payload.get("modifiedTime")),
        modified_at=_datetime_or_none(payload.get("modifiedTime")),
        created_at=_datetime_or_none(payload.get("createdTime")),
        source_url=_str_or_none(payload.get("webViewLink")),
        source_path=_str_or_none(payload.get("name")),
        native_export_mime_type=native_export,
        metadata={
            "md5_checksum": _str_or_none(payload.get("md5Checksum")),
            "parents": list(payload.get("parents") or []),
        },
    )


def map_permission(payload: Mapping[str, Any]) -> NormalizedPermission | None:
    identifier = _str_or_none(payload.get("id"))
    principal_type = _PRINCIPAL_MAP.get(str(payload.get("type") or ""))
    if identifier is None or principal_type is None:
        return None
    if payload.get("deleted") is True:
        return None
    principal_id = (
        _str_or_none(payload.get("emailAddress"))
        or _str_or_none(payload.get("domain"))
        or identifier
    )
    return NormalizedPermission(
        principal_id=principal_id,
        principal_type=principal_type,
        role=map_role(payload.get("role")),
        inherited=False,
        principal_display_name=_str_or_none(payload.get("displayName")),
        raw={"permission_id": identifier},
    )


def map_version(payload: Mapping[str, Any], *, is_current: bool) -> ExternalVersion | None:
    identifier = _str_or_none(payload.get("id"))
    if identifier is None:
        return None
    return ExternalVersion(
        external_version_id=identifier,
        modified_at=_datetime_or_none(payload.get("modifiedTime")),
        file_size=_int_or_none(payload.get("size")),
        checksum=None,
        is_current=is_current,
    )
