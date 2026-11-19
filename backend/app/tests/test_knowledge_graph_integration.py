"""End-to-end knowledge graph extraction and evidence-gated visibility.

These tests need PostgreSQL, so they live outside ``app/tests/knowledge_graph/``
that package overrides the database fixtures for the pure-function unit tests.

The scenario is the Milestone 8 invariant: a tenant has two processed documents,
one the caller may read and one they may not. The real worker CLI extracts both,
and the caller's graph query, node detail and graph-backed RAG answer must never
contain an entity, relationship or quote from the document they cannot read.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import func, select

from app.models import KnowledgeEvidence
from app.tests.helpers import (
    admin_bundle_with_attributes,
    auth_headers,
    make_pdf_bytes,
    tenant_user_with_attributes,
)
from app.tests.rag_helpers import patch_rag_runtime, upload_processed_pdf

BACKEND_DIR = Path(__file__).resolve().parents[2]

AUTHORIZED_TEXT = (
    "The Rhea Leave Policy is managed by Northwind Inc. "
    "Northwind Inc. approves the Rhea Leave Policy for every permanent employee."
)
UNAUTHORIZED_TEXT = (
    "The Zephyr Bonus Policy is managed by Halcyon Holdings. "
    "Halcyon Holdings approves the Zephyr Bonus Policy for executives only."
)


def _run_graph_worker(tenant_id: str) -> subprocess.CompletedProcess:
    """Run the real ``scripts/graph_worker.py --once`` entry point."""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(BACKEND_DIR)
    return subprocess.run(
        [
            sys.executable,
            str(BACKEND_DIR / "scripts" / "graph_worker.py"),
            "--tenant-id",
            str(tenant_id),
            "--once",
        ],
        cwd=str(BACKEND_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=240,
    )


def test_graph_worker_hides_unauthorized_document_evidence(
    client, db, monkeypatch, tenant_context
):
    _, llm = patch_rag_runtime(
        monkeypatch, rag_min_similarity=0.0, rag_max_context_tokens=1000
    )
    organization, _admin, admin_token = admin_bundle_with_attributes(
        client,
        db,
        organization_name=f"Graph {uuid4()}",
        email=f"graph-{uuid4()}@example.com",
        username="graph_admin",
        department="ENGINEERING",
        clearance="5",
    )
    tenant_id = organization["tenant_id"]

    authorized = upload_processed_pdf(
        client,
        admin_token,
        pdf_bytes=make_pdf_bytes([AUTHORIZED_TEXT]),
        filename="leave.pdf",
        document_name="Leave Policy.pdf",
        department="HR",
        classification="INTERNAL",
    )
    unauthorized = upload_processed_pdf(
        client,
        admin_token,
        pdf_bytes=make_pdf_bytes([UNAUTHORIZED_TEXT]),
        filename="bonus.pdf",
        document_name="Executive Bonus.pdf",
        department="FINANCE",
        classification="RESTRICTED",
        required_clearance_level=4,
    )

    # HR employee: same department as the leave document, but no grant on the
    # restricted finance document and clearance below its requirement.
    _, hr_token = tenant_user_with_attributes(
        client,
        admin_token,
        tenant_id,
        email=f"graph-hr-{uuid4()}@example.com",
        username="graph_hr",
        role_names=["HR"],
        department="HR",
        clearance="3",
    )

    worker = _run_graph_worker(tenant_id)
    assert worker.returncode == 0, worker.stderr
    assert "Drained" in worker.stderr, worker.stderr

    # Extraction really covered both documents: the hidden document is hidden by
    # authorization, not because it was never processed.
    tenant_context(db, UUID(tenant_id))
    counts = dict(
        db.execute(
            select(KnowledgeEvidence.document_id, func.count()).where(
                KnowledgeEvidence.tenant_id == UUID(tenant_id),
                KnowledgeEvidence.is_valid.is_(True),
                KnowledgeEvidence.document_id.in_(
                    [
                        UUID(authorized["document_id"]),
                        UUID(unauthorized["document_id"]),
                    ]
                ),
            )
            .group_by(KnowledgeEvidence.document_id)
        ).all()
    )
    worker_output = f"{worker.stdout}\n{worker.stderr}"
    assert counts.get(UUID(authorized["document_id"]), 0) > 0, worker_output
    assert counts.get(UUID(unauthorized["document_id"]), 0) > 0, worker_output

    # The administrator (tenant-wide capability, clearance 5) sees both graphs.
    admin_response = client.post(
        "/knowledge-graph/query",
        headers=auth_headers(admin_token),
        json={"question": "Policy", "max_depth": 2},
    )
    assert admin_response.status_code == 200, admin_response.text
    admin_body = admin_response.json()
    admin_text = json.dumps(admin_body)
    assert "Rhea" in admin_text
    assert "Zephyr" in admin_text

    def node_id_containing(body: dict, name: str) -> str:
        for node in body["nodes"]:
            if name in node["name"]:
                return node["node_id"]
        raise AssertionError(f"no visible node mentions {name}")

    rhea_node_id = node_id_containing(admin_body, "Rhea")
    zephyr_node_id = node_id_containing(admin_body, "Zephyr")

    # The HR caller's graph contains the authorized document only.
    hr_response = client.post(
        "/knowledge-graph/query",
        headers=auth_headers(hr_token),
        json={"question": "Policy", "max_depth": 2},
    )
    assert hr_response.status_code == 200, hr_response.text
    hr_body = hr_response.json()
    hr_text = json.dumps(hr_body)

    assert "Rhea" in hr_text
    assert "Zephyr" not in hr_text
    assert "Halcyon" not in hr_text
    assert hr_body["denied_documents"] >= 1
    visible_document_ids = {
        evidence["document_id"]
        for node in hr_body["nodes"]
        for evidence in node["evidence"]
    }
    assert visible_document_ids == {authorized["document_id"]}

    # Direct node access fails closed with the same 404 as a missing node.
    assert (
        client.get(
            f"/knowledge-graph/nodes/{rhea_node_id}", headers=auth_headers(hr_token)
        ).status_code
        == 200
    )
    assert (
        client.get(
            f"/knowledge-graph/nodes/{zephyr_node_id}", headers=auth_headers(hr_token)
        ).status_code
        == 404
    )

    # A graph-backed answer is built from, and cites, the authorized document.
    answer = client.post(
        "/rag/ask",
        headers=auth_headers(hr_token),
        json={"question": "Rhea Leave Policy", "search_mode": "graph", "top_k": 10},
    )
    assert answer.status_code == 200, answer.text
    answer_body = answer.json()
    assert answer_body["sources"]
    assert {source["document_id"] for source in answer_body["sources"]} == {
        authorized["document_id"]
    }
    assert "Zephyr" not in json.dumps(answer_body)
    assert llm.calls, "the graph answer never reached the language model"
    assert "Zephyr" not in llm.calls[0].context
    assert "Halcyon" not in llm.calls[0].context
