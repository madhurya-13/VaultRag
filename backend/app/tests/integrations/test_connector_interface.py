"""The connector contract, exercised without network access or credentials."""

from __future__ import annotations

import asyncio
import hashlib
import importlib
from pathlib import Path

import pytest

from app.integrations.base import Capability, ChangeKind, NormalizedDocument
from app.integrations.exceptions import (
    DownloadTooLargeError,
    ExternalPathError,
    IntegrationValidationError,
    UnsupportedCapabilityError,
)
from app.integrations.providers.local_storage import LocalStorageConnector

PDF_BYTES = b"%PDF-1.4 fake but stable bytes for hashing"


def _make_connector(root: Path, *, max_download_bytes: int = 1024 * 1024) -> LocalStorageConnector:
    connector = LocalStorageConnector(root=root, max_download_bytes=max_download_bytes)
    asyncio.run(connector.authenticate({}))
    return connector


def test_normalized_document_rejects_invalid_values():
    with pytest.raises(ValueError):
        NormalizedDocument(
            external_file_id="",
            external_repository_id="repo",
            file_name="a.pdf",
        )
    with pytest.raises(ValueError):
        NormalizedDocument(
            external_file_id="repo/a.pdf",
            external_repository_id="repo",
            file_name="a.pdf",
            checksum="NOT-A-CHECKSUM",
        )
    with pytest.raises(ValueError):
        NormalizedDocument(
            external_file_id="repo/a.pdf",
            external_repository_id="repo",
            file_name="a.pdf",
            file_size=-1,
        )


