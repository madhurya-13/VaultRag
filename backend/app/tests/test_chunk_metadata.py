"""Permission snapshot and trusted chunk metadata tests."""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.models import Document
from app.services.chunk_metadata import (
    build_chunk_metadata,
    load_permission_snapshot,
    validate_metadata_against_document,
)
from app.tests.helpers import (
    admin_bundle_with_attributes,
    create_admin_bundle,
    grant_document_permission,
    make_pdf_bytes,
    revoke_document_permission,
    upload_pdf,
    use_tenant,
)


def _uploaded_document(client, db, *, organization_name: str, email: str):
    organization, admin, token = admin_bundle_with_attributes(
        client,
        db,
        organization_name=organization_name,
        email=email,
        username="admin",
    )
    response = upload_pdf(client, token, make_pdf_bytes())
    assert response.status_code == 201, response.text
    use_tenant(db, organization["tenant_id"])
    document = db.scalar(
        select(Document).where(
            Document.tenant_id == UUID(organization["tenant_id"]),
            Document.document_id == UUID(response.json()["document_id"]),
        )
    )
    assert document is not None
    return organization, admin, token, document


def test_snapshot_is_derived_from_active_read_grants(client, db):
    organization, _, _, document = _uploaded_document(
        client,
        db,
        organization_name="Metadata Tenant",
        email="metadata-admin@example.com",
    )
    snapshot = load_permission_snapshot(
        db,
        tenant_id=UUID(organization["tenant_id"]),
        document_id=document.document_id,
    )

    assert snapshot.allowed_roles == ("ADMIN", "ENGINEERING")
    assert len(snapshot.allowed_role_ids) == 2
    assert snapshot.allowed_user_ids == ()
    assert snapshot.snapshot_at.tzinfo is not None


def test_build_chunk_metadata_contains_trusted_document_fields(client, db):
    organization, _, _, document = _uploaded_document(
        client,
        db,
        organization_name="Metadata Fields Tenant",
        email="metadata-fields-admin@example.com",
    )
    snapshot = load_permission_snapshot(
        db,
        tenant_id=UUID(organization["tenant_id"]),
        document_id=document.document_id,
    )
    metadata = build_chunk_metadata(
        document,
        snapshot,
        embedding_model="fake-model",
        embedding_model_version="v1",
    )

    assert metadata["tenant_id"] == str(document.tenant_id)
    assert metadata["document_id"] == str(document.document_id)
    assert metadata["department"] == document.department
    assert metadata["classification"] == document.classification
    assert metadata["required_clearance_level"] == document.required_clearance_level
    assert metadata["allowed_roles"] == ["ADMIN", "ENGINEERING"]
    assert metadata["embedding_model"] == "fake-model"
    assert metadata["embedding_model_version"] == "v1"
    assert "storage_path" not in metadata
    assert "file_hash" not in metadata

    validate_metadata_against_document(metadata, document, snapshot)


def test_validate_metadata_rejects_authoritative_mismatch(client, db):
    organization, _, _, document = _uploaded_document(
        client,
        db,
        organization_name="Metadata Mismatch Tenant",
        email="metadata-mismatch-admin@example.com",
    )
    snapshot = load_permission_snapshot(
        db,
        tenant_id=UUID(organization["tenant_id"]),
        document_id=document.document_id,
    )
    metadata = build_chunk_metadata(
        document,
        snapshot,
        embedding_model="fake-model",
        embedding_model_version="v1",
    )

    wrong_tenant = dict(metadata)
    wrong_tenant["tenant_id"] = str(uuid4())
    with pytest.raises(ValueError):
        validate_metadata_against_document(wrong_tenant, document, snapshot)

    wrong_department = dict(metadata)
    wrong_department["department"] = "FINANCE"
    with pytest.raises(ValueError):
        validate_metadata_against_document(wrong_department, document, snapshot)


def test_permission_snapshot_is_stale_after_grants_and_revocations(client, db):
    organization, _, token, document = _uploaded_document(
        client,
        db,
        organization_name="Metadata Stale Tenant",
        email="metadata-stale-admin@example.com",
    )
    old_snapshot = load_permission_snapshot(
        db,
        tenant_id=UUID(organization["tenant_id"]),
        document_id=document.document_id,
    )
    old_metadata = build_chunk_metadata(
        document,
        old_snapshot,
        embedding_model="fake-model",
        embedding_model_version="v1",
    )
    assert "HR" not in old_metadata["allowed_roles"]

    granted = grant_document_permission(
        client,
        token,
        document.document_id,
        permission_type="READ",
        role_name="HR",
        cross_department_allowed=True,
    )
    current_snapshot = load_permission_snapshot(
        db,
        tenant_id=UUID(organization["tenant_id"]),
        document_id=document.document_id,
    )
    assert "HR" in current_snapshot.allowed_roles
    # The stored JSONB snapshot is explicitly a snapshot, not live state.
    assert "HR" not in old_metadata["allowed_roles"]

    revoke_document_permission(
        client,
        token,
        document.document_id,
        granted["permission_id"],
    )
    after_revoke = load_permission_snapshot(
        db,
        tenant_id=UUID(organization["tenant_id"]),
        document_id=document.document_id,
    )
    assert "HR" not in after_revoke.allowed_roles
