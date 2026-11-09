"""PostgreSQL row-level security tests.

These tests use the real runtime role and the real policies. They are the
database-level counterpart to the application-level isolation tests.
"""

from uuid import UUID

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from app.core.config import get_settings
from app.tests.helpers import (
    FakeEmbeddingService,
    admin_bundle_with_attributes,
    grant_document_permission,
    make_pdf_bytes,
    process_document,
    read_document,
    register_organization,
    tenant_user_with_attributes,
    upload_pdf,
    use_tenant,
)

TENANT_TABLES = (
    "organizations",
    "roles",
    "users",
    "user_roles",
    "documents",
    "document_permissions",
    "document_chunks",
    "document_ingestion_jobs",
    "user_attributes",
    "security_audit_logs",
)


def test_runtime_and_migration_roles_are_restricted(app_engine):
    with app_engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT rolname, rolsuper, rolbypassrls, rolcreatedb, "
                "rolcreaterole, rolcanlogin "
                "FROM pg_roles WHERE rolname IN ('vaultrag_app', 'vaultrag_migrator')"
            )
        ).all()

    roles = {row.rolname: row for row in rows}
    assert set(roles) == {"vaultrag_app", "vaultrag_migrator"}

    app_role = roles["vaultrag_app"]
    assert app_role.rolcanlogin is True
    assert app_role.rolsuper is False
    assert app_role.rolbypassrls is False
    assert app_role.rolcreatedb is False
    assert app_role.rolcreaterole is False

    migrator = roles["vaultrag_migrator"]
    assert migrator.rolcanlogin is True
    assert migrator.rolsuper is False
    assert migrator.rolbypassrls is False
    assert migrator.rolcreatedb is False


def test_every_tenant_table_has_force_rls_and_a_policy(app_engine):
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
        policies = connection.execute(
            text(
                "SELECT tablename, policyname, qual, with_check "
                "FROM pg_policies WHERE schemaname = 'public'"
            )
        ).all()

    for table in TENANT_TABLES:
        assert table in tables
        assert tables[table].relrowsecurity is True, table
        assert tables[table].relforcerowsecurity is True, table

    by_table = {row.tablename: row for row in policies}
    for table in TENANT_TABLES:
        assert table in by_table, table
        policy = by_table[table]
        assert policy.policyname == "tenant_isolation"
        assert "current_tenant_id" in (policy.qual or "")
        assert "current_tenant_id" in (policy.with_check or "")


def test_runtime_role_has_least_privilege_grants(app_engine):
    checks = {
        "documents_delete": "has_table_privilege('vaultrag_app', 'documents', 'DELETE')",
        "documents_update": "has_table_privilege('vaultrag_app', 'documents', 'UPDATE')",
        "organizations_update": "has_table_privilege('vaultrag_app', 'organizations', 'UPDATE')",
        "audit_update": "has_table_privilege('vaultrag_app', 'security_audit_logs', 'UPDATE')",
        "audit_delete": "has_table_privilege('vaultrag_app', 'security_audit_logs', 'DELETE')",
        "audit_insert": "has_table_privilege('vaultrag_app', 'security_audit_logs', 'INSERT')",
        "public_create": "has_schema_privilege('vaultrag_app', 'public', 'CREATE')",
        "db_create": "has_database_privilege('vaultrag_app', current_database(), 'CREATE')",
        "security_usage": "has_schema_privilege('vaultrag_app', 'security', 'USAGE')",
        "function_execute": "has_function_privilege('vaultrag_app', 'security.current_tenant_id()', 'EXECUTE')",
        "migrator_db_create": "has_database_privilege('vaultrag_migrator', current_database(), 'CREATE')",
    }
    with app_engine.connect() as connection:
        result = {
            name: connection.scalar(text(f"SELECT {expression}"))
            for name, expression in checks.items()
        }

    assert result["documents_delete"] is False
    assert result["documents_update"] is True
    assert result["organizations_update"] is False
    assert result["audit_update"] is False
    assert result["audit_delete"] is False
    assert result["audit_insert"] is True
    assert result["public_create"] is False
    assert result["db_create"] is False
    assert result["security_usage"] is True
    assert result["function_execute"] is True
    assert result["migrator_db_create"] is True


def test_current_tenant_id_is_null_for_missing_or_malformed_context(client, app_engine):
    organization = register_organization(client, "TechNova")
    tenant_id = organization["tenant_id"]

    with app_engine.connect() as connection:
        assert connection.scalar(text("SELECT security.current_tenant_id()")) is None

        connection.execute(
            text("SELECT set_config('app.current_tenant_id', 'not-a-uuid', true)")
        )
        assert connection.scalar(text("SELECT security.current_tenant_id()")) is None

        connection.execute(
            text("SELECT set_config('app.current_tenant_id', :tenant_id, true)"),
            {"tenant_id": tenant_id},
        )
        assert str(connection.scalar(text("SELECT security.current_tenant_id()"))) == tenant_id


def test_no_context_or_wrong_context_denies_all_tenant_rows(client, app_engine):
    organization = register_organization(client, "TechNova")
    other = register_organization(client, "BioCore")
    tenant_id = organization["tenant_id"]

    with app_engine.connect() as connection:
        for table in TENANT_TABLES:
            assert connection.scalar(text(f"SELECT count(*) FROM {table}")) == 0, table

        connection.execute(
            text("SELECT set_config('app.current_tenant_id', :tenant_id, true)"),
            {"tenant_id": other["tenant_id"]},
        )
        assert (
            connection.scalar(
                text("SELECT count(*) FROM organizations WHERE tenant_id = :tenant_id"),
                {"tenant_id": tenant_id},
            )
            == 0
        )
        assert (
            connection.scalar(
                text("SELECT count(*) FROM organizations WHERE tenant_id = :tenant_id"),
                {"tenant_id": other["tenant_id"]},
            )
            == 1
        )


