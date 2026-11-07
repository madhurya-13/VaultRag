-- VaultRAG PostgreSQL role bootstrap.
--
-- Run this ONCE as a database owner/superuser for each database (development,
-- test and production), before applying the RLS migration:
--
--   psql -v app_password='...' -v migrator_password='...' \
--        -d vaultrag -f database/bootstrap_roles.sql
--
-- The two passwords are intentionally supplied as psql variables, never
-- committed to the repository.
--
-- Roles:
--   vaultrag_app      runtime application role. LOGIN, no SUPERUSER, no
--                     BYPASSRLS. It owns no tables and cannot run DDL.
--   vaultrag_migrator schema owner / migration role. LOGIN, no SUPERUSER.
--                     It owns public tables and the security schema.
--
-- Re-running is safe: roles are altered to the required attributes and
-- existing schema/table ownership is transferred to the migrator.

\set ON_ERROR_STOP on

SELECT format(
    'CREATE ROLE vaultrag_app LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS',
    :'app_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'vaultrag_app')
\gexec

SELECT format(
    'CREATE ROLE vaultrag_migrator LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS',
    :'migrator_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'vaultrag_migrator')
\gexec

ALTER ROLE vaultrag_app WITH
    LOGIN PASSWORD :'app_password'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;

ALTER ROLE vaultrag_migrator WITH
    LOGIN PASSWORD :'migrator_password'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;

-- Connection and schema-creation privileges for the current database.
SELECT format(
    'GRANT CONNECT ON DATABASE %I TO vaultrag_app, vaultrag_migrator',
    current_database()
)
\gexec

SELECT format(
    'GRANT CREATE ON DATABASE %I TO vaultrag_migrator',
    current_database()
)
\gexec

-- The migrator owns the schema and all existing objects. The app role gets
-- USAGE only; INSERT/SELECT/UPDATE grants are applied by the RLS migration.
ALTER SCHEMA public OWNER TO vaultrag_migrator;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON SCHEMA public FROM vaultrag_app;
GRANT USAGE ON SCHEMA public TO vaultrag_app;

DO $$
DECLARE
    obj record;
BEGIN
    FOR obj IN
        SELECT tablename AS name FROM pg_tables WHERE schemaname = 'public'
    LOOP
        EXECUTE format('ALTER TABLE public.%I OWNER TO vaultrag_migrator', obj.name);
    END LOOP;

    FOR obj IN
        SELECT sequence_name AS name
        FROM information_schema.sequences
        WHERE sequence_schema = 'public'
    LOOP
        EXECUTE format('ALTER SEQUENCE public.%I OWNER TO vaultrag_migrator', obj.name);
    END LOOP;
END
$$;

-- If the security schema has already been created by a prior migration, make
-- the migrator its owner too. This block is a no-op on a fresh database.
DO $$
DECLARE
    obj record;
BEGIN
    IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'security') THEN
        EXECUTE 'ALTER SCHEMA security OWNER TO vaultrag_migrator';

        FOR obj IN
            SELECT tablename AS name FROM pg_tables WHERE schemaname = 'security'
        LOOP
            EXECUTE format('ALTER TABLE security.%I OWNER TO vaultrag_migrator', obj.name);
        END LOOP;

        FOR obj IN
            SELECT p.oid::regprocedure AS signature
            FROM pg_proc p
            JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE n.nspname = 'security'
        LOOP
            EXECUTE format('ALTER FUNCTION %s OWNER TO vaultrag_migrator', obj.signature);
        END LOOP;
    END IF;
END
$$;
