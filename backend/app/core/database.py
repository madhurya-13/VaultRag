"""Database engine, declarative base and request-scoped sessions.

Why these choices
-----------------
* SQLAlchemy 2.0 style ``DeclarativeBase`` + ``Mapped`` gives typed models.
* A naming convention makes Alembic migrations deterministic: constraints get
  stable names instead of database-generated ones, which matters when you later
  write migration scripts.
* ``expire_on_commit=False`` keeps ORM objects usable after the service layer
  commits, avoiding an extra SELECT while building API responses.
* ``get_db`` owns the request transaction: it commits once, after the route has
  completed successfully, and rolls back on any exception. Services therefore
  call ``flush()`` instead of committing internally. This is essential for RLS:
  ``set_config(..., true)`` is transaction-local and would be lost by a nested
  commit.
* ``set_tenant_context`` writes the tenant into the current transaction. RLS
  policies call ``security.current_tenant_id()``, which reads this setting. A
  missing or malformed setting evaluates to NULL, so policies deny by default.
"""

from collections.abc import Generator
from uuid import UUID

from sqlalchemy import MetaData, create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings

# Deterministic constraint names (important for Alembic + composite FKs).
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


settings = get_settings()

# pool_pre_ping discards stale connections after network/database restarts.
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=settings.database_pool_size,
    max_overflow=settings.database_max_overflow,
    pool_timeout=settings.database_pool_timeout_seconds,
    echo=settings.sql_echo,
    future=True,
)

SessionLocal = sessionmaker(
    bind=engine,
    class_=Session,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency: one transaction per request with guaranteed cleanup.

    A successful request commits exactly once. This includes read-only requests;
    the commit is cheap and makes the transaction boundary unambiguous. Any
    exception rolls the transaction back, which also clears the tenant context
    before the connection is returned to the pool.
    """
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def set_tenant_context(db: Session, tenant_id: UUID) -> None:
    """Bind the current transaction to a tenant for PostgreSQL RLS.

    ``set_config(..., true)`` is transaction-local, parameterized and safe to
    call on every request. Calling it again in the same transaction replaces the
    value (used by bootstrap/provisioning flows that deliberately switch from a
    new tenant to an existing one).
    """
    if tenant_id is None:
        raise ValueError("tenant_id is required to establish tenant context")

    db.execute(
        text("SELECT set_config('app.current_tenant_id', :tenant_id, true)"),
        {"tenant_id": str(tenant_id)},
    )