def test_ingestible_type_prefers_native_export_and_rejects_docx():
    exported = NormalizedDocument(
        external_file_id="repo/doc",
        external_repository_id="repo",
        file_name="doc",
        mime_type="application/vnd.google-apps.document",
        native_export_mime_type="application/pdf",
    )
    assert exported.ingestible_mime_type == "application/pdf"

    word = NormalizedDocument(
        external_file_id="repo/a.docx",
        external_repository_id="repo",
        file_name="a.docx",
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    assert word.ingestible_mime_type is None


def test_local_connector_lists_repositories_files_and_metadata(tmp_path: Path):
    repository = tmp_path / "policies"
    repository.mkdir()
    (repository / "handbook.pdf").write_bytes(PDF_BYTES)
    (repository / "notes.docx").write_bytes(b"docx bytes")

    connector = _make_connector(tmp_path)

    repositories = asyncio.run(connector.list_repositories())
    assert [item.external_repository_id for item in repositories] == ["policies"]
    assert repositories[0].repository_kind == "LOCAL"

    page = asyncio.run(connector.list_files("policies", page_size=1))
    assert len(page.items) == 1
    assert page.has_more is True
    assert page.next_cursor == "policies/handbook.pdf"

    rest = asyncio.run(connector.list_files("policies", cursor=page.next_cursor))
    assert [item.file_name for item in rest.items] == ["notes.docx"]
    assert rest.has_more is False
    assert rest.next_cursor is None

    metadata = asyncio.run(connector.get_metadata("policies/handbook.pdf"))
    assert metadata.file_size == len(PDF_BYTES)
    assert metadata.checksum == hashlib.sha256(PDF_BYTES).hexdigest()
    assert metadata.external_version_id == metadata.checksum
    assert metadata.source_url is None
    assert metadata.ingestible_mime_type == "application/pdf"


def test_local_connector_download_returns_verified_bytes(tmp_path: Path):
    repository = tmp_path / "policies"
    repository.mkdir()
    (repository / "handbook.pdf").write_bytes(PDF_BYTES)
    connector = _make_connector(tmp_path)

    downloaded = asyncio.run(
        connector.download("policies/handbook.pdf", max_bytes=1024)
    )
    try:
        assert downloaded.size == len(PDF_BYTES)
        assert downloaded.sha256 == hashlib.sha256(PDF_BYTES).hexdigest()
        assert downloaded.temp_path.read_bytes() == PDF_BYTES
    finally:
        downloaded.cleanup()
    assert not downloaded.temp_path.exists()


def test_local_connector_enforces_the_download_size_cap(tmp_path: Path):
    repository = tmp_path / "policies"
    repository.mkdir()
    (repository / "large.pdf").write_bytes(b"x" * 2048)
    connector = _make_connector(tmp_path)

    with pytest.raises(DownloadTooLargeError):
        asyncio.run(connector.download("policies/large.pdf", max_bytes=1024))


def test_local_connector_rejects_path_traversal(tmp_path: Path):
    (tmp_path / "outside.pdf").write_bytes(PDF_BYTES)
    repository = tmp_path / "policies"
    repository.mkdir()
    connector = _make_connector(tmp_path)

    for identifier in (
        "../outside.pdf",
        "/etc/passwd",
        "policies/../../outside.pdf",
        "policies\\..\\outside.pdf",
    ):
        with pytest.raises(ExternalPathError):
            asyncio.run(connector.get_metadata(identifier))
    with pytest.raises(ExternalPathError):
        asyncio.run(connector.list_files("../policies"))


def test_local_connector_detect_changes_reports_created_updated_deleted(tmp_path: Path):
    repository = tmp_path / "policies"
    repository.mkdir()
    (repository / "keep.pdf").write_bytes(PDF_BYTES)
    (repository / "remove.pdf").write_bytes(b"old content")
    connector = _make_connector(tmp_path)

    first = asyncio.run(connector.detect_changes("policies"))
    assert {record.kind for record in first.changes} == {ChangeKind.CREATED}
    assert len(first.changes) == 2
    assert first.next_cursor

    (repository / "keep.pdf").write_bytes(PDF_BYTES + b" revised")
    (repository / "remove.pdf").unlink()
    (repository / "new.pdf").write_bytes(b"brand new")

    second = asyncio.run(
        connector.detect_changes("policies", cursor=first.next_cursor)
    )
    by_name = {record.external_file_id: record.kind for record in second.changes}
    assert by_name == {
        "policies/keep.pdf": ChangeKind.UPDATED,
        "policies/new.pdf": ChangeKind.CREATED,
        "policies/remove.pdf": ChangeKind.DELETED,
    }
    deleted = next(
        record for record in second.changes if record.kind is ChangeKind.DELETED
    )
    assert deleted.deleted is True

    unchanged = asyncio.run(
        connector.detect_changes("policies", cursor=second.next_cursor)
    )
    assert unchanged.changes == ()


def test_undeclared_capabilities_fail_loudly(tmp_path: Path):
    repository = tmp_path / "policies"
    repository.mkdir()
    (repository / "handbook.pdf").write_bytes(PDF_BYTES)
    connector = _make_connector(tmp_path)

    for call in (
        lambda: connector.get_permissions("policies/handbook.pdf"),
        lambda: connector.get_versions("policies/handbook.pdf"),
    ):
        with pytest.raises(UnsupportedCapabilityError):
            asyncio.run(call())

    with pytest.raises(UnsupportedCapabilityError):
        connector.require(Capability.WEBHOOKS)


def test_connector_refuses_to_point_at_private_document_storage(tmp_path: Path):
    connector = LocalStorageConnector(root=tmp_path)
    private_root = tmp_path / "private_documents"
    private_root.mkdir()

    from types import SimpleNamespace

    module = importlib.import_module(
        "app.integrations.providers.local_storage.connector"
    )
    original = module.get_settings
    module.get_settings = lambda: SimpleNamespace(document_storage_dir=str(private_root))
    try:
        with pytest.raises(IntegrationValidationError):
            asyncio.run(connector.authenticate({}))
    finally:
        module.get_settings = original


def test_invalid_cursor_is_rejected(tmp_path: Path):
    repository = tmp_path / "policies"
    repository.mkdir()
    (repository / "handbook.pdf").write_bytes(PDF_BYTES)
    connector = _make_connector(tmp_path)

    with pytest.raises(IntegrationValidationError):
        asyncio.run(connector.detect_changes("policies", cursor="{not json"))
