"""Seed two synthetic tenants for a local Milestone 10 demo.

Usage (API must be running, and the embedding/LLM services must be available):

    python scripts/seed_demo.py --api http://127.0.0.1:8000

The script uses HTTP APIs for tenant data and the supported out-of-band
`bootstrap_admin` operator command for each first administrator (public signup
deliberately cannot grant ADMIN). It creates two organizations, admins and
employees, materializes the bundled synthetic corpus through normal ingestion,
grants employees READ on the imported documents, asks a few questions (creating
conversations and feedback), records repeated unanswered questions so a visible
knowledge gap can form, and executes one retrieval-only evaluation run.

It is idempotent per organization name only in the sense that a second run will
fail the duplicate registration/import; reset the demo database first.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import httpx

ADMIN_PASSWORD = "DemoPassword123!"
EMPLOYEE_PASSWORD = "DemoEmployee123!"


def _post(client, path, token=None, **kwargs):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    response = client.post(path, headers=headers, **kwargs)
    response.raise_for_status()
    return response.json()


def _login(client, tenant_id: str, email: str, password: str) -> str:
    return _post(
        client,
        "/auth/login",
        json={"tenant_id": tenant_id, "email": email, "password": password},
    )["access_token"]


def _bootstrap_admin(tenant_id: str, slug: str) -> None:
    """The first administrator is deliberately an out-of-band operator action."""
    backend_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "app.scripts.bootstrap_admin",
            "--tenant-id",
            tenant_id,
            "--username",
            f"{slug}_admin",
            "--email",
            f"admin@{slug}.example",
            "--department",
            "HR",
            "--clearance-level",
            "3",
            "--password",
            ADMIN_PASSWORD,
        ],
        cwd=backend_root,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Admin bootstrap failed for {slug} (exit {result.returncode}). "
            "Check the DATABASE settings in backend/.env."
        )


def _seed_tenant(client, name: str, slug: str) -> None:
    handle = slug.replace("-", "_")
    org = _post(
        client,
        "/organizations/register",
        json={"organization_name": name},
    )
    tenant_id = org["tenant_id"]
    _bootstrap_admin(tenant_id, handle)
    token = _login(client, tenant_id, f"admin@{slug}.example", ADMIN_PASSWORD)

    employee = _post(
        client,
        "/users",
        token,
        json={
            "email": f"employee@{slug}.example",
            "username": f"{handle}_employee",
            "password": EMPLOYEE_PASSWORD,
            "role_names": ["EMPLOYEE"],
        },
    )
    employee_token = _login(
        client, tenant_id, f"employee@{slug}.example", EMPLOYEE_PASSWORD
    )
    _post(
        client,
        f"/users/{employee['user_id']}/attributes",
        token,
        json={
            "attributes": {
                "department": "HR",
                "clearance_level": "3",
                "employment_status": "ACTIVE",
            }
        },
    )

    dataset = _post(
        client,
        "/evaluation/datasets/synthetic",
        token,
        json={"create_documents": True},
    )
    documents = client.get("/documents", headers={"Authorization": f"Bearer {token}"})
    documents.raise_for_status()
    for document in documents.json():
        client.post(
            f"/documents/{document['document_id']}/permissions",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "user_id": employee["user_id"],
                "permission_type": "READ",
            },
        )

    questions = [
        "annual leave days",
        "remote work policy",
        "expense approval limits",
    ]
    for index, question in enumerate(questions):
        conversation = _post(
            client, "/rag/conversations", employee_token, json={}
        )
        answer = _post(
            client,
            "/rag/ask",
            employee_token,
            json={
                "question": question,
                "top_k": 5,
                "conversation_id": conversation["conversation_id"],
            },
        )
        if answer.get("message_id"):
            _post(
                client,
                "/feedback",
                employee_token,
                json={
                    "message_id": answer["message_id"],
                    "sentiment": "POSITIVE" if index == 0 else "NEGATIVE",
                    "category": "MISSING_CITATION" if index == 1 else "OTHER",
                    "comment": "Seeded demo feedback.",
                },
            )

    # Repeated unanswered questions form a visible gap (minimum group size 3).
    for _ in range(3):
        _post(
            client,
            "/rag/ask",
            employee_token,
            json={"question": "lunar base relocation allowance policy", "top_k": 5},
        )

    _post(
        client,
        "/evaluation/runs",
        token,
        json={
            "dataset_id": dataset["dataset_id"],
            "configuration": "hybrid",
            "top_k": 5,
            "answer_mode": "retrieval",
        },
    )
    print(f"Seeded tenant {name}: {tenant_id}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    args = parser.parse_args()

    with httpx.Client(base_url=args.api, timeout=180.0) as client:
        for name, slug in (
            ("Acme Demo", "acme-demo"),
            ("Globex Demo", "globex-demo"),
        ):
            _seed_tenant(client, name, slug)
    print("Demo seed complete. Sign in as admin@<slug>.example with the documented password.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
