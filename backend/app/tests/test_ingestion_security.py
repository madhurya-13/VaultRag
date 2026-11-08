"""Security tests for ingestion APIs, tenant isolation and debug endpoints."""

from uuid import UUID

from sqlalchemy import and_, select

from app.models import Document, DocumentPermission, Role
from app.services.chunk_metadata import load_permission_snapshot
from app.tests.helpers import (
    FakeEmbeddingService,
    admin_bundle_with_attributes,
    create_admin_bundle,
    document_chunks,
    document_status,
    list_documents,
    make_pdf_bytes,
    process_document as process_document_api,
    revoke_document_permission,
    tenant_user_with_attributes,
    upload_pdf,
    use_tenant,
)


def _patch_fake_embedder(monkeypatch):
    fake = FakeEmbeddingService()
    monkeypatch.setattr(
        "app.services.ingestion_service.get_embedding_service",
        lambda: fake,
    )
    return fake


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


def _role_permission(db, tenant_id, document_id, role_name):
    use_tenant(db, tenant_id)
    return db.scalar(
        select(DocumentPermission)
        .join(
            Role,
            and_(
                Role.tenant_id == DocumentPermission.tenant_id,
                Role.role_id == DocumentPermission.role_id,
            ),
        )
        .where(
            DocumentPermission.tenant_id == UUID(str(tenant_id)),
            DocumentPermission.document_id == UUID(str(document_id)),
            Role.role_name == role_name,
            DocumentPermission.permission_type == "READ",
            DocumentPermission.is_active.is_(True),
        )
    )


def test_non_admin_cannot_upload_process_or_view_chunks(client, db, monkeypatch):
    organization, _, admin_token = admin_bundle_with_attributes(
        client,
        db,
        organization_name="Ingestion RBAC Tenant",
        email="ingestion-rbac-admin@example.com",
        username="admin",
    )
    upload = upload_pdf(client, admin_token, make_pdf_bytes())
    assert upload.status_code == 201, upload.text
    document_id = upload.json()["document_id"]
    _patch_fake_embedder(monkeypatch)
    processed = process_document_api(client, admin_token, document_id)
    assert processed.status_code == 200, processed.text

    _, employee_token = tenant_user_with_attributes(
        client,
        admin_token,
        organization["tenant_id"],
        email="ingestion-employee@example.com",
        username="ingestion_employee",
        role_names=["EMPLOYEE"],
    )

    upload_denied = upload_pdf(client, employee_token, make_pdf_bytes())
    assert upload_denied.status_code == 403

    process_denied = process_document_api(client, employee_token, document_id)
    assert process_denied.status_code == 403

    chunks_denied = document_chunks(client, employee_token, document_id)
    assert chunks_denied.status_code == 403

    status_denied = document_status(client, employee_token, document_id)
    assert status_denied.status_code in {403, 404}


def test_admin_without_abac_attributes_cannot_inspect_chunks(client, db, monkeypatch):
    _, _, token = create_admin_bundle(
        client,
        db,
        organization_name="Ingestion No Attributes Tenant",
        email="ingestion-no-attrs-admin@example.com",
        username="admin",
    )
    upload = upload_pdf(client, token, make_pdf_bytes())
    assert upload.status_code == 201, upload.text
    document_id = upload.json()["document_id"]
    _patch_fake_embedder(monkeypatch)
    assert process_document_api(client, token, document_id).status_code == 200

    # VIEW_CHUNKS is an admin capability, but chunk text is still document
    # content and must pass normal READ authorization including ABAC.
    response = document_chunks(client, token, document_id)
    assert response.status_code == 403


