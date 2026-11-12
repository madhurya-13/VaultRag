"""Load test for a running VaultRAG API using synthetic demo identities.

Required env: VAULTRAG_TENANT_ID, VAULTRAG_USER_EMAIL, VAULTRAG_USER_PASSWORD.
For tenant concurrency use Locust's --users with CSV rows whose tenant IDs and
credentials belong to separate synthetic organizations.
"""
import csv
import io
from itertools import cycle
import os
from uuid import uuid4

from locust import HttpUser, between, events, task

API_PREFIX = os.getenv("VAULTRAG_API_PREFIX", "/api").rstrip("/")
RAG_TASK_WEIGHT = max(0, int(os.getenv("VAULTRAG_RAG_WEIGHT", "2")))
UPLOAD_TASK_WEIGHT = max(0, int(os.getenv("VAULTRAG_UPLOAD_WEIGHT", "1")))


def _synthetic_users():
    path = os.getenv("VAULTRAG_USERS_CSV")
    if path:
        with open(path, newline="", encoding="utf-8") as handle:
            return list(csv.DictReader(handle))
    return [{
        "tenant_id": os.getenv("VAULTRAG_TENANT_ID", ""),
        "email": os.getenv("VAULTRAG_USER_EMAIL", ""),
        "password": os.getenv("VAULTRAG_USER_PASSWORD", ""),
        "can_upload": os.getenv("VAULTRAG_CAN_UPLOAD", "false"),
    }]


USERS = _synthetic_users()
USER_SEQUENCE = cycle(USERS)


@events.test_start.add_listener
def validate_credentials(environment, **_kwargs):
    if not USERS or any(
        not all(profile.get(field) for field in ("tenant_id", "email", "password"))
        for profile in USERS
    ):
        raise RuntimeError(
            "Provide synthetic credentials via VAULTRAG_USERS_CSV or VAULTRAG_TENANT_ID, "
            "VAULTRAG_USER_EMAIL and VAULTRAG_USER_PASSWORD."
        )


class VaultRagUser(HttpUser):
    wait_time = between(1, 3)

    def on_start(self):
        profile = next(USER_SEQUENCE)
        request_host = os.getenv("VAULTRAG_REQUEST_HOST")
        if request_host:
            self.client.headers["Host"] = request_host
        response = self.client.post(
            f"{API_PREFIX}/auth/login",
            json={
                "tenant_id": profile["tenant_id"],
                "email": profile["email"],
                "password": profile["password"],
            },
            name="auth/login",
        )
        if response.status_code != 200:
            raise RuntimeError(f"Synthetic login failed: HTTP {response.status_code}")
        self.client.headers["Authorization"] = f"Bearer {response.json()['access_token']}"
        self.can_upload = str(profile.get("can_upload", "false")).lower() == "true"

    @task(5)
    def authenticated_read(self):
        self.client.get(f"{API_PREFIX}/auth/me", name="auth/me")

    @task(3)
    def list_documents(self):
        self.client.get(f"{API_PREFIX}/documents", name="documents/list")

    @task(RAG_TASK_WEIGHT)
    def rag_question(self):
        self.client.post(
            f"{API_PREFIX}/rag/ask",
            json={"question": "Summarize the synthetic demonstration policy.", "top_k": 5},
            name="rag/ask",
        )

    @task(UPLOAD_TASK_WEIGHT)
    def upload_and_process_synthetic_pdf(self):
        """Exercise PDF extraction, chunking, embedding and persistence as an admin."""
        if not self.can_upload:
            return

        import fitz

        pdf = fitz.open()
        page = pdf.new_page()
        page.insert_text(
            (72, 72),
            "Synthetic load-test policy. Receipts and manager approval are "
            "required for a reimbursable expense. " * 8,
        )
        content = pdf.tobytes()
        pdf.close()
        suffix = uuid4().hex
        uploaded = self.client.post(
            f"{API_PREFIX}/documents/upload",
            data={
                "document_name": f"Synthetic load test {suffix}",
                "department": "FINANCE",
                "classification": "INTERNAL",
            },
            files={
                "file": (f"load-{suffix}.pdf", io.BytesIO(content), "application/pdf")
            },
            name="documents/upload [synthetic PDF]",
        )
        if uploaded.status_code != 201:
            return

        document_id = uploaded.json()["document_id"]
        self.client.post(
            f"{API_PREFIX}/documents/{document_id}/process",
            name="documents/process [synthetic PDF]",
        )
