"""Evaluation API: dataset import, real-pipeline runs, isolation and revocation."""

from __future__ import annotations


from app.tests.helpers import (
    admin_bundle_with_attributes,
    auth_headers,
    create_admin_bundle,
    grant_document_permission,
    make_pdf_bytes,
    revoke_document_permission,
    tenant_user_with_attributes,
    upload_pdf,
)
from app.tests.rag_helpers import RecordingLLM, patch_rag_runtime, upload_processed_pdf


def _case(
    key: str,
    question: str,
    *,
    category: str = "single_document",
    allowed: list[str] | None = None,
    gold: list[str] | None = None,
    expected_answer: str | None = "Permanent employees receive 18 days of annual leave each year.",
    expected_answerable: bool = True,
    restricted: list[str] | None = None,
    filters: dict | None = None,
) -> dict:
    allowed_names = allowed if allowed is not None else ["Acme Employee Handbook"]
    gold_names = gold if gold is not None else allowed_names
    return {
        "key": key,
        "category": category,
        "question": question,
        "expected_answer": expected_answer,
        "expected_answerable": expected_answerable,
        "allowed_document_names": allowed_names,
        "gold_document_names": gold_names,
        "gold_chunks": [
            {"document_name": name, "chunk_index": 0} for name in gold_names
        ],
        "expected_citation_document_names": gold_names,
        "restricted_document_names": restricted or [],
        "filters": filters or {},
        "notes": "Synthetic test case.",
    }


