"""Evaluation framework: dataset coverage, metric correctness, security."""

import math

from app.tests.evaluation.rag_eval_harness import (
    METHODS,
    benchmark,
    load_dataset,
    mean_reciprocal_rank,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
    render_markdown,
)

REQUIRED_CATEGORIES = {
    "simple_factual",
    "exact_keyword",
    "semantic",
    "multi_document",
    "comparison",
    "ambiguous",
    "insufficient_evidence",
    "conflicting_documents",
    "permission_restricted",
    "cross_tenant_attack",
}


def test_dataset_declares_every_required_category():
    dataset = load_dataset()
    categories = {query["category"] for query in dataset["queries"]}

    assert REQUIRED_CATEGORIES <= categories
    assert len(dataset["documents"]) >= 6
    assert len(dataset["queries"]) >= len(REQUIRED_CATEGORIES)


def test_dataset_labels_never_include_a_document_outside_the_allow_list():
    dataset = load_dataset()
    chunk_to_document = {
        chunk["id"]: document["id"]
        for document in dataset["documents"]
        for chunk in document["chunks"]
    }

    for query in dataset["queries"]:
        for gold in query.get("gold_chunks", []):
            assert chunk_to_document[gold] in query["allowed_documents"]


def test_metric_helpers_match_hand_computed_values():
    assert recall_at_k(["a", "b"], ["a", "c"], 2) == 0.5
    assert precision_at_k(["a"], ["a", "c"], 2) == 0.5
    assert precision_at_k([], ["a"], 2) == 0.0
    assert recall_at_k([], [], 2) == 1.0
    assert mean_reciprocal_rank(["b"], ["a", "b"]) == 0.5
    assert mean_reciprocal_rank(["c"], ["a", "b"]) == 0.0
    assert ndcg_at_k(["a"], ["a"], 5) == 1.0
    assert 0.0 < ndcg_at_k(["b"], ["a", "b"], 5) < 1.0
    assert not math.isnan(ndcg_at_k([], [], 5))


def test_benchmark_reports_all_four_methods_with_bounded_metrics():
    results = benchmark()

    assert set(results["methods"]) == set(METHODS)
    for method in METHODS:
        metrics = results["methods"][method]
        assert metrics["queries"] == results["dataset"]["queries"]
        for key in (
            "recall_at_k",
            "precision_at_k",
            "mrr",
            "ndcg_at_k",
            "context_precision",
            "context_recall",
            "answer_faithfulness_proxy",
            "answer_relevance_proxy",
            "citation_validity",
        ):
            assert 0.0 <= metrics[key] <= 1.0
            assert not math.isnan(metrics[key])


def test_benchmark_never_returns_a_chunk_outside_the_allow_list():
    results = benchmark()

    assert results["security"]["authorization_violations"] == 0
    assert results["security"]["restricted_queries"] > 0


def test_unfiltered_retrieval_measurably_leaks_restricted_chunks():
    """Documents why authorization filtering is mandatory, not optional."""
    results = benchmark()

    assert results["security"]["unfiltered_restricted_leak_rate"] > 0.0


def test_keyword_retrieval_finds_exact_identifiers():
    results = benchmark()
    keyword_rows = {
        row["query_id"]: row for row in results["per_query"]["keyword"]
    }

    for query_id in ("q03-exact-keyword", "q04-exact-keyword"):
        assert keyword_rows[query_id]["recall_at_k"] == 1.0
        assert keyword_rows[query_id]["mrr"] == 1.0


def test_report_contains_measured_tables_not_placeholders():
    report = render_markdown(benchmark())

    assert "Semantic only" in report
    assert "Hybrid + reranking" in report
    assert "TODO" not in report
    assert "authorization violations = 0" in report
