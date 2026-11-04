"""Import every model so SQLAlchemy's registry and Alembic see them all."""

from app.models.organization import Organization
from app.models.role import Role
from app.models.user import User
from app.models.user_role import UserRole

__all__ = ["Organization", "Role", "User", "UserRole"]