def _import_dataset(client, token: str, cases: list[dict], name: str = "eval-v1") -> dict:
    response = client.post(
        "/evaluation/datasets",
        headers=auth_headers(token),
        json={
            "name": name,
            "version": 1,
            "description": "Synthetic evaluation",
            "cases": cases,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _upload_handbook(client, token: str) -> dict:
    text = (
        "Acme Employee Handbook. Every permanent employee receives 18 days of "
        "annual leave each year. Employees may work remotely for up to three "
        "days per week with manager approval."
    )
    return upload_processed_pdf(
        client,
        token,
        pdf_bytes=make_pdf_bytes([text]),
        filename="handbook.pdf",
        document_name="Acme Employee Handbook",
        department="HR",
        classification="INTERNAL",
    )


def test_dataset_import_is_admin_only_and_tenant_isolated(client, db):
    alpha, _, alpha_token = admin_bundle_with_attributes(
        client, db, organization_name="Alpha Corp", email="alpha@example.com", username="admin"
    )
    beta, _, beta_token = create_admin_bundle(
        client, db, organization_name="Beta Corp", email="beta@example.com"
    )
    _, employee_token = tenant_user_with_attributes(
        client,
        alpha_token,
        alpha["tenant_id"],
        email="employee@example.com",
        username="employee",
        role_names=["EMPLOYEE"],
        department="HR",
        clearance="1",
    )

    forbidden = client.get("/evaluation/datasets", headers=auth_headers(employee_token))
    assert forbidden.status_code == 403

    dataset = _import_dataset(client, alpha_token, [_case("leave", "How many leave days?")])
    assert dataset["query_count"] == 1
    assert dataset["source"] == "IMPORTED"
    assert dataset["cases"][0]["case_key"] == "leave"

    cross = client.get(
        f"/evaluation/datasets/{dataset['dataset_id']}",
        headers=auth_headers(beta_token),
    )
    assert cross.status_code == 404

    listed = client.get("/evaluation/datasets", headers=auth_headers(beta_token))
    assert listed.status_code == 200
    assert listed.json()["datasets"] == []

    runs = client.get("/evaluation/runs", headers=auth_headers(beta_token))
    assert runs.status_code == 200
    assert runs.json()["runs"] == []


def test_bundled_synthetic_dataset_can_be_imported_without_documents(client, db):
    _, _, token = admin_bundle_with_attributes(
        client, db, organization_name="Synthetic Corp", email="synthetic@example.com", username="admin"
    )
    response = client.post(
        "/evaluation/datasets/synthetic",
        headers=auth_headers(token),
        json={"create_documents": False},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["source"] == "SYNTHETIC"
    assert body["query_count"] >= 14
    categories = {case["category"] for case in body["cases"]}
    assert "restricted_document" in categories
    assert "permission_change" in categories


def test_synthetic_corpus_materializes_through_ingestion(client, db, monkeypatch):
    _, _, token = admin_bundle_with_attributes(
        client, db, organization_name="Corpus Corp", email="corpus@example.com", username="admin"
    )
    patch_rag_runtime(monkeypatch)

    response = client.post(
        "/evaluation/datasets/synthetic",
        headers=auth_headers(token),
        json={"create_documents": True},
    )
    assert response.status_code == 201, response.text
    assert response.json()["source"] == "SYNTHETIC"

    documents = client.get("/documents", headers=auth_headers(token)).json()
    by_name = {document["document_name"]: document for document in documents}
    assert "Acme Employee Handbook" in by_name
    assert "Acme Salary Bands" in by_name
    assert by_name["Acme Salary Bands"]["classification"] == "RESTRICTED"
    assert all(
        document["processing_status"] == "COMPLETED" for document in documents
    )


def test_retrieval_run_uses_real_pipeline_and_is_repeatable(client, db, monkeypatch):
    _, _, token = admin_bundle_with_attributes(
        client, db, organization_name="Eval Corp", email="eval@example.com", username="admin"
    )
    patch_rag_runtime(monkeypatch)
    _upload_handbook(client, token)
    dataset = _import_dataset(
        client,
        token,
        [
            _case(
                "leave-days",
                "annual leave days",
            )
        ],
    )

    response = client.post(
        "/evaluation/runs",
        headers=auth_headers(token),
        json={
            "dataset_id": dataset["dataset_id"],
            "configuration": "keyword",
            "top_k": 5,
            "answer_mode": "retrieval",
        },
    )
    assert response.status_code == 201, response.text
    run = response.json()
    assert run["status"] == "COMPLETED"
    assert run["authorization_violations"] == 0
    assert run["failed_cases"] == 0
    assert run["metrics"]["recall_at_5"] == 1.0, run
    assert run["metrics"]["reranker_implementation"] is None

    results = client.get(
        f"/evaluation/runs/{run['run_id']}/results",
        headers=auth_headers(token),
    )
    assert results.status_code == 200, results.text
    rows = results.json()["results"]
    assert len(rows) == 1
    assert rows[0]["passed"] is True
    assert rows[0]["case_key"] == "leave-days"
    assert rows[0]["retrieved_chunk_ids"]
    assert rows[0]["answer"] is None

    repeated = client.post(
        "/evaluation/runs",
        headers=auth_headers(token),
        json={
            "dataset_id": dataset["dataset_id"],
            "configuration": "keyword",
            "top_k": 5,
            "answer_mode": "retrieval",
        },
    )
    assert repeated.status_code == 201, repeated.text
    assert repeated.json()["metrics"]["recall_at_5"] == run["metrics"]["recall_at_5"]
    assert repeated.json()["metrics"]["mrr"] == run["metrics"]["mrr"]

    configs = client.get("/evaluation/configurations", headers=auth_headers(token))
    assert configs.status_code == 200
    assert len(configs.json()["configurations"]) == 6


def test_generated_run_computes_answer_and_citation_metrics(client, db, monkeypatch):
    _, _, token = admin_bundle_with_attributes(
        client, db, organization_name="Answer Corp", email="answer@example.com", username="admin"
    )
    llm = RecordingLLM(
        answer="Employees receive 18 days of annual leave each year [S1]."
    )
    patch_rag_runtime(monkeypatch, llm=llm)
    _upload_handbook(client, token)
    dataset = _import_dataset(
        client,
        token,
        [_case("leave-citation", "annual leave days employee")],
    )

    response = client.post(
        "/evaluation/runs",
        headers=auth_headers(token),
        json={
            "dataset_id": dataset["dataset_id"],
            "configuration": "keyword",
            "top_k": 5,
            "answer_mode": "generated",
        },
    )
    assert response.status_code == 201, response.text
    run = response.json()
    assert run["status"] == "COMPLETED", run
    assert run["metrics"]["citation_validity"] == 1.0
    assert run["metrics"]["citation_coverage"] == 1.0
    assert run["metrics"]["answerability_accuracy"] == 1.0
    assert run["metrics"]["answer_token_f1"] > 0.5
    assert len(llm.calls) == 1

    rows = client.get(
        f"/evaluation/runs/{run['run_id']}/results", headers=auth_headers(token)
    ).json()["results"]
    assert rows[0]["passed"] is True
    assert rows[0]["citations"][0]["document_name"] == "Acme Employee Handbook"
    assert "[S1]" in rows[0]["answer"]


def test_restricted_viewer_and_permission_revocation_are_enforced(client, db, monkeypatch):
    alpha, _, admin_token = admin_bundle_with_attributes(
        client, db, organization_name="Policy Corp", email="policy@example.com", username="admin"
    )
    patch_rag_runtime(monkeypatch)
    handbook = _upload_handbook(client, admin_token)

    salary_text = (
        "Acme Salary Bands. Synthetic restricted compensation document. Band C "
        "ranges from 75000 to 100000 demo credits. Access requires RESTRICTED clearance."
    )
    salary_upload = upload_pdf(
        client,
        admin_token,
        make_pdf_bytes([salary_text]),
        filename="salary.pdf",
        document_name="Acme Salary Bands",
        department="HR",
        classification="RESTRICTED",
        extra_form={"required_clearance_level": "4"},
    )
    assert salary_upload.status_code == 201, salary_upload.text
    salary = salary_upload.json()
    processed = client.post(
        f"/documents/{salary['document_id']}/process", headers=auth_headers(admin_token)
    )
    assert processed.status_code == 200, processed.text

    viewer, _ = tenant_user_with_attributes(
        client,
        admin_token,
        alpha["tenant_id"],
        email="viewer@example.com",
        username="viewer",
        role_names=["EMPLOYEE"],
        department="HR",
        clearance="1",
    )
    grant = grant_document_permission(
        client, admin_token, handbook["document_id"], role_name="EMPLOYEE"
    )

    dataset = _import_dataset(
        client,
        admin_token,
        [
            _case(
                "leave-visible",
                "annual leave days",
            ),
            _case(
                "salary-hidden",
                "What is the salary range for Band C?",
                category="restricted_document",
                allowed=[],
                gold=[],
                expected_answer=None,
                expected_answerable=False,
                restricted=["Acme Salary Bands"],
            ),
        ],
    )

    response = client.post(
        "/evaluation/runs",
        headers=auth_headers(admin_token),
        json={
            "dataset_id": dataset["dataset_id"],
            "configuration": "keyword",
            "top_k": 5,
            "answer_mode": "retrieval",
            "viewer_user_id": viewer["user_id"],
        },
    )
    assert response.status_code == 201, response.text
    run = response.json()
    assert run["status"] == "COMPLETED"
    assert run["authorization_violations"] == 0

    rows = client.get(
        f"/evaluation/runs/{run['run_id']}/results", headers=auth_headers(admin_token)
    ).json()["results"]
    by_key = {row["case_key"]: row for row in rows}
    assert "Acme Salary Bands" not in {
        citation["document_name"]
        for row in rows
        for citation in row["citations"]
    }
    assert str(salary["document_id"]) not in by_key["salary-hidden"]["retrieved_document_ids"]
    assert by_key["salary-hidden"]["passed"] is True
    assert by_key["leave-visible"]["passed"] is True

    revoke_document_permission(
        client, admin_token, handbook["document_id"], grant["permission_id"]
    )
    after = client.post(
        "/evaluation/runs",
        headers=auth_headers(admin_token),
        json={
            "dataset_id": dataset["dataset_id"],
            "configuration": "keyword",
            "top_k": 5,
            "answer_mode": "retrieval",
            "viewer_user_id": viewer["user_id"],
        },
    )
    assert after.status_code == 201, after.text
    after_rows = client.get(
        f"/evaluation/runs/{after.json()['run_id']}/results",
        headers=auth_headers(admin_token),
    ).json()["results"]
    after_leave = next(row for row in after_rows if row["case_key"] == "leave-visible")
    assert after_leave["retrieved_document_ids"] == []
    assert after_leave["passed"] is False
    assert after.json()["authorization_violations"] == 0


def test_run_rejects_foreign_tenant_dataset(client, db):
    _, _, alpha_token = admin_bundle_with_attributes(
        client, db, organization_name="One Corp", email="one@example.com", username="admin"
    )
    _, _, beta_token = create_admin_bundle(
        client, db, organization_name="Two Corp", email="two@example.com"
    )
    dataset = _import_dataset(client, alpha_token, [_case("leave", "How many leave days?")])

    response = client.post(
        "/evaluation/runs",
        headers=auth_headers(beta_token),
        json={"dataset_id": dataset["dataset_id"], "configuration": "keyword"},
    )
    assert response.status_code == 404