def test_employee_with_read_permission_can_view_status(client, db, monkeypatch):
    organization, _, admin_token = admin_bundle_with_attributes(
        client,
        db,
        organization_name="Ingestion Read Tenant",
        email="ingestion-read-admin@example.com",
        username="admin",
    )
    upload = upload_pdf(client, admin_token, make_pdf_bytes())
    assert upload.status_code == 201, upload.text
    document_id = upload.json()["document_id"]
    _patch_fake_embedder(monkeypatch)
    assert process_document_api(client, admin_token, document_id).status_code == 200

    _, engineering_token = tenant_user_with_attributes(
        client,
        admin_token,
        organization["tenant_id"],
        email="ingestion-engineer@example.com",
        username="ingestion_engineer",
        role_names=["ENGINEERING"],
    )

    response = document_status(client, engineering_token, document_id)
    assert response.status_code == 200, response.text
    assert response.json()["processing_status"] == "COMPLETED"


def test_cross_tenant_ingestion_access_is_not_found(client, db, monkeypatch):
    first_org, _, first_token = admin_bundle_with_attributes(
        client,
        db,
        organization_name="Ingestion Tenant A",
        email="ingestion-a@example.com",
        username="admin",
    )
    _, _, second_token = admin_bundle_with_attributes(
        client,
        db,
        organization_name="Ingestion Tenant B",
        email="ingestion-b@example.com",
        username="admin",
    )
    upload = upload_pdf(client, first_token, make_pdf_bytes())
    assert upload.status_code == 201, upload.text
    document_id = upload.json()["document_id"]
    _patch_fake_embedder(monkeypatch)
    assert process_document_api(client, first_token, document_id).status_code == 200

    assert process_document_api(client, second_token, document_id).status_code == 404
    assert document_status(client, second_token, document_id).status_code == 404
    assert document_chunks(client, second_token, document_id).status_code == 404

    visible = list_documents(client, second_token)
    assert all(item["document_id"] != document_id for item in visible)


def test_chunk_metadata_is_stale_after_permission_revocation(client, db, monkeypatch):
    organization, _, token = admin_bundle_with_attributes(
        client,
        db,
        organization_name="Ingestion Snapshot Tenant",
        email="ingestion-snapshot-admin@example.com",
        username="admin",
    )
    upload = upload_pdf(client, token, make_pdf_bytes())
    assert upload.status_code == 201, upload.text
    document_id = upload.json()["document_id"]
    _patch_fake_embedder(monkeypatch)
    assert process_document_api(client, token, document_id).status_code == 200

    chunks = document_chunks(client, token, document_id)
    assert chunks.status_code == 200, chunks.text
    assert "ENGINEERING" in chunks.json()[0]["metadata"]["allowed_roles"]

    engineering_permission = _role_permission(
        db,
        organization["tenant_id"],
        document_id,
        "ENGINEERING",
    )
    assert engineering_permission is not None
    revoke_document_permission(
        client,
        token,
        document_id,
        engineering_permission.permission_id,
    )

    # Stored chunk JSONB is a snapshot. It still shows the old grant, while a
    # live authorization read would use the now-revoked permission.
    chunks_after = document_chunks(client, token, document_id)
    assert chunks_after.status_code == 200
    assert "ENGINEERING" in chunks_after.json()[0]["metadata"]["allowed_roles"]

    current = load_permission_snapshot(
        db,
        tenant_id=UUID(organization["tenant_id"]),
        document_id=UUID(document_id),
    )
    assert "ENGINEERING" not in current.allowed_roles


def test_archived_document_cannot_be_processed(client, db, monkeypatch):
    organization, _, token = admin_bundle_with_attributes(
        client,
        db,
        organization_name="Ingestion Archive Tenant",
        email="ingestion-archive-admin@example.com",
        username="admin",
    )
    upload = upload_pdf(client, token, make_pdf_bytes())
    assert upload.status_code == 201, upload.text
    document_id = upload.json()["document_id"]
    _patch_fake_embedder(monkeypatch)

    use_tenant(db, organization["tenant_id"])
    document = db.scalar(
        select(Document).where(
            Document.tenant_id == UUID(organization["tenant_id"]),
            Document.document_id == UUID(document_id),
        )
    )
    assert document is not None
    document.processing_status = "ARCHIVED"
    db.commit()

    response = process_document_api(client, token, document_id)
    assert response.status_code == 409
