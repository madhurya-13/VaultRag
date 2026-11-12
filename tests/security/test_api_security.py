"""Authentication, API authorization and request validation attacks."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import jwt
import pytest
from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.core.security import create_access_token
from app.tests.helpers import auth_headers, tenant_user_with_attributes
from app.tests.test_auth import (
    test_expired_jwt_is_rejected,
    test_missing_or_malformed_bearer_token_is_rejected,
    test_modified_jwt_is_rejected,
    test_public_registration_cannot_assign_roles_or_activation,
)
from app.tests.test_rbac import test_non_admin_cannot_use_administrator_endpoints

__all__ = [
    "test_expired_jwt_is_rejected",
    "test_missing_or_malformed_bearer_token_is_rejected",
    "test_modified_jwt_is_rejected",
    "test_public_registration_cannot_assign_roles_or_activation",
    "test_non_admin_cannot_use_administrator_endpoints",
]


def test_signed_employee_token_cannot_forge_admin_role(client, db):
    """The API reloads effective roles from PostgreSQL instead of trusting JWT."""
    from app.tests.helpers import admin_bundle_with_attributes

    organization, _, admin_token = admin_bundle_with_attributes(
        client,
        db,
        organization_name=f"Escalation {uuid4()}",
        email=f"escalation-admin-{uuid4()}@example.com",
        username="admin",
    )
    employee, _ = tenant_user_with_attributes(
        client,
        admin_token,
        organization["tenant_id"],
        email=f"employee-{uuid4()}@example.com",
        username="employee",
        role_names=["EMPLOYEE"],
    )
    forged_role_token, _ = create_access_token(
        user_id=employee["user_id"],
        tenant_id=organization["tenant_id"],
        roles=["ADMIN"],
    )

    response = client.get("/users", headers=auth_headers(forged_role_token))
    assert response.status_code == 403


def test_access_token_issuer_and_audience_are_enforced(client, db):
    """Valid signatures from the right key are insufficient with wrong claims."""
    from app.tests.helpers import create_admin_bundle

    organization, admin, _ = create_admin_bundle(
        client,
        db,
        organization_name=f"Claims {uuid4()}",
        email=f"claims-admin-{uuid4()}@example.com",
    )
    settings = get_settings()
    base = {
        "sub": str(admin.user_id),
        "tenant_id": str(organization["tenant_id"]),
        "roles": ["ADMIN"],
        "type": "access",
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
    }
    now = datetime.now(timezone.utc)
    for changed in ({"iss": "other-service"}, {"aud": "other-api"}):
        payload = {
            **base,
            **changed,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=5)).timestamp()),
        }
        token = jwt.encode(
            payload,
            settings.jwt_secret_key.get_secret_value(),
            algorithm=settings.jwt_algorithm,
        )
        response = client.get("/auth/me", headers=auth_headers(token))
        assert response.status_code == 401


def test_production_settings_reject_wildcard_hosts_and_privileged_db_role():
    common = {
        "environment": "production",
        "database_url": "postgresql+psycopg://vaultrag_app:longpassword@db/vaultrag",
        "jwt_secret_key": "a-distinct-production-secret-with-more-than-32-chars",
        "cors_origins": ["https://demo.example"],
        "allowed_hosts": ["demo.example"],
    }
    Settings(**common)

    with pytest.raises(ValidationError, match="wildcard"):
        Settings(**{**common, "allowed_hosts": ["*"]})
    with pytest.raises(ValidationError, match="wildcard"):
        Settings(**{**common, "allowed_hosts": ["*.demo.example"]})
    with pytest.raises(ValidationError, match="HTTPS"):
        Settings(**{**common, "cors_origins": ["http://demo.example"]})
    with pytest.raises(ValidationError, match="valid HTTPS origins"):
        Settings(**{**common, "cors_origins": ["https://demo.example/api"]})
    with pytest.raises(ValidationError, match="least-privilege"):
        Settings(
            **{
                **common,
                "database_url": "postgresql+psycopg://postgres:pw@db/vaultrag",
            }
        )


def test_host_header_is_restricted_and_security_headers_are_present(client):
    denied_host = client.get("/", headers={"Host": "attacker.invalid"})
    assert denied_host.status_code == 400

    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Cache-Control"] == "no-store"


def test_sql_injection_payload_is_treated_as_a_filter_value(client, db):
    from app.tests.helpers import admin_bundle_with_attributes, auth_headers

    _, _, token = admin_bundle_with_attributes(
        client,
        db,
        organization_name=f"API filter {uuid4()}",
        email=f"api-filter-{uuid4()}@example.com",
        username="admin",
    )
    payload = "' OR 1=1; DROP TABLE users; --"
    response = client.get(
        "/security/audit-logs",
        headers=auth_headers(token),
        params={"action": payload},
    )
    assert response.status_code == 200, response.text
    assert isinstance(response.json(), list)
    assert client.get("/health").json()["status"] == "ok"


def test_unsupported_http_method_is_rejected(client):
    assert client.get("/auth/login").status_code == 405
