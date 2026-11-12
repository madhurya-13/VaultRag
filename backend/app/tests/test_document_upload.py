"""Secure PDF upload tests: validation, private storage and initial grants."""

import hashlib
from io import BytesIO
from uuid import UUID, uuid4

import pytest
from sqlalchemy import and_, select

from app.core.exceptions import PayloadTooLargeError
from app.core.security_constants import PERMISSION_MANAGE, PERMISSION_READ
from app.models import Document, DocumentPermission, Role
from app.services.storage import LocalPrivateStorage, get_private_storage
from app.tests.helpers import (
    create_admin_bundle,
    make_pdf_bytes,
    upload_pdf,
    use_tenant,
)


def _load_document(db, tenant_id, document_id) -> Document:
    use_tenant(db, tenant_id)
    document = db.scalar(
        select(Document).where(
            Document.tenant_id == UUID(str(tenant_id)),
            Document.document_id == UUID(str(document_id)),
        )
    )
    assert document is not None
    return document


def test_upload_valid_pdf_stores_private_file_and_server_derived_metadata(
    client, db
):
    organization, admin, token = create_admin_bundle(
        client,
        db,
        organization_name="Upload Tenant",
        email="upload-admin@example.com",
    )
    response = upload_pdf(client, token, make_pdf_bytes())

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["document_type"] == "PDF"
    assert body["department"] == "ENGINEERING"
    assert body["classification"] == "INTERNAL"
    assert body["processing_status"] == "UPLOADED"
    assert body["total_pages"] == 0
    assert body["total_chunks"] == 0
    assert body["embedding_model"] is None
    assert body["uploaded_by"] == str(admin.user_id)
    assert body["tenant_id"] == organization["tenant_id"]
    assert body["file_size"] > 0
    assert len(body["file_hash"]) == 64
    assert "storage_path" not in body

    document = _load_document(db, organization["tenant_id"], body["document_id"])
    assert document.processing_status == "UPLOADED"
    assert document.file_size == body["file_size"]
    assert document.file_hash == body["file_hash"]
    assert document.storage_path is not None

    storage = get_private_storage()
    stored_path = storage.path_for(document.storage_path)
    assert stored_path.is_file()
    assert stored_path.is_relative_to(storage.root)
    assert stored_path.name.endswith(".pdf")
    assert hashlib.sha256(stored_path.read_bytes()).hexdigest() == document.file_hash

    all_permission_rows = db.scalars(
        select(DocumentPermission).where(
            DocumentPermission.tenant_id == UUID(organization["tenant_id"]),
            DocumentPermission.document_id == UUID(body["document_id"]),
            DocumentPermission.is_active.is_(True),
        )
    ).all()
    assert len(all_permission_rows) == 3
    assert all(permission.user_id is None for permission in all_permission_rows)
    assert all(
        permission.granted_by == admin.user_id
        for permission in all_permission_rows
    )

    permission_rows = db.execute(
        select(DocumentPermission, Role.role_name)
        .join(
            Role,
            and_(
                Role.tenant_id == DocumentPermission.tenant_id,
                Role.role_id == DocumentPermission.role_id,
            ),
        )
        .where(
            DocumentPermission.tenant_id == UUID(organization["tenant_id"]),
            DocumentPermission.document_id == UUID(body["document_id"]),
            DocumentPermission.is_active.is_(True),
        )
    ).all()
    grants = {
        (role_name, permission.permission_type)
        for permission, role_name in permission_rows
    }
    assert grants == {
        ("ADMIN", PERMISSION_READ),
        ("ADMIN", PERMISSION_MANAGE),
        ("ENGINEERING", PERMISSION_READ),
    }


