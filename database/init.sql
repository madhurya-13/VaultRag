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
-- The application schema itself is owned by Alembic, not by this file:
--   cd backend
--   alembic upgrade head
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
-- MILESTONE 2 READINESS - NOT IMPLEMENTED YET
-- ===========================================================================
-- Milestone 1 enforces tenant isolation in the application layer and with
-- composite foreign keys. It does NOT enable PostgreSQL Row-Level Security.
--
-- The schema was designed so RLS can be added without restructuring:
--   * every tenant-owned table has a NOT NULL tenant_id;
--   * roles/users/user_roles all reference the tenant root;
--   * composite FKs prevent cross-tenant associations.
--
-- Planned Milestone 2 approach (do not run this yet):
--   1. Create a non-owner application role (e.g. vaultrag_app).
--   2. For each tenant table:
--        ALTER TABLE users ENABLE ROW LEVEL SECURITY;
--        ALTER TABLE users FORCE ROW LEVEL SECURITY;
--        CREATE POLICY tenant_isolation ON users
--          USING (tenant_id = current_setting('app.tenant_id', true)::uuid);
--   3. At the start of each request/transaction:
--        SET LOCAL app.tenant_id = '<authenticated tenant uuid>';
--   4. Keep application-level filters as defence in depth.
--
-- RLS is a database safety net, not a replacement for the authenticated
-- tenant context implemented in the API.
-- ===========================================================================
