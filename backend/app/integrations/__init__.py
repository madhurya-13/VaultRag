"""Enterprise integration framework (Milestone 9).

Importing this package registers the connectors that ship with VaultRAG. Only
the registry, the value objects and the credential envelope are import-safe for
every process; ORM models live in ``app.integrations.models`` and are imported
by ``app.models`` for Alembic.
"""

from app.integrations import providers  # noqa: F401  (registers built-ins)

__all__ = ["providers"]
