"""Google Drive connector.

The connector talks to the Drive v3 REST API with a tenant-supplied OAuth
credential. It never sees VaultRAG storage, sessions or authorization rules:
downloads land in a private temporary file and the sync service decides what to
ingest.

Security notes
--------------
* Tokens are held in memory only and never logged or returned.
* Every provider-supplied identifier is passed through the value objects, which
  bound its length and reject empty values.
* Downloads are size-capped after the bytes are received and the SHA-256 is
  always recomputed locally.
"""

from __future__ import annotations

import hashlib
import tempfile
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, ClassVar
from uuid import uuid4

import httpx

from app.core.security_constants import PROVIDER_GOOGLE_DRIVE
from app.integrations.base import (
    Capability,
    ChangeKind,
    ChangePage,
    ChangeRecord,
    ConnectionState,
    Connector,
    DownloadedDocument,
    ExternalVersion,
    FilePage,
    NormalizedDocument,
    NormalizedPermission,
    RepositoryDescriptor,
)
from app.integrations.exceptions import (
    DownloadTooLargeError,
    ProviderAuthenticationError,
    ProviderRateLimitedError,
    ProviderRequestError,
)
from app.integrations.providers.google_drive.mapper import (
    map_document,
    map_permission,
    map_repository,
    map_version,
)
from app.integrations.registry import register_provider

API_BASE = "https://www.googleapis.com/drive/v3"
TOKEN_URL = "https://oauth2.googleapis.com/token"
MAX_PAGE_SIZE = 1000

_FILE_FIELDS = (
    "id,name,mimeType,size,md5Checksum,modifiedTime,createdTime,webViewLink,"
    "parents,version,trashed"
)


