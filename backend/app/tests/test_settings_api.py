"""Phase 7: tenant settings validation, versioning, rollback and access."""

from app.tests.helpers import (
    admin_bundle_with_attributes,
    auth_headers,
    tenant_user_with_attributes,
)
from app.core.security_constants import ROLE_EMPLOYEE


def test_settings_defaults_update_history_and_rollback(client, db):
    _, _, token = admin_bundle_with_attributes(
        client, db, organization_name="Settings Corp", email="settings@example.com", username="admin"
    )
    initial = client.get("/admin/settings", headers=auth_headers(token))
    assert initial.status_code == 200
    assert initial.json()["version"] == 1
    assert initial.json()["settings"]["default_search_mode"] == "hybrid"

    updated = client.put(
        "/admin/settings",
        headers=auth_headers(token),
        json={"default_search_mode": "semantic", "default_top_k": 5, "feedback_enabled": False},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["version"] == 2
    assert updated.json()["settings"]["default_top_k"] == 5

    history = client.get("/admin/settings/history", headers=auth_headers(token))
    assert history.status_code == 200
    versions = [item["version"] for item in history.json()["history"]]
    assert versions[:2] == [2, 1]

    rolled_back = client.post(
        "/admin/settings/rollback",
        headers=auth_headers(token),
        json={"version": 1},
    )
    assert rolled_back.status_code == 200
    assert rolled_back.json()["version"] == 3
    assert rolled_back.json()["settings"]["default_search_mode"] == "hybrid"


def test_settings_reject_invalid_bounds_and_non_admin(client, db):
    org, _, admin_token = admin_bundle_with_attributes(
        client, db, organization_name="Settings Bounds", email="bounds@example.com", username="admin"
    )
    bad = client.put(
        "/admin/settings",
        headers=auth_headers(admin_token),
        json={"default_top_k": 50, "max_top_k": 5},
    )
    assert bad.status_code == 422

    bad_thresholds = client.put(
        "/admin/settings",
        headers=auth_headers(admin_token),
        json={
            "evidence_strong_threshold": 0.4,
            "evidence_moderate_threshold": 0.6,
            "evidence_limited_threshold": 0.2,
        },
    )
    assert bad_thresholds.status_code == 422

    _, employee_token = tenant_user_with_attributes(
        client,
        admin_token,
        org["tenant_id"],
        email="settings-employee@example.com",
        username="employee",
        role_names=[ROLE_EMPLOYEE],
    )
    denied = client.get("/admin/settings", headers=auth_headers(employee_token))
    assert denied.status_code == 403
