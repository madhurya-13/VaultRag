-- ===========================================================================
-- VaultRAG PostgreSQL bootstrap
-- ===========================================================================
-- This script is mounted into the official postgres image's
-- /docker-entrypoint-initdb.d directory and therefore runs ONCE, when the
-- data volume is first created.
--
-- It can also be executed manually against a local PostgreSQL installation:
--   psql -U postgres -f database/init.sql
--
-- This file creates the databases only. It deliberately does NOT create the
-- runtime/migration roles because their passwords must not be committed.
--
-- After this script, run the role bootstrap once per database:
--   psql -v app_password='...' -v migrator_password='...' \
--        -d vaultrag -f database/bootstrap_roles.sql
--   psql -v app_password='...' -v migrator_password='...' \
--        -d vaultrag_test -f database/bootstrap_roles.sql
--
-- Then apply the schema with the migrator URL:
--   cd backend
--   alembic upgrade head
--
-- The API connects as vaultrag_app (NOSUPERUSER, NOBYPASSRLS). Alembic connects
-- as vaultrag_migrator, which owns the tables and the security schema.
-- ===========================================================================

-- PostgreSQL has no CREATE DATABASE IF NOT EXISTS. \gexec executes the
-- generated statement only when the database is missing.
SELECT 'CREATE DATABASE vaultrag'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'vaultrag')\gexec

-- Dedicated database for pytest. The test suite DROPS and TRUNCATES objects in
-- this database, so it must never point at real data.
SELECT 'CREATE DATABASE vaultrag_test'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'vaultrag_test')\gexec

-- ===========================================================================
-- What the migrations add
-- ===========================================================================
-- Milestone 1:
--   * tenant root, users, roles, user_roles;
--   * composite tenant-safe foreign keys.
--
-- Milestone 2:
--   * documents, document_permissions, user_attributes, security_audit_logs;
--   * security.current_tenant_id() reading app.current_tenant_id;
--   * ENABLE + FORCE ROW LEVEL SECURITY on every tenant table;
--   * tenant_isolation policies for PUBLIC;
--   * least-privilege grants for vaultrag_app;
--   * append-only audit table (SELECT + INSERT only for the runtime role).
--
-- Tenant context is transaction-local:
--   SELECT set_config('app.current_tenant_id', :tenant_id, true);
-- Missing/malformed context evaluates to NULL and denies all tenant rows.
-- ===========================================================================
