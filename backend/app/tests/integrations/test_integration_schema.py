"""Database-level guarantees for the Milestone 9 schema.

These tests use the real ``vaultrag_app`` role, so they prove tenant isolation
and least privilege at the same boundary production uses.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.core.security_constants import (
    PERMISSION_ORIGIN_MANUAL,
    PROVIDER_LOCAL_STORAGE,
)
from app.integrations.models import (
    ExternalDocument,
    Integration,
    IntegrationCredential,
    IntegrationSyncJob,
)
from app.models import DocumentPermission, Role
from app.tests.helpers import (
    create_admin_bundle,
    create_document,
    use_tenant,
)

NEW_TENANT_TABLES = (
    "integrations",
    "integration_credentials",
    "integration_repositories",
    "external_documents",
    "document_versions",
    "integration_sync_jobs",
    "integration_sync_events",
    "integration_identity_mappings",
    "integration_oauth_states",
)


def _make_integration(db, organization, admin, name: str = "Local policies") -> UUID:
    """Insert an integration and return its id.

    The id is captured before commit because committing expires ORM instances
    and the follow-up refresh would run without the transaction-local RLS
    tenant, making a committed row look deleted.
    """
    tenant_id = UUID(organization["tenant_id"])
    use_tenant(db, tenant_id)
    integration = Integration(
        tenant_id=tenant_id,
        provider=PROVIDER_LOCAL_STORAGE,
        integration_name=name,
        created_by=admin.user_id,
    )
    db.add(integration)
    db.flush()
    integration_id = integration.integration_id
    db.commit()
    return integration_id


def test_new_tables_have_forced_rls_and_a_tenant_policy(app_engine):
    with app_engine.connect() as connection:
        tables = {
            row.relname: row
            for row in connection.execute(
                text(
                    "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity "
                    "FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = 'public' AND c.relkind = 'r'"
                )
            ).all()
        }
        policies = {
            row.tablename: row
            for row in connection.execute(
                text(
                    "SELECT tablename, policyname, qual, with_check "
                    "FROM pg_policies WHERE schemaname = 'public'"
                )
            ).all()
        }

    for table in NEW_TENANT_TABLES:
        assert table in tables, table
        assert tables[table].relrowsecurity is True, table
        assert tables[table].relforcerowsecurity is True, table
        policy = policies[table]
        assert policy.policyname == "tenant_isolation"
        assert "current_tenant_id" in (policy.qual or "")
        assert "current_tenant_id" in (policy.with_check or "")


def test_runtime_role_grants_stay_least_privilege(app_engine):
    checks = {
        "documents_delete": "has_table_privilege('vaultrag_app', 'documents', 'DELETE')",
        "chunks_delete": "has_table_privilege('vaultrag_app', 'document_chunks', 'DELETE')",
        "integrations_delete": "has_table_privilege('vaultrag_app', 'integrations', 'DELETE')",
        "credentials_delete": "has_table_privilege('vaultrag_app', 'integration_credentials', 'DELETE')",
        "versions_delete": "has_table_privilege('vaultrag_app', 'document_versions', 'DELETE')",
        "integrations_insert": "has_table_privilege('vaultrag_app', 'integrations', 'INSERT')",
        "credentials_update": "has_table_privilege('vaultrag_app', 'integration_credentials', 'UPDATE')",
        "permissions_update": "has_table_privilege('vaultrag_app', 'document_permissions', 'UPDATE')",
    }
    with app_engine.connect() as connection:
        granted = {
            name: connection.scalar(text(f"SELECT {expression}"))
            for name, expression in checks.items()
        }

    assert granted["documents_delete"] is False
    assert granted["integrations_delete"] is False
    assert granted["credentials_delete"] is False
    assert granted["versions_delete"] is False
    assert granted["chunks_delete"] is True
    assert granted["integrations_insert"] is True
    assert granted["credentials_update"] is True
    assert granted["permissions_update"] is True


def test_integrations_are_isolated_between_tenants(client, db):
    alpha, alpha_admin, _ = create_admin_bundle(
        client, db, organization_name="Alpha Corp", email="alpha@example.com"
    )
    beta, _, _ = create_admin_bundle(
        client, db, organization_name="Beta Corp", email="beta@example.com"
    )

    integration_id = _make_integration(db, alpha, alpha_admin)

    use_tenant(db, UUID(beta["tenant_id"]))
    assert db.scalars(select(Integration)).all() == []

    use_tenant(db, UUID(alpha["tenant_id"]))
    visible = db.scalars(select(Integration)).all()
    assert [row.integration_id for row in visible] == [integration_id]

    # A forged tenant_id is rejected by the RLS WITH CHECK clause.
    use_tenant(db, UUID(beta["tenant_id"]))
    db.add(
        Integration(
            tenant_id=UUID(alpha["tenant_id"]),
            provider=PROVIDER_LOCAL_STORAGE,
            integration_name="Forged",
            created_by=alpha_admin.user_id,
        )
    )
    with pytest.raises(DBAPIError):
        db.commit()
    db.rollback()


def test_external_file_identity_is_unique_per_integration(client, db):
    organization, admin, token = create_admin_bundle(
        client, db, organization_name="Alpha Corp", email="alpha@example.com"
    )
    integration_id = _make_integration(db, organization, admin)
    first = create_document(client, token, document_name="First")
    second = create_document(client, token, document_name="Second")

    tenant_id = UUID(organization["tenant_id"])
    use_tenant(db, tenant_id)
    db.add(
        ExternalDocument(
            tenant_id=tenant_id,
            integration_id=integration_id,
            internal_document_id=UUID(first["document_id"]),
            provider=PROVIDER_LOCAL_STORAGE,
            external_file_id="policies/handbook.pdf",
            external_repository_id="policies",
        )
    )
    db.commit()

    use_tenant(db, tenant_id)
    db.add(
        ExternalDocument(
            tenant_id=tenant_id,
            integration_id=integration_id,
            internal_document_id=UUID(second["document_id"]),
            provider=PROVIDER_LOCAL_STORAGE,
            external_file_id="policies/handbook.pdf",
            external_repository_id="policies",
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_only_one_active_sync_job_per_integration(client, db):
    organization, admin, _ = create_admin_bundle(
        client, db, organization_name="Alpha Corp", email="alpha@example.com"
    )
    integration_id = _make_integration(db, organization, admin)
    tenant_id = UUID(organization["tenant_id"])

    use_tenant(db, tenant_id)
    db.add(
        IntegrationSyncJob(
            tenant_id=tenant_id,
            integration_id=integration_id,
            created_by=admin.user_id,
        )
    )
    db.commit()

    use_tenant(db, tenant_id)
    db.add(
        IntegrationSyncJob(
            tenant_id=tenant_id,
            integration_id=integration_id,
            created_by=admin.user_id,
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

    # A finished job releases the active slot.
    use_tenant(db, tenant_id)
    db.execute(
        update(IntegrationSyncJob)
        .where(
            IntegrationSyncJob.tenant_id == tenant_id,
            IntegrationSyncJob.integration_id == integration_id,
        )
        .values(sync_status="COMPLETED")
    )
    db.commit()

    use_tenant(db, tenant_id)
    db.add(
        IntegrationSyncJob(
            tenant_id=tenant_id,
            integration_id=integration_id,
            created_by=admin.user_id,
        )
    )
    db.commit()


def test_external_permission_requires_the_owning_integration(client, db):
    organization, admin, token = create_admin_bundle(
        client, db, organization_name="Alpha Corp", email="alpha@example.com"
    )
    integration_id = _make_integration(db, organization, admin)
    document = create_document(client, token)
    tenant_id = UUID(organization["tenant_id"])

    use_tenant(db, tenant_id)
    role_id = db.scalar(select(Role.role_id).where(Role.tenant_id == tenant_id))
    assert role_id is not None

    # Default provenance for an ordinary grant is MANUAL.
    manual = DocumentPermission(
        tenant_id=tenant_id,
        document_id=UUID(document["document_id"]),
        role_id=role_id,
        permission_type="READ",
        granted_by=admin.user_id,
    )
    db.add(manual)
    db.flush()
    assert manual.permission_origin == PERMISSION_ORIGIN_MANUAL
    assert manual.managed_by_integration_id is None
    db.commit()

    use_tenant(db, tenant_id)
    external_without_owner = DocumentPermission(
        tenant_id=tenant_id,
        document_id=UUID(document["document_id"]),
        role_id=role_id,
        permission_type="UPDATE",
        granted_by=admin.user_id,
        permission_origin="EXTERNAL",
    )
    db.add(external_without_owner)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

    use_tenant(db, tenant_id)
    owned = DocumentPermission(
        tenant_id=tenant_id,
        document_id=UUID(document["document_id"]),
        role_id=role_id,
        permission_type="UPDATE",
        granted_by=admin.user_id,
        permission_origin="EXTERNAL",
        managed_by_integration_id=integration_id,
    )
    db.add(owned)
    db.flush()
    assert owned.managed_by_integration_id == integration_id
    db.commit()


def test_value_constraints_reject_unknown_vocabulary(client, db):
    organization, admin, _ = create_admin_bundle(
        client, db, organization_name="Alpha Corp", email="alpha@example.com"
    )
    tenant_id = UUID(organization["tenant_id"])
    use_tenant(db, tenant_id)

    db.add(
        Integration(
            tenant_id=tenant_id,
            provider="dropbox",
            integration_name="Unsupported",
            created_by=admin.user_id,
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_credential_rows_require_a_real_nonce(client, db):
    organization, admin, _ = create_admin_bundle(
        client, db, organization_name="Alpha Corp", email="alpha@example.com"
    )
    integration_id = _make_integration(db, organization, admin)
    tenant_id = UUID(organization["tenant_id"])

    use_tenant(db, tenant_id)
    db.add(
        IntegrationCredential(
            tenant_id=tenant_id,
            integration_id=integration_id,
            provider=PROVIDER_LOCAL_STORAGE,
            ciphertext=b"ciphertext",
            nonce=b"short",
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

    use_tenant(db, tenant_id)
    db.add(
        IntegrationCredential(
            tenant_id=tenant_id,
            integration_id=integration_id,
            provider=PROVIDER_LOCAL_STORAGE,
            ciphertext=b"ciphertext",
            nonce=b"0123456789ab",
        )
    )
    db.commit()