def test_direct_database_access_without_context_sees_no_tenant_data(
    client, db, app_engine, monkeypatch
):
    embedder = FakeEmbeddingService()
    monkeypatch.setattr(
        "app.services.ingestion_service.get_embedding_service",
        lambda: embedder,
    )
    org, admin, admin_token = admin_bundle_with_attributes(
        client, db, organization_name="Alpha Corp", email="admin@alpha.com", username="alpha_admin"
    )
    upload = upload_pdf(
        client,
        admin_token,
        make_pdf_bytes(
            ["RLS chunk isolation policy text for direct database access verification."]
        ),
        filename="rls-policy.pdf",
        document_name="RLS Policy.pdf",
        department="HR",
    )
    assert upload.status_code == 201, upload.text
    document = upload.json()
    processed = process_document(client, admin_token, document["document_id"])
    assert processed.status_code == 200, processed.text

    user, _ = tenant_user_with_attributes(
        client,
        admin_token,
        org["tenant_id"],
        email="hr@alpha.com",
        username="hr_user",
        role_names=["HR"],
    )
    grant_document_permission(
        client,
        admin_token,
        document["document_id"],
        permission_type="READ",
        user_id=user["user_id"],
    )
    assert read_document(client, admin_token, document["document_id"]).status_code == 200

    with app_engine.connect() as connection:
        for table in TENANT_TABLES:
            assert connection.scalar(text(f"SELECT count(*) FROM {table}")) == 0, table

        connection.execute(
            text("SELECT set_config('app.current_tenant_id', :tenant_id, true)"),
            {"tenant_id": org["tenant_id"]},
        )
        for table in TENANT_TABLES:
            count = connection.scalar(text(f"SELECT count(*) FROM {table}"))
            assert count > 0, (table, count)


def test_insert_with_mismatched_tenant_is_blocked_by_rls(client, app_engine):
    organization = register_organization(client, "TechNova")
    other = register_organization(client, "BioCore")

    with app_engine.connect() as connection:
        connection.execute(
            text("SELECT set_config('app.current_tenant_id', :tenant_id, true)"),
            {"tenant_id": organization["tenant_id"]},
        )
        with pytest.raises(DBAPIError) as exc_info:
            connection.execute(
                text(
                    "INSERT INTO security_audit_logs "
                    "(event_id, tenant_id, resource_type, action, decision, reason, policy, request_id) "
                    "VALUES (gen_random_uuid(), :tenant_id, 'DOCUMENT', 'READ', "
                    "'ALLOW', 'cross tenant insert', 'ALLOW', gen_random_uuid())"
                ),
                {"tenant_id": other["tenant_id"]},
            )
        assert "row-level security" in str(exc_info.value).lower()
        connection.rollback()

        assert (
            connection.scalar(
                text("SELECT count(*) FROM security_audit_logs WHERE tenant_id = :tenant_id"),
                {"tenant_id": other["tenant_id"]},
            )
            == 0
        )


def test_tenant_context_does_not_leak_across_pooled_connections(client):
    organization = register_organization(client, "TechNova")
    tenant_id = organization["tenant_id"]

    # pool_size=1 guarantees the second checkout reuses the same physical
    # connection, so this test really proves the context was cleared.
    pooled_engine = create_engine(
        get_settings().database_url,
        pool_size=1,
        max_overflow=0,
        pool_pre_ping=True,
    )
    try:
        # Case 1: commit clears a transaction-local set_config.
        with pooled_engine.connect() as connection:
            connection.execute(
                text("SELECT set_config('app.current_tenant_id', :tenant_id, true)"),
                {"tenant_id": tenant_id},
            )
            assert connection.scalar(text("SELECT count(*) FROM organizations")) == 1
            connection.commit()

        with pooled_engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM organizations")) == 0
            assert connection.scalar(text("SELECT security.current_tenant_id()")) is None

        # Case 2: returning a connection with an open transaction rolls it back,
        # which also clears the transaction-local context.
        with pooled_engine.connect() as connection:
            connection.execute(
                text("SELECT set_config('app.current_tenant_id', :tenant_id, true)"),
                {"tenant_id": tenant_id},
            )
            assert connection.scalar(text("SELECT count(*) FROM organizations")) == 1
            # Deliberately do not commit; close() returns the connection to the pool.

        with pooled_engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM organizations")) == 0
            assert connection.scalar(text("SELECT security.current_tenant_id()")) is None
    finally:
        pooled_engine.dispose()


def test_force_rls_subjects_the_table_owner_to_tenant_context(client, migration_db):
    organization = register_organization(client, "TechNova")
    tenant_id = organization["tenant_id"]

    # The migrator owns these tables. Without FORCE + a PUBLIC policy it would
    # see everything; without a context it must see nothing.
    assert migration_db.scalar(text("SELECT count(*) FROM organizations")) == 0

    use_tenant(migration_db, tenant_id)
    assert migration_db.scalar(text("SELECT count(*) FROM organizations")) == 1
