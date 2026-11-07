"""PostgreSQL row-level security and runtime role grants.

Revision ID: 9d4e6f8a1b2c
Revises: 7c1e9a4b2f6d
Create Date: 2026-11-05

This migration turns tenant isolation into a database-enforced boundary:

* ``security.current_tenant_id()`` reads ``app.current_tenant_id`` from the
  current transaction. Missing/malformed values return NULL.
* Every tenant-owned table gets ENABLE + FORCE ROW LEVEL SECURITY.
* Policies apply to PUBLIC (including the table-owning migrator). FORCE therefore
  protects against accidental owner access too. Data migrations must establish a
  tenant context explicitly; bootstrap/superuser operations can still bypass RLS.
* The runtime role receives least-privilege table grants. It has no DELETE on
  documents, user_attributes or audit logs, and no UPDATE/DELETE on audit logs.

The role itself must exist before this migration. Run
``database/bootstrap_roles.sql`` as a database owner first.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "9d4e6f8a1b2c"
down_revision: Union[str, None] = "7c1e9a4b2f6d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

APP_ROLE = "vaultrag_app"

TENANT_TABLES = (
    "organizations",
    "roles",
    "users",
    "user_roles",
    "documents",
    "document_permissions",
    "user_attributes",
    "security_audit_logs",
)

# Least-privilege runtime grants. Audit rows are append-only: SELECT + INSERT.
APP_GRANTS = {
    "organizations": "SELECT, INSERT",
    "roles": "SELECT, INSERT",
    "users": "SELECT, INSERT, UPDATE",
    "user_roles": "SELECT, INSERT, DELETE",
    "documents": "SELECT, INSERT, UPDATE",
    "document_permissions": "SELECT, INSERT, UPDATE",
    "user_attributes": "SELECT, INSERT, UPDATE",
    "security_audit_logs": "SELECT, INSERT",
}


def upgrade() -> None:
    # Fail loudly rather than creating policies that no role can exercise.
    op.execute(
        sa.text(
            f"""
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                    RAISE EXCEPTION
                        'Role {APP_ROLE} is missing. Run database/bootstrap_roles.sql first.';
                END IF;
            END
            $$
            """
        )
    )

    op.execute(sa.text("CREATE SCHEMA IF NOT EXISTS security"))
    op.execute(sa.text("REVOKE ALL ON SCHEMA security FROM PUBLIC"))
    op.execute(sa.text(f"GRANT USAGE ON SCHEMA security TO {APP_ROLE}"))

    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION security.current_tenant_id()
            RETURNS uuid
            LANGUAGE plpgsql
            STABLE
            PARALLEL SAFE
            AS $$
            BEGIN
                RETURN NULLIF(
                    current_setting('app.current_tenant_id', true),
                    ''
                )::uuid;
            EXCEPTION
                WHEN invalid_text_representation THEN
                    RETURN NULL;
            END;
            $$
            """
        )
    )
    op.execute(
        sa.text(
            "REVOKE ALL ON FUNCTION security.current_tenant_id() FROM PUBLIC"
        )
    )
    op.execute(
        sa.text(
            f"GRANT EXECUTE ON FUNCTION security.current_tenant_id() TO {APP_ROLE}"
        )
    )

    for table in TENANT_TABLES:
        op.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"DROP POLICY IF EXISTS tenant_isolation ON {table}"))
        op.execute(
            sa.text(
                f"""
                CREATE POLICY tenant_isolation ON {table}
                AS PERMISSIVE
                FOR ALL
                TO PUBLIC
                USING (
                    security.current_tenant_id() IS NOT NULL
                    AND tenant_id = security.current_tenant_id()
                )
                WITH CHECK (
                    security.current_tenant_id() IS NOT NULL
                    AND tenant_id = security.current_tenant_id()
                )
                """
            )
        )

    # Remove any previous runtime grants first, including grants on
    # alembic_version. Then apply only the explicit allow-list.
    op.execute(sa.text(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {APP_ROLE}"))
    for table, privileges in APP_GRANTS.items():
        op.execute(sa.text(f"GRANT {privileges} ON {table} TO {APP_ROLE}"))

    # The app role can use the public schema but cannot create objects in it.
    op.execute(sa.text(f"REVOKE CREATE ON SCHEMA public FROM {APP_ROLE}"))
    op.execute(sa.text(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}"))


def downgrade() -> None:
    for table in TENANT_TABLES:
        op.execute(sa.text(f"DROP POLICY IF EXISTS tenant_isolation ON {table}"))
        op.execute(sa.text(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY"))

    op.execute(sa.text(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {APP_ROLE}"))
    op.execute(sa.text(f"REVOKE ALL ON SCHEMA security FROM {APP_ROLE}"))
    op.execute(
        sa.text("DROP FUNCTION IF EXISTS security.current_tenant_id()")
    )
    op.execute(sa.text("DROP SCHEMA IF EXISTS security CASCADE"))
