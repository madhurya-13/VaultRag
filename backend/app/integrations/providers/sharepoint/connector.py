"""SharePoint / OneDrive connector (Microsoft Graph).

External identity
-----------------
A Graph item id is only unique within one drive, so VaultRAG's
``external_file_id`` is ``"<drive_id>:<item_id>"``. Repositories are document
libraries (Graph ``drive`` objects).

Security notes
--------------
* The only accepted absolute URL is a Graph continuation link whose host is
  ``graph.microsoft.com`` and whose scheme is HTTPS. Everything else is
  rejected, which closes the SSRF surface a provider-supplied ``nextLink``
  would otherwise open.
* A permission role that is not recognised maps to ``UNKNOWN`` and produces no
  grant downstream.
"""

from __future__ import annotations

import hashlib
import tempfile
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import quote, urlparse
from uuid import uuid4

import httpx

from app.core.security_constants import PROVIDER_SHAREPOINT
from app.integrations.base import (
    Capability,
    ChangeKind,
    ChangePage,
    ChangeRecord,
    ConnectionState,
    Connector,
    DownloadedDocument,
    ExternalRole,
    ExternalVersion,
    FilePage,
    NormalizedDocument,
    NormalizedPermission,
    PrincipalType,
    RepositoryDescriptor,
)
from app.integrations.exceptions import (
    DownloadTooLargeError,
    IntegrationValidationError,
    ProviderAuthenticationError,
    ProviderRateLimitedError,
    ProviderRequestError,
)
from app.integrations.registry import register_provider

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_ALLOWED_HOSTS = {"graph.microsoft.com"}
_ITEM_SELECT = (
    "id,name,size,file,folder,lastModifiedDateTime,createdDateTime,webUrl,"
    "eTag,cTag,parentReference"
)
_ROLE_MAP = {
    "owner": ExternalRole.OWNER,
    "fullcontrol": ExternalRole.OWNER,
    "write": ExternalRole.WRITER,
    "edit": ExternalRole.WRITER,
    "read": ExternalRole.READER,
    "restrictedview": ExternalRole.READER,
}


def _validated_url(raw: str) -> str:
    parsed = urlparse(raw)
    if parsed.scheme != "https" or parsed.hostname not in _ALLOWED_HOSTS:
        raise ProviderRequestError("The provider returned an unsafe continuation URL")
    return raw


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
    return value if isinstance(value, str) and value.strip() else None


def split_item_id(external_file_id: str) -> tuple[str, str]:
    """Split ``drive:item`` and reject anything malformed."""
    if not isinstance(external_file_id, str) or ":" not in external_file_id:
        raise IntegrationValidationError(
            "The SharePoint file identifier must be '<drive_id>:<item_id>'"
        )
    drive_id, item_id = external_file_id.split(":", 1)
    if not drive_id.strip() or not item_id.strip():
        raise IntegrationValidationError(
            "The SharePoint file identifier must be '<drive_id>:<item_id>'"
        )
    return drive_id.strip(), item_id.strip()


