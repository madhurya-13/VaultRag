"""HTTP provider connectors, exercised with a deterministic transport.

No network access and no real credentials are used. The mock verifies the
request shape, then the test checks normalization, role mapping and download
guards.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app.integrations.base import (
    ChangeKind,
    ExternalRole,
    PrincipalType,
)
from app.integrations.exceptions import DownloadTooLargeError
from app.integrations.providers.google_drive import GoogleDriveConnector
from app.integrations.providers.sharepoint import SharePointConnector
from app.integrations.providers.sharepoint.connector import split_item_id

PDF_BYTES = b"%PDF-1.4 synthetic provider payload"


def _drive_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith("/about"):
        return httpx.Response(200, json={"user": {"emailAddress": "a@example.com"}})
    if path.endswith("/drives"):
        return httpx.Response(200, json={"drives": [{"id": "shared1", "name": "Shared"}]})
    if path.endswith("/changes/startPageToken"):
        return httpx.Response(200, json={"startPageToken": "token-1"})
    if path.endswith("/changes"):
        return httpx.Response(
            200,
            json={
                "newStartPageToken": "token-2",
                "changes": [
                    {
                        "changeType": "fileUpdated",
                        "fileId": "file1",
                        "file": {
                            "id": "file1",
                            "name": "handbook.pdf",
                            "parents": ["root"],
                            "version": "7",
                        },
                    },
                    {
                        "changeType": "fileDeleted",
                        "fileId": "file2",
                        "removed": True,
                    },
                ],
            },
        )
    if path.endswith("/files/file1/permissions"):
        return httpx.Response(
            200,
            json={
                "permissions": [
                    {
                        "id": "p1",
                        "type": "user",
                        "role": "writer",
                        "emailAddress": "alice@example.com",
                        "displayName": "Alice",
                    },
                    {
                        "id": "p2",
                        "type": "anyone",
                        "role": "reader",
                    },
                    {
                        "id": "p3",
                        "type": "user",
                        "role": "commenter",
                        "emailAddress": "bob@example.com",
                    },
                ]
            },
        )
    if path.endswith("/files/file1/revisions"):
        return httpx.Response(
            200,
            json={
                "revisions": [
                    {"id": "1", "size": "10"},
                    {"id": "2", "size": "12"},
                ]
            },
        )
    if path.endswith("/files/file1/export"):
        return httpx.Response(200, content=PDF_BYTES)
    if path.endswith("/files/file1"):
        return httpx.Response(
            200,
            json={
                "id": "file1",
                "name": "handbook.pdf",
                "mimeType": "application/vnd.google-apps.document",
                "parents": ["root"],
                "version": "7",
                "modifiedTime": "2026-11-01T10:00:00Z",
                "webViewLink": "https://docs.google.com/document/d/file1",
            },
        )
    if path.endswith("/files"):
        return httpx.Response(
            200,
            json={
                "files": [
                    {
                        "id": "file1",
                        "name": "handbook.pdf",
                        "mimeType": "application/pdf",
                        "size": "123",
                        "parents": ["root"],
                    }
                ],
                "nextPageToken": "page-2",
            },
        )
    return httpx.Response(404, json={"error": {"code": 404}})


def _connector() -> GoogleDriveConnector:
    connector = GoogleDriveConnector(transport=httpx.MockTransport(_drive_handler))
    asyncio.run(connector.authenticate({"access_token": "token"}))
    return connector


def test_google_drive_normalizes_files_permissions_and_versions():
    connector = _connector()
    try:
        repositories = asyncio.run(connector.list_repositories())
        assert repositories[0].external_repository_id == "root"
        assert repositories[1].name == "Shared"

        page = asyncio.run(connector.list_files("root", page_size=10))
        assert page.items[0].file_name == "handbook.pdf"
        assert page.items[0].file_size == 123
        assert page.has_more is True
        assert page.next_cursor == "page-2"

        metadata = asyncio.run(connector.get_metadata("file1"))
        assert metadata.ingestible_mime_type == "application/pdf"
        assert metadata.source_url == "https://docs.google.com/document/d/file1"

        permissions = asyncio.run(connector.get_permissions("file1"))
        assert permissions[0].role is ExternalRole.WRITER
        assert permissions[0].principal_type is PrincipalType.USER
        assert permissions[1].principal_type is PrincipalType.ANYONE
        assert permissions[2].role is ExternalRole.COMMENTER

        versions = asyncio.run(connector.get_versions("file1"))
        assert [version.is_current for version in versions] == [False, True]

        downloaded = asyncio.run(
            connector.download("file1", max_bytes=1024)
        )
        try:
            assert downloaded.sha256 == __import__("hashlib").sha256(
                PDF_BYTES
            ).hexdigest()
            assert downloaded.mime_type == "application/pdf"
        finally:
            downloaded.cleanup()
    finally:
        asyncio.run(connector.disconnect())


def test_google_drive_changes_and_size_cap():
    connector = _connector()
    try:
        start = asyncio.run(connector.detect_changes("root"))
        assert start.next_cursor == "token-1"
        page = asyncio.run(connector.detect_changes("root", cursor="token-1"))
        kinds = {change.kind for change in page.changes}
        assert ChangeKind.UPDATED in kinds
        assert ChangeKind.DELETED in kinds
        assert page.next_cursor == "token-2"

        with pytest.raises(DownloadTooLargeError):
            asyncio.run(connector.download("file1", max_bytes=4))
    finally:
        asyncio.run(connector.disconnect())


def _graph_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith("/drives"):
        return httpx.Response(
            200,
            json={"value": [{"id": "d1", "name": "Documents", "driveType": "documentLibrary"}]},
        )
    if path.endswith("/root/children"):
        return httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": "i1",
                        "name": "policy.pdf",
                        "size": 42,
                        "file": {"mimeType": "application/pdf"},
                        "cTag": "ctag-1",
                        "webUrl": "https://contoso.sharepoint.com/policy.pdf",
                        "lastModifiedDateTime": "2026-11-02T08:00:00Z",
                    },
                    {
                        "id": "folder1",
                        "name": "Folder",
                        "folder": {"childCount": 2},
                    },
                ],
                "@odata.nextLink": "https://graph.microsoft.com/v1.0/drives/d1/root/children?$skiptoken=x",
            },
        )
    if path.endswith("/items/i1/permissions"):
        return httpx.Response(
            200,
            json={
                "value": [
                    {
                        "roles": ["read"],
                        "grantedToV2": {
                            "user": {
                                "id": "u1",
                                "email": "alice@example.com",
                                "displayName": "Alice",
                            }
                        },
                    },
                    {
                        "roles": ["owner"],
                        "grantedToV2": {
                            "group": {"id": "g1", "displayName": "HR"}
                        },
                    },
                    {"roles": ["read"], "link": {"scope": "anonymous"}},
                ]
            },
        )
    if path.endswith("/items/i1/versions"):
        return httpx.Response(
            200,
            json={
                "value": [
                    {"id": "1.0", "size": 40, "lastModifiedDateTime": "2026-10-01T00:00:00Z"},
                    {"id": "2.0", "size": 42, "lastModifiedDateTime": "2026-11-02T00:00:00Z"},
                ]
            },
        )
    if path.endswith("/items/i1/content"):
        return httpx.Response(302, headers={"Location": "https://graph.microsoft.com/v1.0/blob"})
    if path.endswith("/blob"):
        return httpx.Response(200, content=PDF_BYTES)
    if path.endswith("/items/i1"):
        return httpx.Response(
            200,
            json={
                "id": "i1",
                "name": "policy.pdf",
                "size": 42,
                "file": {"mimeType": "application/pdf"},
                "cTag": "ctag-1",
                "webUrl": "https://contoso.sharepoint.com/policy.pdf",
                "lastModifiedDateTime": "2026-11-02T08:00:00Z",
            },
        )
    if path.endswith("/root/delta"):
        return httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": "i1",
                        "name": "policy.pdf",
                        "file": {"mimeType": "application/pdf"},
                        "cTag": "ctag-2",
                    },
                    {"id": "i2", "deleted": {"state": "deleted"}},
                ],
                "@odata.deltaLink": "https://graph.microsoft.com/v1.0/drives/d1/root/delta?token=abc",
            },
        )
    return httpx.Response(404, json={"error": {"code": "itemNotFound"}})


def test_sharepoint_normalizes_drives_files_and_acl():
    connector = SharePointConnector(transport=httpx.MockTransport(_graph_handler))
    asyncio.run(connector.authenticate({"access_token": "token"}))
    try:
        repositories = asyncio.run(connector.list_repositories())
        assert repositories[0].name == "Documents"
        assert repositories[0].repository_kind == "LIBRARY"

        page = asyncio.run(connector.list_files("d1"))
        assert len(page.items) == 1
        assert page.items[0].external_file_id == "d1:i1"
        assert page.has_more is True
        assert page.next_cursor.startswith("https://graph.microsoft.com/")

        permissions = asyncio.run(connector.get_permissions("d1:i1"))
        roles = {(item.principal_type, item.role) for item in permissions}
        assert (PrincipalType.USER, ExternalRole.READER) in roles
        assert (PrincipalType.GROUP, ExternalRole.OWNER) in roles
        assert (PrincipalType.ANYONE, ExternalRole.READER) in roles

        versions = asyncio.run(connector.get_versions("d1:i1"))
        assert versions[-1].is_current is True

        downloaded = asyncio.run(connector.download("d1:i1", max_bytes=1024))
        try:
            assert downloaded.size == len(PDF_BYTES)
        finally:
            downloaded.cleanup()
    finally:
        asyncio.run(connector.disconnect())


def test_sharepoint_delta_reports_deletions_and_rejects_unsafe_links():
    connector = SharePointConnector(transport=httpx.MockTransport(_graph_handler))
    asyncio.run(connector.authenticate({"access_token": "token"}))
    try:
        page = asyncio.run(connector.detect_changes("d1"))
        assert {change.kind for change in page.changes} == {
            ChangeKind.UPDATED,
            ChangeKind.DELETED,
        }
        assert page.next_cursor.startswith("https://graph.microsoft.com/")

        with pytest.raises(Exception):
            split_item_id("no-separator")

        unsafe = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "value": [],
                    "@odata.nextLink": "https://attacker.example/steal",
                },
            )
        )
        attacker = SharePointConnector(transport=unsafe)
        asyncio.run(attacker.authenticate({"access_token": "token"}))
        try:
            with pytest.raises(Exception):
                asyncio.run(attacker.list_files("d1"))
        finally:
            asyncio.run(attacker.disconnect())
    finally:
        asyncio.run(connector.disconnect())


def test_google_drive_requires_a_token_and_validates_connection():
    connector = GoogleDriveConnector(transport=httpx.MockTransport(_drive_handler))
    with pytest.raises(Exception):
        asyncio.run(connector.authenticate({}))
    asyncio.run(connector.authenticate({"access_token": "token"}))
    try:
        state = asyncio.run(connector.validate_connection())
        assert state.ok is True
    finally:
        asyncio.run(connector.disconnect())
