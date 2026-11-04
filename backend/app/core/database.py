"""Database engine, declarative base and request-scoped sessions.

Why these choices
-----------------
* SQLAlchemy 2.0 style ``DeclarativeBase`` + ``Mapped`` gives typed models.
* A naming convention makes Alembic migrations deterministic: constraints get
  stable names instead of database-generated ones, which matters when you later
  write migration scripts.
* ``expire_on_commit=False`` keeps ORM objects usable after the service layer
  commits, avoiding an extra SELECT while building API responses.
* ``get_db`` rolls back on any exception and always closes the session. This is
  the only place request sessions are created, so transactions cannot leak
  between requests.
"""

from collections.abc import Generator

from sqlalchemy import MetaData, create_engine
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
    """FastAPI dependency: one session per request, guaranteed cleanup."""
    db = SessionLocal()
    try:
        yield db
    except Exception:
        # The service layer may have flushed but not committed. Never leave a
        # failed transaction open on a pooled connection.
        db.rollback()
        raise
    finally:
        db.close()