@register_provider
class SharePointConnector(Connector):
    provider = PROVIDER_SHAREPOINT
    display_name = "Microsoft SharePoint"
    supports_oauth = True
    capabilities: ClassVar[frozenset[Capability]] = frozenset(
        {
            Capability.LIST_REPOSITORIES,
            Capability.LIST_FILES,
            Capability.GET_METADATA,
            Capability.DOWNLOAD,
            Capability.GET_PERMISSIONS,
            Capability.DETECT_CHANGES,
            Capability.GET_VERSIONS,
        }
    )

    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        super().__init__(transport=transport)
        self._access_token: str | None = None
        self._client: httpx.AsyncClient | None = None

    # -- lifecycle ------------------------------------------------------
    async def authenticate(self, credentials: Mapping[str, Any]) -> None:
        access_token = credentials.get("access_token")
        if not isinstance(access_token, str) or not access_token.strip():
            raise ProviderAuthenticationError(
                "SharePoint requires an access_token"
            )
        self._access_token = access_token.strip()
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(20.0, connect=5.0),
            transport=self._transport,
            follow_redirects=True,
        )

    async def disconnect(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _get_json(
        self,
        path_or_url: str,
        *,
        params: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self._client is None or self._access_token is None:
            raise ProviderAuthenticationError("SharePoint is not authenticated")
        url = (
            path_or_url
            if path_or_url.startswith("https://")
            else f"{GRAPH_BASE}{path_or_url}"
        )
        if path_or_url.startswith("https://"):
            url = _validated_url(url)
        try:
            response = await self._client.get(
                url,
                params=params,
                headers={"Authorization": f"Bearer {self._access_token}"},
            )
        except httpx.HTTPError as exc:
            raise ProviderRequestError("Microsoft Graph did not respond") from exc
        if response.status_code == 401:
            raise ProviderAuthenticationError(
                "Microsoft Graph rejected the stored credential"
            )
        if response.status_code == 429:
            raise ProviderRateLimitedError(
                "Microsoft Graph rate-limited the request"
            )
        if response.status_code >= 400:
            raise ProviderRequestError("Microsoft Graph rejected the request")
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderRequestError(
                "Microsoft Graph returned an unreadable response"
            ) from exc
        if not isinstance(payload, dict):
            raise ProviderRequestError(
                "Microsoft Graph returned an unexpected payload"
            )
        return payload

    async def validate_connection(self) -> ConnectionState:
        try:
            await self._get_json("/drives", params={"$top": 1})
        except ProviderAuthenticationError:
            return ConnectionState(
                ok=False,
                detail="SharePoint authorization has expired",
                reauth_required=True,
            )
        except ProviderRequestError as exc:
            return ConnectionState(ok=False, detail=str(exc))
        return ConnectionState(ok=True)

    # -- discovery ------------------------------------------------------
    async def list_repositories(self) -> tuple[RepositoryDescriptor, ...]:
        payload = await self._get_json(
            "/drives", params={"$select": "id,name,description,driveType"}
        )
        repositories: list[RepositoryDescriptor] = []
        for drive in payload.get("value") or []:
            if not isinstance(drive, dict):
                continue
            identifier = _str_or_none(drive.get("id"))
            if identifier is None:
                continue
            repositories.append(
                RepositoryDescriptor(
                    external_repository_id=identifier,
                    name=(_str_or_none(drive.get("name")) or identifier)[:512],
                    repository_kind="LIBRARY",
                    description=(
                        _str_or_none(drive.get("description")) or ""
                    )[:512]
                    or None,
                )
            )
        return tuple(repositories)

    @staticmethod
    def _map_document(
        payload: Mapping[str, Any], *, repository_id: str
    ) -> NormalizedDocument:
        identifier = _str_or_none(payload.get("id"))
        if identifier is None:
            raise ProviderRequestError("Microsoft Graph item has no id")
        file_facet = payload.get("file")
        mime_type = (
            _str_or_none(file_facet.get("mimeType"))
            if isinstance(file_facet, dict)
            else None
        )
        return NormalizedDocument(
            external_file_id=f"{repository_id}:{identifier}",
            external_repository_id=repository_id,
            file_name=(_str_or_none(payload.get("name")) or identifier)[:512],
            mime_type=mime_type,
            file_size=_int_or_none(payload.get("size")),
            checksum=None,
            external_version_id=_str_or_none(payload.get("cTag"))
            or _str_or_none(payload.get("eTag")),
            modified_at=_datetime_or_none(payload.get("lastModifiedDateTime")),
            created_at=_datetime_or_none(payload.get("createdDateTime")),
            source_url=_str_or_none(payload.get("webUrl")),
            source_path=_str_or_none(payload.get("name")),
            metadata={},
        )

    async def list_files(
        self,
        external_repository_id: str,
        *,
        cursor: str | None = None,
        page_size: int = 100,
    ) -> FilePage:
        if cursor:
            payload = await self._get_json(cursor)
        else:
            payload = await self._get_json(
                f"/drives/{quote(external_repository_id, safe='')}/root/children",
                params={
                    "$select": _ITEM_SELECT,
                    "$top": max(1, min(int(page_size), 999)),
                },
            )
        items = tuple(
            self._map_document(item, repository_id=external_repository_id)
            for item in payload.get("value") or []
            if isinstance(item, dict) and isinstance(item.get("file"), dict)
        )
        next_link = _str_or_none(payload.get("@odata.nextLink"))
        return FilePage(
            items=items,
            next_cursor=_validated_url(next_link) if next_link else None,
            has_more=next_link is not None,
        )

    async def get_metadata(self, external_file_id: str) -> NormalizedDocument:
        drive_id, item_id = split_item_id(external_file_id)
        payload = await self._get_json(
            f"/drives/{quote(drive_id, safe='')}/items/{quote(item_id, safe='')}",
            params={"$select": _ITEM_SELECT},
        )
        return self._map_document(payload, repository_id=drive_id)

    async def get_permissions(
        self, external_file_id: str
    ) -> tuple[NormalizedPermission, ...]:
        drive_id, item_id = split_item_id(external_file_id)
        payload = await self._get_json(
            f"/drives/{quote(drive_id, safe='')}/items/{quote(item_id, safe='')}"
            "/permissions"
        )
        permissions: list[NormalizedPermission] = []
        for item in payload.get("value") or []:
            if not isinstance(item, dict):
                continue
            roles = item.get("roles")
            role = ExternalRole.UNKNOWN
            if isinstance(roles, list):
                for raw_role in roles:
                    mapped = _ROLE_MAP.get(str(raw_role).replace(" ", "").lower())
                    if mapped is not None:
                        role = mapped
                        break
            link = item.get("link")
            if isinstance(link, dict) and str(link.get("scope")).lower() == "anonymous":
                permissions.append(
                    NormalizedPermission(
                        principal_id="anonymous",
                        principal_type=PrincipalType.ANYONE,
                        role=role,
                    )
                )
                continue
            grantee: Mapping[str, Any] | None = None
            principal_type = PrincipalType.USER
            granted_to = item.get("grantedToV2")
            for key, mapped_type in (
                ("user", PrincipalType.USER),
                ("group", PrincipalType.GROUP),
                ("siteGroup", PrincipalType.ROLE),
                ("application", PrincipalType.ROLE),
            ):
                candidate = (
                    granted_to.get(key) if isinstance(granted_to, dict) else None
                )
                if isinstance(candidate, dict):
                    grantee = candidate
                    principal_type = mapped_type
                    break
            if grantee is None:
                continue
            principal_id = (
                _str_or_none(grantee.get("email"))
                or _str_or_none(grantee.get("id"))
                or _str_or_none(grantee.get("displayName"))
            )
            if principal_id is None:
                continue
            permissions.append(
                NormalizedPermission(
                    principal_id=principal_id,
                    principal_type=principal_type,
                    role=role,
                    principal_display_name=_str_or_none(grantee.get("displayName")),
                )
            )
        return tuple(permissions)

    async def detect_changes(
        self, external_repository_id: str, *, cursor: str | None = None
    ) -> ChangePage:
        if cursor and cursor.startswith("https://"):
            payload = await self._get_json(cursor)
        else:
            params: dict[str, Any] = {}
            if cursor:
                params["token"] = cursor
            payload = await self._get_json(
                f"/drives/{quote(external_repository_id, safe='')}/root/delta",
                params=params or None,
            )
        changes: list[ChangeRecord] = []
        for item in payload.get("value") or []:
            if not isinstance(item, dict):
                continue
            identifier = _str_or_none(item.get("id"))
            if identifier is None:
                continue
            if "deleted" in item:
                changes.append(
                    ChangeRecord(
                        kind=ChangeKind.DELETED,
                        external_file_id=f"{external_repository_id}:{identifier}",
                        external_repository_id=external_repository_id,
                        deleted=True,
                    )
                )
                continue
            if not isinstance(item.get("file"), dict):
                continue
            changes.append(
                ChangeRecord(
                    kind=ChangeKind.UPDATED,
                    external_file_id=f"{external_repository_id}:{identifier}",
                    external_repository_id=external_repository_id,
                    external_version_id=_str_or_none(item.get("cTag")),
                    file_name=_str_or_none(item.get("name")),
                    modified_at=_datetime_or_none(item.get("lastModifiedDateTime")),
                )
            )
        next_link = _str_or_none(payload.get("@odata.nextLink"))
        delta_link = _str_or_none(payload.get("@odata.deltaLink"))
        next_cursor = (
            _validated_url(next_link)
            if next_link
            else _validated_url(delta_link)
            if delta_link
            else None
        )
        return ChangePage(
            changes=tuple(changes),
            next_cursor=next_cursor,
            has_more=next_link is not None,
        )

    async def get_versions(self, external_file_id: str) -> tuple[ExternalVersion, ...]:
        drive_id, item_id = split_item_id(external_file_id)
        payload = await self._get_json(
            f"/drives/{quote(drive_id, safe='')}/items/{quote(item_id, safe='')}"
            "/versions"
        )
        raw_versions = [
            item for item in payload.get("value") or [] if isinstance(item, dict)
        ]
        versions: list[ExternalVersion] = []
        for index, item in enumerate(raw_versions):
            identifier = _str_or_none(item.get("id"))
            if identifier is None:
                continue
            versions.append(
                ExternalVersion(
                    external_version_id=identifier,
                    modified_at=_datetime_or_none(item.get("lastModifiedDateTime")),
                    file_size=_int_or_none(item.get("size")),
                    is_current=index == len(raw_versions) - 1,
                )
            )
        return tuple(versions)

    # -- content --------------------------------------------------------
    async def download(
        self,
        external_file_id: str,
        *,
        max_bytes: int,
        expected_version_id: str | None = None,
    ) -> DownloadedDocument:
        if max_bytes <= 0:
            raise DownloadTooLargeError()
        drive_id, item_id = split_item_id(external_file_id)
        if self._client is None or self._access_token is None:
            raise ProviderAuthenticationError("SharePoint is not authenticated")
        try:
            response = await self._client.get(
                f"{GRAPH_BASE}/drives/{quote(drive_id, safe='')}/items/"
                f"{quote(item_id, safe='')}/content",
                headers={"Authorization": f"Bearer {self._access_token}"},
            )
        except httpx.HTTPError as exc:
            raise ProviderRequestError("Microsoft Graph download failed") from exc
        if response.status_code == 401:
            raise ProviderAuthenticationError(
                "Microsoft Graph rejected the stored credential"
            )
        if response.status_code >= 400:
            raise ProviderRequestError("Microsoft Graph download failed")
        content = response.content
        if len(content) > max_bytes:
            raise DownloadTooLargeError()

        temporary = Path(tempfile.gettempdir()) / f"vaultrag-graph-{uuid4().hex}"
        temporary.write_bytes(content)
        temporary.chmod(0o600)
        metadata = await self.get_metadata(external_file_id)
        return DownloadedDocument(
            temp_path=temporary,
            size=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            mime_type=metadata.mime_type,
            external_version_id=metadata.external_version_id,
        )
