"""End-to-end integration API: connect, sync, version, restore and isolate.

The local reference connector is used so the whole path runs without network or
provider credentials. Ingestion uses the deterministic fake embedding service.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import select

from app.core.security_constants import PERMISSION_ORIGIN_EXTERNAL
from app.integrations.base import (
    ExternalRole,
    NormalizedPermission,
    PrincipalType,
)
from app.integrations.models import DocumentVersion, ExternalDocument, Integration
from app.models import Document, DocumentPermission
from app.services.integrations.permissions import mirror_document_permissions
from app.tests.helpers import (
    admin_bundle_with_attributes,
    auth_headers,
    create_admin_bundle,
    make_pdf_bytes,
    use_tenant,
)


@pytest.fixture()
def fake_ingestion(monkeypatch):
    from app.tests.helpers import FakeEmbeddingService

    embedder = FakeEmbeddingService()
    monkeypatch.setattr(
        "app.services.ingestion_service.get_embedding_service",
        lambda: embedder,
    )
    return embedder


def _create_integration(
    client,
    token: str,
    *,
    root: Path,
    name: str = "Local policies",
    permission_sync_mode: str = "VAULTRAG_MANAGED",
):
    response = client.post(
        "/integrations",
        headers=auth_headers(token),
        json={
            "provider": "local_storage",
            "integration_name": name,
            "permission_sync_mode": permission_sync_mode,
            "configuration": {"root": str(root), "department": "ENGINEERING"},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _source_tree(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    repository = root / "policies"
    repository.mkdir(parents=True)
    (repository / "handbook.pdf").write_bytes(
        make_pdf_bytes(["Annual leave is eighteen days for every employee."])
    )
    return root


def test_full_sync_ingests_creates_versions_and_is_idempotent(
    client, db, tmp_path, fake_ingestion
):
    organization, admin, token = admin_bundle_with_attributes(
        client,
        db,
        organization_name="Alpha Corp",
        email="alpha@example.com",
        username="admin",
    )
    root = _source_tree(tmp_path)
    integration = _create_integration(client, token, root=root)

    connected = client.post(
        f"/integrations/{integration['integration_id']}/connect",
        headers=auth_headers(token),
    )
    assert connected.status_code == 200, connected.text
    assert connected.json()["connected"] is True

    repositories = client.get(
        f"/integrations/{integration['integration_id']}/repositories",
        headers=auth_headers(token),
    )
    assert repositories.status_code == 200, repositories.text
    rows = repositories.json()["repositories"]
    assert [row["external_repository_id"] for row in rows] == ["policies"]

    selected = client.put(
        f"/integrations/{integration['integration_id']}/repositories/"
        f"{rows[0]['repository_id']}",
        headers=auth_headers(token),
        json={"selected": True, "sync_mode": "FULL"},
    )
    assert selected.status_code == 200, selected.text

    synced = client.post(
        f"/integrations/{integration['integration_id']}/sync",
        headers=auth_headers(token),
        json={"sync_type": "FULL"},
    )
    assert synced.status_code == 202, synced.text
    job = synced.json()
    assert job["sync_status"] == "COMPLETED", job
    assert job["files_processed"] == 1
    assert job["versions_created"] == 1

    documents = client.get(
        f"/integrations/{integration['integration_id']}/documents",
        headers=auth_headers(token),
    )
    assert documents.status_code == 200, documents.text
    external = documents.json()["documents"]
    assert len(external) == 1
    assert external[0]["sync_status"] == "SYNCED"
    document_id = external[0]["internal_document_id"]

    versions = client.get(
        f"/documents/{document_id}/versions", headers=auth_headers(token)
    )
    assert versions.status_code == 200, versions.text
    rows = versions.json()["versions"]
    assert len(rows) == 1
    assert rows[0]["is_current"] is True
    assert rows[0]["ingestion_status"] == "COMPLETED"
    first_version_id = rows[0]["document_version_id"]

    # A second run over identical bytes creates no new version.
    again = client.post(
        f"/integrations/{integration['integration_id']}/sync",
        headers=auth_headers(token),
        json={"sync_type": "FULL"},
    )
    assert again.status_code == 202, again.text
    assert again.json()["files_processed"] == 1
    assert again.json()["versions_created"] == 0

    # Changing the source bytes replaces the live revision and keeps history.
    (root / "policies" / "handbook.pdf").write_bytes(
        make_pdf_bytes(["Annual leave is twenty days after the revision."])
    )
    changed = client.post(
        f"/integrations/{integration['integration_id']}/sync",
        headers=auth_headers(token),
        json={"sync_type": "FULL"},
    )
    assert changed.status_code == 202, changed.text
    assert changed.json()["versions_created"] == 1

    versions = client.get(
        f"/documents/{document_id}/versions", headers=auth_headers(token)
    ).json()["versions"]
    assert len(versions) == 2
    assert sum(1 for row in versions if row["is_current"]) == 1

    restored = client.post(
        f"/documents/{document_id}/versions/{first_version_id}/restore",
        headers=auth_headers(token),
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["is_current"] is True
    current = client.get(
        f"/documents/{document_id}/versions", headers=auth_headers(token)
    ).json()["versions"]
    assert sum(1 for row in current if row["is_current"]) == 1
    assert next(row for row in current if row["is_current"])[
        "document_version_id"
    ] == first_version_id


def test_sync_requires_configuration_and_admin_capability(
    client, db, tmp_path, fake_ingestion
):
    organization, admin, token = admin_bundle_with_attributes(
        client,
        db,
        organization_name="Alpha Corp",
        email="alpha@example.com",
        username="admin",
    )
    integration = _create_integration(client, token, root=_source_tree(tmp_path))

    # A tenant member without MANAGE_INTEGRATIONS cannot inspect connections.
    other, other_token = None, None
    from app.tests.helpers import tenant_user_with_attributes

    other, other_token = tenant_user_with_attributes(
        client,
        token,
        organization["tenant_id"],
        email="employee@example.com",
        username="employee",
        role_names=["EMPLOYEE"],
    )
    forbidden = client.get(
        f"/integrations/{integration['integration_id']}/repositories",
        headers=auth_headers(other_token),
    )
    assert forbidden.status_code == 403

    # Syncing before choosing a repository fails safely rather than pretending.
    queued = client.post(
        f"/integrations/{integration['integration_id']}/sync",
        headers=auth_headers(token),
        json={"sync_type": "FULL"},
    )
    assert queued.status_code == 202, queued.text
    assert queued.json()["sync_status"] == "FAILED"
    assert queued.json()["error_code"] == "INTEGRATION_NO_REPOSITORIES"

    # Credentials never appear in an API response.
    detail = client.get(
        f"/integrations/{integration['integration_id']}",
        headers=auth_headers(token),
    )
    assert detail.status_code == 200
    assert "root" in detail.json()["configuration"]
    assert "credentials" not in detail.json()
    assert detail.json()["credential"] is None


def test_mirrored_permission_mapping_is_deny_by_default(client, db):
    organization, admin, _ = create_admin_bundle(
        client, db, organization_name="Alpha Corp", email="alpha@example.com"
    )
    tenant_id = UUID(organization["tenant_id"])
    use_tenant(db, tenant_id)
    integration = Integration(
        tenant_id=tenant_id,
        provider="local_storage",
        integration_name="Mirror",
        permission_sync_mode="EXTERNAL_MIRROR",
        created_by=admin.user_id,
    )
    db.add(integration)
    db.flush()
    document = Document(
        tenant_id=tenant_id,
        document_name="Shared",
        document_type="INTEGRATION",
        department="ENGINEERING",
        classification="INTERNAL",
        required_clearance_level=1,
        uploaded_by=admin.user_id,
    )
    db.add(document)
    db.flush()
    external = ExternalDocument(
        tenant_id=tenant_id,
        integration_id=integration.integration_id,
        internal_document_id=document.document_id,
        provider="local_storage",
        external_file_id="policies/shared.pdf",
        external_repository_id="policies",
    )
    db.add(external)
    from app.integrations.models import IntegrationIdentityMapping

    db.add(
        IntegrationIdentityMapping(
            tenant_id=tenant_id,
            integration_id=integration.integration_id,
            external_principal_id="alice@example.com",
            external_principal_type="USER",
            user_id=admin.user_id,
            created_by=admin.user_id,
        )
    )
    db.flush()

    result = mirror_document_permissions(
        db,
        integration=integration,
        document=document,
        external_document=external,
        permissions=(
            NormalizedPermission(
                principal_id="alice@example.com",
                principal_type=PrincipalType.USER,
                role=ExternalRole.READER,
            ),
            NormalizedPermission(
                principal_id="unmapped@example.com",
                principal_type=PrincipalType.USER,
                role=ExternalRole.READER,
            ),
            NormalizedPermission(
                principal_id="alice@example.com",
                principal_type=PrincipalType.USER,
                role=ExternalRole.UNKNOWN,
            ),
        ),
        actor_user_id=admin.user_id,
    )
    db.commit()
    use_tenant(db, tenant_id)

    assert result.granted == 1
    # One unmapped principal and one UNKNOWN role; neither becomes READ.
    assert result.unresolved == 2
    grants = db.scalars(
        select(DocumentPermission).where(
            DocumentPermission.tenant_id == tenant_id,
            DocumentPermission.document_id == document.document_id,
            DocumentPermission.is_active.is_(True),
        )
    ).all()
    assert len(grants) == 1
    assert grants[0].permission_origin == PERMISSION_ORIGIN_EXTERNAL
    assert grants[0].managed_by_integration_id == integration.integration_id
    assert grants[0].user_id == admin.user_id
    assert external.permission_state == "RESOLVED"


def test_integration_tables_are_tenant_isolated(client, db, tmp_path):
    alpha, alpha_admin, alpha_token = admin_bundle_with_attributes(
        client,
        db,
        organization_name="Alpha Corp",
        email="alpha@example.com",
        username="admin",
    )
    beta, _, beta_token = create_admin_bundle(
        client, db, organization_name="Beta Corp", email="beta@example.com"
    )
    alpha_integration = _create_integration(
        client, alpha_token, root=_source_tree(tmp_path)
    )

    # The other tenant cannot read the integration by guessing its id.
    cross = client.get(
        f"/integrations/{alpha_integration['integration_id']}",
        headers=auth_headers(beta_token),
    )
    assert cross.status_code == 404

    listed = client.get("/integrations", headers=auth_headers(beta_token))
    assert listed.status_code == 200
    assert all(
        row["integration_id"] != alpha_integration["integration_id"]
        for row in listed.json()["integrations"]
    )

    use_tenant(db, UUID(alpha["tenant_id"]))
    assert db.scalar(select(DocumentVersion)) is None
    assert len(db.scalars(select(ExternalDocument)).all()) == 0
