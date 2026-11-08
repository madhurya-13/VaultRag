"""Pytest fixtures.

CRITICAL: this suite DROPS and TRUNCATES the database configured by
``TEST_DATABASE_URL``. A guard below refuses to run unless the database name
contains "test".

The application is tested as the restricted ``vaultrag_app`` role (NOSUPERUSER,
NOBYPASSRLS). Schema setup, Alembic migrations and test truncation use the
separate ``vaultrag_migrator`` role. This makes RLS tests exercise the same
privilege boundary as production instead of accidentally running as a
superuser.
"""

import atexit
import os
import shutil
import tempfile
from pathlib import Path
from uuid import UUID

from dotenv import dotenv_values
from sqlalchemy.engine import make_url

BACKEND_DIR = Path(__file__).resolve().parents[2]
TEST_STORAGE_DIR = Path(tempfile.mkdtemp(prefix="vaultrag-test-documents-"))
atexit.register(shutil.rmtree, TEST_STORAGE_DIR, ignore_errors=True)

# Environment variables win. Otherwise honour backend/.env so a developer who
# changed the local role passwords does not accidentally run tests against the
# defaults below. The defaults are intentionally restricted-role URLs.
_file_env = (
    dotenv_values(BACKEND_DIR / ".env") if (BACKEND_DIR / ".env").exists() else {}
)
TEST_DATABASE_URL = (
    os.environ.get("TEST_DATABASE_URL")
    or _file_env.get("TEST_DATABASE_URL")
    or "postgresql+psycopg://vaultrag_app:vaultrag_app_local_dev_password@localhost:5432/vaultrag_test"
)
TEST_MIGRATION_DATABASE_URL = (
    os.environ.get("TEST_MIGRATION_DATABASE_URL")
    or _file_env.get("TEST_MIGRATION_DATABASE_URL")
    or "postgresql+psycopg://vaultrag_migrator:vaultrag_migrator_local_dev_password@localhost:5432/vaultrag_test"
)
os.environ["ENVIRONMENT"] = "test"
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["MIGRATION_DATABASE_URL"] = TEST_MIGRATION_DATABASE_URL
os.environ["JWT_SECRET_KEY"] = "test-only-jwt-secret-0123456789abcdef0123456789abcdef"
os.environ["ACCESS_TOKEN_EXPIRE_MINUTES"] = "30"
os.environ["DOCUMENT_STORAGE_DIR"] = str(TEST_STORAGE_DIR)

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

import app.models  # noqa: E402,F401  (register all tables)
from app.core.database import SessionLocal, engine, set_tenant_context  # noqa: E402
from app.main import app  # noqa: E402

def _assert_safe_test_database() -> None:
    for label, url in (
        ("TEST_DATABASE_URL", TEST_DATABASE_URL),
        ("TEST_MIGRATION_DATABASE_URL", TEST_MIGRATION_DATABASE_URL),
    ):
        database_name = make_url(url).database or ""
        if "test" not in database_name.lower():
            raise RuntimeError(
                f"Refusing to run destructive tests against a non-test database: "
                f"{label}={database_name!r}. Use a *_test database."
            )


@pytest.fixture(scope="session")
def migration_engine():
    """DDL-capable engine for schema setup/cleanup. Never used by TestClient."""
    migration_engine = create_engine(
        TEST_MIGRATION_DATABASE_URL,
        pool_pre_ping=True,
        future=True,
    )
    try:
        yield migration_engine
    finally:
        migration_engine.dispose()


@pytest.fixture(scope="session", autouse=True)
def _database_schema(migration_engine):
    """Reset the test schema and apply every Alembic migration as the migrator."""
    _assert_safe_test_database()

    # Drop both schemas so a stale RLS policy or table cannot affect the run.
    with migration_engine.begin() as connection:
        connection.execute(text("DROP SCHEMA IF EXISTS security CASCADE"))
        connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        connection.execute(
            text("CREATE SCHEMA public AUTHORIZATION vaultrag_migrator")
        )

    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    command.upgrade(config, "head")

    yield

    # Intentionally do not drop the schema: leaving it in place makes debugging
    # failed tests easier. The next run drops it first.


@pytest.fixture(autouse=True)
def _clean_tables(migration_engine):
    """Truncate between tests so tests cannot depend on execution order.

    TRUNCATE is performed by the table owner. RLS does not filter TRUNCATE, but
    using the migrator keeps cleanup independent of any tenant context.
    """
    with migration_engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE TABLE "
                "security_audit_logs, document_chunks, document_ingestion_jobs, "
                "document_permissions, documents, user_attributes, user_roles, "
                "users, roles, organizations "
                "RESTART IDENTITY CASCADE"
            )
        )
    yield


@pytest.fixture(scope="session")
def client(_database_schema):
    """One TestClient; its context manager runs the FastAPI lifespan.

    The session-scoped client is only created after the schema fixture, so the
    lifespan health check always sees migrated tables.
    """
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def db():
    """Direct application-role session for assertions and constraint tests."""
    with SessionLocal() as session:
        yield session
        session.rollback()


@pytest.fixture()
def migration_db(migration_engine):
    """Migrator session for test setup that needs privileges the app must not have.

    RLS still applies (FORCE + PUBLIC policy); callers must set a tenant context.
    """
    with Session(migration_engine) as session:
        yield session
        session.rollback()


@pytest.fixture()
def app_engine():
    """The restricted runtime engine, for tests that open raw connections."""
    return engine


@pytest.fixture()
def app_sessionmaker():
    return SessionLocal


@pytest.fixture()
def tenant_context():
    """Helper that sets the transaction-local RLS tenant on a session."""
    def _set(session: Session, tenant_id: str | UUID) -> None:
        set_tenant_context(session, UUID(str(tenant_id)))

    return _set
