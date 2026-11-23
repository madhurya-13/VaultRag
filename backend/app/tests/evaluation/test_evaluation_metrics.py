"""Deterministic evaluation metrics and dataset validation."""

import math

import pytest

from app.core.exceptions import BadRequestError
from app.services.evaluation.dataset import load_synthetic_payload, validate_payload
from app.services.evaluation.metrics import (
    aggregate,
    answer_metrics,
    citation_metrics,
    mean_reciprocal_rank,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
    retrieval_metrics,
)


def test_retrieval_metric_helpers_match_hand_computed_values():
    assert recall_at_k(["a", "b"], ["a", "c"], 2) == 0.5
    assert recall_at_k(["a", "b"], ["a", "c"], 1) == 0.5
    assert precision_at_k(["a"], ["a", "c"], 2) == 0.5
    assert precision_at_k([], [], 5) == 1.0
    assert precision_at_k([], ["a"], 5) == 0.0
    assert mean_reciprocal_rank(["b"], ["a", "b"]) == 0.5
    assert mean_reciprocal_rank(["c"], ["a", "b"]) == 0.0
    assert ndcg_at_k(["a"], ["a"], 5) == 1.0
    value = ndcg_at_k(["b"], ["a", "b"], 5)
    assert 0.0 < value < 1.0
    assert not math.isnan(ndcg_at_k([], [], 5))


def test_retrieval_metrics_are_configurable_across_k_values():
    result = retrieval_metrics(
        gold_chunk_ids=["c1", "c2"],
        gold_document_ids=["d1", "d2"],
        retrieved_chunk_ids=["c1", "x", "c2"],
        retrieved_document_ids=["d1", "d3", "d2"],
        k_values=(3, 5, 10),
    )

    assert result["recall_at_3"] == 1.0
    assert result["precision_at_3"] == pytest.approx(0.6667, abs=0.0001)
    assert result["document_recall_at_3"] == 1.0
    assert result["mrr"] == 1.0
    assert result["retrieval_success"] is True
    assert result["relevant_evidence_rate"] == 1.0
    for k in (3, 5, 10):
        assert f"recall_at_{k}" in result


def test_citation_metrics_detect_invalid_and_missing_citations():
    valid = citation_metrics(
        "Leave is 18 days [S1]. Remote work is allowed [S2].",
        valid_source_ids={"S1", "S2"},
        answer_is_abstention=False,
    )
    assert valid["citation_validity"] == 1.0
    assert valid["citation_coverage"] == 1.0

    invalid = citation_metrics(
        "Leave is 18 days [S9].",
        valid_source_ids={"S1"},
        answer_is_abstention=False,
    )
    assert invalid["citation_validity"] == 0.0
    assert invalid["invalid_citations"] == ["S9"]

    abstention = citation_metrics(
        "I could not find sufficient information in the documents you are authorized to access.",
        valid_source_ids=set(),
        answer_is_abstention=True,
    )
    assert abstention["citation_validity"] == 1.0
    assert abstention["citation_coverage"] == 1.0


def test_answer_metrics_are_transparent_overlap_proxies():
    metrics = answer_metrics(
        answer="Employees receive 18 days of annual leave [S1].",
        expected_answer="Permanent employees receive 18 days of annual leave.",
        evidence_texts=["Every permanent employee receives 18 days of annual leave each year."],
        valid_source_ids={"S1"},
        answerable_expected=True,
        abstained=False,
    )

    assert metrics["answerability_correct"] is True
    assert 0.0 < metrics["answer_token_f1"] <= 1.0
    # 5 of 8 answer tokens also occur in the evidence; overlap is lexical only.
    assert metrics["faithfulness_overlap"] == 0.625
    assert metrics["hallucination_numeric_rate"] == 0.0
    assert metrics["citation_validity"] == 1.0

    fabricated_number = answer_metrics(
        answer="Employees receive 42 days of leave [S1].",
        expected_answer=None,
        evidence_texts=["Employees receive 18 days of leave."],
        valid_source_ids={"S1"},
        answerable_expected=True,
        abstained=False,
    )
    assert fabricated_number["hallucination_numeric_rate"] == 1.0


def test_aggregate_counts_failures_and_violations():
    rows = [
        {
            "recall_at_5": 1.0,
            "mrr": 1.0,
            "retrieval_success": True,
            "answerability_correct": True,
            "passed": True,
            "authorization_violations": 0,
            "latency_ms": 10,
        },
        {
            "recall_at_5": 0.0,
            "mrr": 0.0,
            "retrieval_success": False,
            "answerability_correct": False,
            "passed": False,
            "authorization_violations": 1,
            "latency_ms": 30,
            "error_code": "CASE_FAILURE",
        },
    ]
    summary = aggregate(rows, (5,))

    assert summary["cases"] == 2
    assert summary["recall_at_5"] == 0.5
    assert summary["retrieval_success_rate"] == 0.5
    assert summary["cases_passed"] == 1
    assert summary["authorization_violations"] == 1
    assert summary["error_rate"] == 0.5


def test_bundled_dataset_validates_and_covers_required_categories():
    payload = load_synthetic_payload()
    normalized = validate_payload(payload)
    categories = {case["category"] for case in normalized["cases"]}

    assert len(normalized["documents"]) >= 8
    assert len(normalized["cases"]) >= 14
    assert {
        "single_document",
        "multi_document",
        "comparison",
        "numeric",
        "temporal",
        "conflicting",
        "graph_relationship",
        "insufficient_evidence",
        "unanswerable",
        "restricted_document",
        "metadata_filter",
        "document_version",
        "permission_change",
        "citation",
    } <= categories


def test_gold_labels_never_reference_a_document_outside_the_allow_list():
    normalized = validate_payload(load_synthetic_payload())
    for case in normalized["cases"]:
        allowed = set(case["allowed_document_names"])
        assert set(case["gold_document_names"]) <= allowed
        assert {
            item["document_name"] for item in case["gold_chunks"]
        } <= allowed


def test_dataset_validation_rejects_unknown_category_and_missing_key():
    payload = load_synthetic_payload()
    payload["cases"][0]["category"] = "not_a_category"
    with pytest.raises(BadRequestError):
        validate_payload(payload)

    payload = load_synthetic_payload()
    payload["cases"][0]["key"] = ""
    with pytest.raises(BadRequestError):
        validate_payload(payload)