@register_provider
class GoogleDriveConnector(Connector):
    provider = PROVIDER_GOOGLE_DRIVE
    display_name = "Google Drive"
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
            Capability.NATIVE_EXPORT,
        }
    )

    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        super().__init__(transport=transport)
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._client_id: str | None = None
        self._client_secret: str | None = None
        self._token_expires_at: datetime | None = None
        self._client: httpx.AsyncClient | None = None

    # -- lifecycle ------------------------------------------------------
    async def authenticate(self, credentials: Mapping[str, Any]) -> None:
        access_token = credentials.get("access_token")
        refresh_token = credentials.get("refresh_token")
        if not access_token and not refresh_token:
            raise ProviderAuthenticationError(
                "Google Drive requires an access_token or refresh_token"
            )
        self._access_token = str(access_token) if access_token else None
        self._refresh_token = str(refresh_token) if refresh_token else None
        self._client_id = str(credentials["client_id"]) if credentials.get("client_id") else None
        self._client_secret = (
            str(credentials["client_secret"]) if credentials.get("client_secret") else None
        )
        self._client = httpx.AsyncClient(
            base_url=API_BASE,
            transport=self._transport,
            timeout=httpx.Timeout(20.0, connect=5.0),
            follow_redirects=True,
        )
        if self._access_token is None:
            await self._refresh_access_token()

    async def disconnect(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _refresh_access_token(self) -> None:
        if not self._refresh_token or not self._client_id or not self._client_secret:
            raise ProviderAuthenticationError(
                "Google Drive refresh requires refresh_token, client_id and client_secret"
            )
        async with httpx.AsyncClient(
            transport=self._transport, timeout=httpx.Timeout(20.0, connect=5.0)
        ) as client:
            response = await client.post(
                TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": self._refresh_token,
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
            )
        if response.status_code != 200:
            raise ProviderAuthenticationError(
                "Google Drive rejected the stored credential"
            )
        payload = response.json()
        token = payload.get("access_token")
        if not isinstance(token, str) or not token:
            raise ProviderAuthenticationError(
                "Google Drive returned no access token"
            )
        self._access_token = token
        expires_in = payload.get("expires_in")
        if isinstance(expires_in, (int, float)):
            self._token_expires_at = datetime.now(timezone.utc) + timedelta(
                seconds=float(expires_in))
        if isinstance(payload.get("refresh_token"), str):
            self._refresh_token = payload["refresh_token"]

    def _request_client(self) -> httpx.AsyncClient:
        if self._client is None or self._access_token is None:
            raise ProviderAuthenticationError("Google Drive is not authenticated")
        return self._client

    async def _get(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        client = self._request_client()
        try:
            response = await client.get(
                path,
                params=params,
                headers={"Authorization": f"Bearer {self._access_token}"},
            )
        except httpx.HTTPError as exc:
            raise ProviderRequestError(
                "Google Drive did not respond"
            ) from exc
        if response.status_code in {401, 403} and await self._is_auth_problem(response):
            raise ProviderAuthenticationError(
                "Google Drive rejected the stored credential"
            )
        if response.status_code == 429:
            raise ProviderRateLimitedError(
                "Google Drive rate-limited the request"
            )
        if response.status_code >= 400:
            raise ProviderRequestError(
                "Google Drive rejected the request"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderRequestError(
                "Google Drive returned an unreadable response"
            ) from exc
        if not isinstance(payload, dict):
            raise ProviderRequestError("Google Drive returned an unexpected payload")
        return payload

    @staticmethod
    async def _is_auth_problem(response: httpx.Response) -> bool:
        try:
            payload = response.json()
        except ValueError:
            return response.status_code == 401
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict):
            return error.get("code") in {401, 403} and str(
                error.get("status") or ""
            ).upper() in {"UNAUTHENTICATED", "PERMISSION_DENIED", ""}
        return response.status_code == 401

    async def validate_connection(self) -> ConnectionState:
        try:
            await self._get("/about", params={"fields": "user"})
        except ProviderAuthenticationError:
            return ConnectionState(
                ok=False,
                detail="Google Drive authorization has expired",
                reauth_required=True,
            )
        except ProviderRequestError as exc:
            return ConnectionState(ok=False, detail=str(exc))
        return ConnectionState(ok=True)

    # -- discovery ------------------------------------------------------
    async def list_repositories(self) -> tuple[RepositoryDescriptor, ...]:
        repositories: list[RepositoryDescriptor] = [
            RepositoryDescriptor(
                external_repository_id="root",
                name="My Drive",
                repository_kind="DRIVE",
            )
        ]
        payload = await self._get(
            "/drives",
            params={"pageSize": 100, "fields": "drives(id,name)"},
        )
        for drive in payload.get("drives") or []:
            if isinstance(drive, dict):
                repositories.append(map_repository(drive))
        return tuple(repositories)

    async def list_files(
        self,
        external_repository_id: str,
        *,
        cursor: str | None = None,
        page_size: int = 100,
    ) -> FilePage:
        params: dict[str, Any] = {
            "q": f"'{external_repository_id}' in parents and trashed = false",
            "pageSize": max(1, min(int(page_size), MAX_PAGE_SIZE)),
            "fields": f"nextPageToken,files({_FILE_FIELDS})",
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
        }
        if cursor:
            params["pageToken"] = cursor
        payload = await self._get("/files", params=params)
        items = tuple(
            map_document(item, repository_id=external_repository_id)
            for item in payload.get("files") or []
            if isinstance(item, dict)
        )
        next_cursor = payload.get("nextPageToken")
        return FilePage(
            items=items,
            next_cursor=next_cursor if isinstance(next_cursor, str) else None,
            has_more=bool(next_cursor),
        )

    async def get_metadata(self, external_file_id: str) -> NormalizedDocument:
        payload = await self._get(
            f"/files/{external_file_id}",
            params={"fields": _FILE_FIELDS, "supportsAllDrives": "true"},
        )
        repository_id = "root"
        parents = payload.get("parents")
        if isinstance(parents, list) and parents and isinstance(parents[0], str):
            repository_id = parents[0]
        return map_document(payload, repository_id=repository_id)

    async def get_permissions(
        self, external_file_id: str
    ) -> tuple[NormalizedPermission, ...]:
        payload = await self._get(
            f"/files/{external_file_id}/permissions",
            params={
                "fields": (
                    "permissions(id,type,role,emailAddress,displayName,domain,"
                    "deleted)"
                ),
                "supportsAllDrives": "true",
            },
        )
        permissions: list[NormalizedPermission] = []
        for item in payload.get("permissions") or []:
            if not isinstance(item, dict):
                continue
            mapped = map_permission(item)
            if mapped is not None:
                permissions.append(mapped)
        return tuple(permissions)

    async def detect_changes(
        self, external_repository_id: str, *, cursor: str | None = None
    ) -> ChangePage:
        if not cursor:
            payload = await self._get("/changes/startPageToken")
            token = payload.get("startPageToken")
            if not isinstance(token, str) or not token:
                raise ProviderRequestError(
                    "Google Drive returned no change cursor"
                )
            return ChangePage(changes=(), next_cursor=token, has_more=False)

        payload = await self._get(
            "/changes",
            params={
                "pageToken": cursor,
                "fields": (
                    "nextPageToken,newStartPageToken,"
                    "changes(changeType,fileId,removed,file(id,name,mimeType,size,"
                    "modifiedTime,createdTime,webViewLink,parents,version))"
                ),
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
            },
        )
        changes: list[ChangeRecord] = []
        for item in payload.get("changes") or []:
            if not isinstance(item, dict):
                continue
            file_id = item.get("fileId")
            if not isinstance(file_id, str) or not file_id:
                continue
            if item.get("removed") is True:
                changes.append(
                    ChangeRecord(
                        kind=ChangeKind.DELETED,
                        external_file_id=file_id,
                        external_repository_id=external_repository_id,
                        deleted=True,
                    )
                )
                continue
            file_payload = item.get("file")
            if not isinstance(file_payload, dict):
                continue
            parents = file_payload.get("parents")
            repo = (
                parents[0]
                if isinstance(parents, list)
                and parents
                and isinstance(parents[0], str)
                else external_repository_id
            )
            changes.append(
                ChangeRecord(
                    kind=(
                        ChangeKind.CREATED
                        if item.get("changeType") == "fileCreated"
                        else ChangeKind.UPDATED
                    ),
                    external_file_id=file_id,
                    external_repository_id=repo,
                    external_version_id=str(file_payload.get("version") or "")
                    or None,
                    file_name=str(file_payload.get("name") or "") or None,
                    modified_at=None,
                )
            )
        next_cursor = payload.get("nextPageToken") or payload.get("newStartPageToken")
        return ChangePage(
            changes=tuple(changes),
            next_cursor=next_cursor if isinstance(next_cursor, str) else None,
            has_more=bool(payload.get("nextPageToken")),
        )

    async def get_versions(self, external_file_id: str) -> tuple[ExternalVersion, ...]:
        payload = await self._get(
            f"/files/{external_file_id}/revisions",
            params={"fields": "revisions(id,modifiedTime,size)"},
        )
        raw_versions = [
            item for item in payload.get("revisions") or [] if isinstance(item, dict)
        ]
        versions = [
            map_version(item, is_current=index == len(raw_versions) - 1)
            for index, item in enumerate(raw_versions)
        ]
        return tuple(version for version in versions if version is not None)

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
        metadata = await self.get_metadata(external_file_id)
        client = self._request_client()
        headers = {"Authorization": f"Bearer {self._access_token}"}

        if metadata.native_export_mime_type:
            url = f"/files/{external_file_id}/export"
            params = {"mimeType": metadata.native_export_mime_type}
        else:
            url = f"/files/{external_file_id}"
            params = {"alt": "media", "supportsAllDrives": "true"}

        try:
            response = await client.get(url, params=params, headers=headers)
        except httpx.HTTPError as exc:
            raise ProviderRequestError("Google Drive download failed") from exc
        if response.status_code >= 400:
            raise ProviderRequestError("Google Drive download failed")
        content = response.content
        if len(content) > max_bytes:
            raise DownloadTooLargeError()

        temporary = Path(tempfile.gettempdir()) / f"vaultrag-drive-{uuid4().hex}"
        temporary.write_bytes(content)
        temporary.chmod(0o600)
        return DownloadedDocument(
            temp_path=temporary,
            size=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            mime_type=metadata.native_export_mime_type or metadata.mime_type,
            external_version_id=metadata.external_version_id,
        )
