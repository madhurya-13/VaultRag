"""Pytest fixtures.

CRITICAL: this suite DROPS and TRUNCATES the database configured by
``TEST_DATABASE_URL``. A guard below refuses to run unless the database name
contains "test".
"""

import os
from uuid import UUID

# Configure the application BEFORE importing app modules. Environment variables
# take precedence over backend/.env, so tests can never accidentally use the
# development database when the defaults below are used.
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://vaultrag:vaultrag_local_dev_password@localhost:5432/vaultrag_test",
)
os.environ["ENVIRONMENT"] = "test"
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["JWT_SECRET_KEY"] = "test-only-jwt-secret-0123456789abcdef0123456789abcdef"
os.environ["ACCESS_TOKEN_EXPIRE_MINUTES"] = "30"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

import app.models  # noqa: E402,F401  (register all tables)
from app.core.database import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402


def _assert_safe_test_database() -> None:
    database_name = make_url(TEST_DATABASE_URL).database or ""
    if "test" not in database_name.lower():
        raise RuntimeError(
            "Refusing to run destructive tests against a non-test database: "
            f"{database_name!r}. Set TEST_DATABASE_URL to a *_test database."
        )


@pytest.fixture(scope="session", autouse=True)
def _database_schema():
    """Create a clean schema once for the whole session."""
    _assert_safe_test_database()
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    # Intentionally do not drop the schema: leaving it in place makes debugging
    # failed tests easier. The next run drops it first.


@pytest.fixture(autouse=True)
def _clean_tables():
    """Truncate between tests so tests cannot depend on execution order."""
    with engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE TABLE user_roles, users, roles, organizations "
                "RESTART IDENTITY CASCADE"
            )
        )
    yield


@pytest.fixture(scope="session")
def client():
    """One TestClient; its context manager runs the FastAPI lifespan."""
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def db():
    """Direct database session for assertions and constraint tests."""
    with SessionLocal() as session:
        yield session
        session.rollback()