def test_upload_ignores_client_supplied_security_metadata(client, db):
    organization, admin, token = create_admin_bundle(
        client,
        db,
        organization_name="Mass Assignment Tenant",
        email="mass-admin@example.com",
    )
    other_tenant = str(uuid4())
    response = upload_pdf(
        client,
        token,
        make_pdf_bytes(),
        extra_form={
            "tenant_id": other_tenant,
            "uploaded_by": str(uuid4()),
            "processing_status": "COMPLETED",
            "total_pages": "999",
            "total_chunks": "999",
            "file_hash": "0" * 64,
            "storage_path": "public/evil.pdf",
            "is_active": "false",
            "permission_type": "MANAGE_PERMISSIONS",
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["tenant_id"] == organization["tenant_id"]
    assert body["uploaded_by"] == str(admin.user_id)
    assert body["processing_status"] == "UPLOADED"
    assert body["total_pages"] == 0
    assert body["total_chunks"] == 0
    assert body["file_hash"] != "0" * 64

    document = _load_document(db, organization["tenant_id"], body["document_id"])
    assert document.storage_path != "public/evil.pdf"
    assert document.is_active is True


def test_upload_rejects_non_pdf_extension(client, db):
    _, _, token = create_admin_bundle(
        client,
        db,
        organization_name="Extension Tenant",
        email="extension-admin@example.com",
    )

    response = upload_pdf(client, token, make_pdf_bytes(), filename="notes.txt")
    assert response.status_code == 415
    assert "PDF" in response.json()["detail"]


def test_upload_rejects_renamed_non_pdf_content(client, db):
    _, _, token = create_admin_bundle(
        client,
        db,
        organization_name="Renamed Tenant",
        email="renamed-admin@example.com",
    )

    response = upload_pdf(
        client,
        token,
        b"this is plain text pretending to be a PDF",
        filename="innocent.pdf",
    )
    assert response.status_code == 422


def test_upload_rejects_malformed_pdf(client, db):
    _, _, token = create_admin_bundle(
        client,
        db,
        organization_name="Malformed Tenant",
        email="malformed-admin@example.com",
    )

    response = upload_pdf(client, token, b"%PDF-1.4\nthis is not valid")
    assert response.status_code == 422


def test_upload_rejects_encrypted_pdf(client, db):
    _, _, token = create_admin_bundle(
        client,
        db,
        organization_name="Encrypted Tenant",
        email="encrypted-admin@example.com",
    )

    response = upload_pdf(
        client,
        token,
        make_pdf_bytes(encrypt_password="secret-password"),
    )
    assert response.status_code == 422
    assert "Encrypted" in response.json()["detail"]


def test_upload_rejects_image_only_pdf(client, db):
    _, _, token = create_admin_bundle(
        client,
        db,
        organization_name="Image Only Tenant",
        email="image-admin@example.com",
    )

    response = upload_pdf(client, token, make_pdf_bytes(image_only=True))
    assert response.status_code == 422


def test_upload_rejects_pdf_over_page_limit_without_registering_document(
    client, db, monkeypatch
):
    from app.services import document_upload
    from app.core.config import get_settings

    organization, _, token = create_admin_bundle(
        client,
        db,
        organization_name="Page limit tenant",
        email="page-limit-admin@example.com",
    )
    original_settings = get_settings()
    monkeypatch.setattr(
        document_upload,
        "get_settings",
        lambda: original_settings.model_copy(update={"max_pdf_pages": 1}),
    )

    response = upload_pdf(client, token, make_pdf_bytes(["Policy text " * 20] * 2))
    assert response.status_code == 422
    assert "page count" in response.json()["detail"].lower()

    use_tenant(db, organization["tenant_id"])
    stored = db.scalars(
        select(Document).where(Document.tenant_id == UUID(organization["tenant_id"]))
    ).all()
    assert stored == []


def test_upload_rejects_empty_file(client, db):
    _, _, token = create_admin_bundle(
        client,
        db,
        organization_name="Empty Tenant",
        email="empty-admin@example.com",
    )

    response = upload_pdf(client, token, b"")
    assert response.status_code == 415


def test_upload_duplicate_hash_is_tenant_scoped(client, db):
    first_org, _, first_token = create_admin_bundle(
        client,
        db,
        organization_name="Duplicate Tenant A",
        email="duplicate-a@example.com",
    )
    second_org, _, second_token = create_admin_bundle(
        client,
        db,
        organization_name="Duplicate Tenant B",
        email="duplicate-b@example.com",
    )
    content = make_pdf_bytes()

    first = upload_pdf(client, first_token, content)
    assert first.status_code == 201, first.text
    duplicate = upload_pdf(client, first_token, content)
    assert duplicate.status_code == 409

    other_tenant = upload_pdf(client, second_token, content)
    assert other_tenant.status_code == 201, other_tenant.text
    assert other_tenant.json()["tenant_id"] == second_org["tenant_id"]
    assert first.json()["tenant_id"] == first_org["tenant_id"]


def test_upload_rejects_invalid_department_and_classification(client, db):
    _, _, token = create_admin_bundle(
        client,
        db,
        organization_name="Attribute Tenant",
        email="attribute-admin@example.com",
    )

    bad_department = upload_pdf(client, token, department="UNKNOWN")
    assert bad_department.status_code == 400

    bad_classification = upload_pdf(client, token, classification="TOP_SECRET")
    assert bad_classification.status_code == 400


def test_local_storage_enforces_size_limit_and_path_traversal(tmp_path):
    storage = LocalPrivateStorage(tmp_path)

    with pytest.raises(PayloadTooLargeError):
        storage.save_stream(
            BytesIO(b"12345"),
            original_filename="too-large.pdf",
            max_bytes=4,
        )
    assert list(tmp_path.rglob("*.pdf")) == []

    with pytest.raises(ValueError):
        storage.path_for("../escape.pdf")
    with pytest.raises(ValueError):
        storage.path_for("C:\\escape.pdf")

    key = storage.build_key(uuid4(), uuid4())
    assert key.endswith(".pdf")
    assert ".." not in key
    assert storage.path_for(key).is_relative_to(storage.root)


def test_upload_rejects_wrong_declared_content_type(client, db):
    _, _, token = create_admin_bundle(
        client,
        db,
        organization_name="Content Type Tenant",
        email="content-type-admin@example.com",
    )

    response = upload_pdf(
        client,
        token,
        make_pdf_bytes(),
        content_type="text/plain",
    )
    assert response.status_code == 415
