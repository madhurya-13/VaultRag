"""Employee-to-admin escalation and immediate role revocation attacks."""
from app.tests.test_rbac import (
    test_document_permission_does_not_grant_administrative_capability,
    test_non_admin_cannot_use_administrator_endpoints,
    test_role_grant_takes_effect_immediately_when_revoked,
)
from app.tests.test_auth import test_public_registration_cannot_assign_roles_or_activation

__all__ = [
    "test_document_permission_does_not_grant_administrative_capability",
    "test_non_admin_cannot_use_administrator_endpoints",
    "test_role_grant_takes_effect_immediately_when_revoked",
    "test_public_registration_cannot_assign_roles_or_activation",
]
