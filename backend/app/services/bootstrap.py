"""Safe initial-administrator provisioning.

This function is shared by the CLI script and the test-suite. It is NOT exposed
over HTTP: creating the first ADMIN is a server-operator action, not a public
API call.
"""

import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import set_tenant_context
from app.core.exceptions import ConflictError
from app.models import User
from app.services.organizations import get_organization_or_raise
from app.services.users import create_user, normalize_email

logger = logging.getLogger(__name__)


def provision_initial_admin(
    db: Session,
    *,
    tenant_id: UUID,
    username: str,
    email: str,
    password: str,
) -> User:
    """Create an active ADMIN account in an existing, active tenant.

    Safety properties:
    * The tenant must already exist (organizations are registered separately).
    * The tenant must be active.
    * The email must not already exist in that tenant; the script never
      silently promotes an existing account.
    * ADMIN is resolved inside the tenant, so the new admin can only ever receive
      that tenant's ADMIN role.
    """
    # This is an operator action with an explicit tenant argument, so the
    # service is allowed to establish the RLS context itself.
    set_tenant_context(db, tenant_id)
    get_organization_or_raise(db, tenant_id, require_active=True)

    existing = db.scalar(
        select(User.user_id).where(
            User.tenant_id == tenant_id,
            User.email == normalize_email(email),
        )
    )
    if existing is not None:
        raise ConflictError(
            "A user with this email already exists in this organization; "
            "refusing to silently promote it"
        )

    admin = create_user(
        db,
        tenant_id=tenant_id,
        username=username,
        email=email,
        password=password,
        role_names=["ADMIN"],
        is_active=True,
    )
    logger.info("Provisioned initial admin user_id=%s tenant_id=%s", admin.user_id, tenant_id)
    return admin
