"""The operator bootstrap must create a usable least-surprise first admin."""

from uuid import UUID

from sqlalchemy import select

from app.core.security_constants import (
    ATTR_CLEARANCE_LEVEL,
    ATTR_DEPARTMENT,
    ATTR_EMPLOYMENT_STATUS,
    EMPLOYMENT_ACTIVE,
)
from app.models import User, UserAttribute
from app.scripts.bootstrap_admin import main
from app.tests.helpers import register_organization, use_tenant


def test_bootstrap_admin_seeds_required_abac_attributes(client, db):
    organization = register_organization(client, "Bootstrap Admin Tenant")
    tenant_id = UUID(organization["tenant_id"])

    assert main(
        [
            "--tenant-id",
            str(tenant_id),
            "--username",
            "bootstrap_admin",
            "--email",
            "bootstrap-admin@example.com",
            "--password",
            "BootstrapSecure123!",
            "--department",
            "FINANCE",
            "--clearance-level",
            "3",
        ]
    ) == 0

    use_tenant(db, tenant_id)
    user = db.scalar(
        select(User).where(
            User.tenant_id == tenant_id,
            User.email == "bootstrap-admin@example.com",
        )
    )
    assert user is not None
    attributes = {
        row.attribute_name: row.attribute_value
        for row in db.scalars(
            select(UserAttribute).where(
                UserAttribute.tenant_id == tenant_id,
                UserAttribute.user_id == user.user_id,
            )
        )
    }
    assert attributes == {
        ATTR_DEPARTMENT: "FINANCE",
        ATTR_CLEARANCE_LEVEL: "3",
        ATTR_EMPLOYMENT_STATUS: EMPLOYMENT_ACTIVE,
    }
