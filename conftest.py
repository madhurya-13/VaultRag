"""Load backend PostgreSQL fixtures for repository-level security tests."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "backend"))
pytest_plugins = ("app.tests.conftest",)
