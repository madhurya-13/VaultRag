"""Knowledge graph unit tests are database-free.

The repository-wide conftest builds the PostgreSQL schema for every test
session. These modules exercise pure functions only (normalization, extraction
validation, traversal, conflict grouping, seed tokenization), so this package
overrides the two autouse database fixtures. Tests that do need a database
belong outside this package.
"""

import pytest


@pytest.fixture(scope="session", autouse=True)
def _database_schema():
    yield


@pytest.fixture(autouse=True)
def _clean_tables():
    yield
