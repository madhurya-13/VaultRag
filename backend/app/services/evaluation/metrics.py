"""Deterministic retrieval and answer metrics.

Every function here is pure and unit-tested with hand-computed values. The
answer metrics are deliberately transparent:

* ``answer_token_f1`` compares against a labeled reference answer; it is an
  overlap score, not a semantic correctness judgment.
* ``faithfulness_overlap`` and ``hallucination_numeric_rate`` are lexical
  indicators. They are not factuality guarantees.
* Model-based judging, when enabled, is stored separately and labeled.

No metric is presented as a calibrated probability.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Sequence
from typing import Any

DEFAULT_K_VALUES = (3, 5, 10)
SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
CITATION_MARKER = re.compile(r"\[(S\d+)\]")
NUMBER_TOKEN = re.compile(r"\b\d+(?:[.,]\d+)?\b")
WORD = re.compile(r"[a-z0-9]+")


def recall_at_k(gold: Iterable[str], retrieved: Sequence[str], k: int) -> float:
    gold_set = set(gold)
    ranked = list(retrieved[:k])
    if not gold_set:
        return 1.0 if not ranked else 0.0
    return len(gold_set & set(ranked)) / len(gold_set)


def precision_at_k(gold: Iterable[str], retrieved: Sequence[str], k: int) -> float:
    gold_set = set(gold)
    ranked = list(retrieved[:k])
    if not ranked:
        return 1.0 if not gold_set else 0.0
    return len(gold_set & set(ranked)) / len(ranked)


def mean_reciprocal_rank(gold: Iterable[str], retrieved: Sequence[str]) -> float:
    gold_set = set(gold)
    if not gold_set:
        return 1.0 if not retrieved else 0.0
    for rank, value in enumerate(retrieved, start=1):
        if value in gold_set:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(gold: Iterable[str], retrieved: Sequence[str], k: int) -> float:
    gold_set = set(gold)
    ranked = list(retrieved[:k])
    if not gold_set:
        return 1.0 if not ranked else 0.0
    dcg = sum(
        1.0 / math.log2(rank + 1)
        for rank, value in enumerate(ranked, start=1)
        if value in gold_set
    )
    ideal = sum(
        1.0 / math.log2(rank + 1)
        for rank in range(1, min(len(gold_set), k) + 1)
    )
    return dcg / ideal if ideal else 0.0


def retrieval_metrics(
    *,
    gold_chunk_ids: Sequence[str],
    gold_document_ids: Sequence[str],
    retrieved_chunk_ids: Sequence[str],
    retrieved_document_ids: Sequence[str],
    k_values: Sequence[int] = DEFAULT_K_VALUES,
) -> dict[str, Any]:
    """Retrieval metrics at each configured K.

    ``recall_at_k``/``precision_at_k``/``ndcg_at_k`` use gold chunks.
    ``document_recall_at_k`` uses gold documents, which matters when a document
    is represented by several chunks and citations are document-level.
    """
    metrics: dict[str, Any] = {
        "retrieved_chunks": len(retrieved_chunk_ids),
        "retrieved_documents": len(retrieved_document_ids),
        "mrr": round(mean_reciprocal_rank(gold_chunk_ids, retrieved_chunk_ids), 4),
        "document_mrr": round(
            mean_reciprocal_rank(gold_document_ids, retrieved_document_ids), 4
        ),
        "retrieval_success": (
            bool(set(gold_chunk_ids) & set(retrieved_chunk_ids))
            if gold_chunk_ids
            else not retrieved_chunk_ids
        ),
        "relevant_evidence_rate": (
            len(set(gold_document_ids) & set(retrieved_document_ids))
            / len(set(gold_document_ids))
            if gold_document_ids
            else (1.0 if not retrieved_document_ids else 0.0)
        ),
    }
    for k in sorted(set(max(1, int(value)) for value in k_values)):
        metrics[f"recall_at_{k}"] = round(
            recall_at_k(gold_chunk_ids, retrieved_chunk_ids, k), 4
        )
        metrics[f"precision_at_{k}"] = round(
            precision_at_k(gold_chunk_ids, retrieved_chunk_ids, k), 4
        )
        metrics[f"ndcg_at_{k}"] = round(
            ndcg_at_k(gold_chunk_ids, retrieved_chunk_ids, k), 4
        )
        metrics[f"document_recall_at_{k}"] = round(
            recall_at_k(gold_document_ids, retrieved_document_ids, k), 4
        )
    return metrics


def _tokens(text: str) -> list[str]:
    return WORD.findall((text or "").lower())


def token_f1(reference: str, candidate: str) -> float:
    reference_tokens = _tokens(reference)
    candidate_tokens = _tokens(candidate)
    if not reference_tokens or not candidate_tokens:
        return 0.0
    common = 0
    remaining = list(candidate_tokens)
    for token in reference_tokens:
        if token in remaining:
            remaining.remove(token)
            common += 1
    if common == 0:
        return 0.0
    precision = common / len(candidate_tokens)
    recall = common / len(reference_tokens)
    return round(2 * precision * recall / (precision + recall), 4)


def _sentences(answer: str) -> list[str]:
    return [part for part in SENTENCE_SPLIT.split(answer.strip()) if part.strip()]


def citation_metrics(
    answer: str,
    *,
    valid_source_ids: set[str],
    answer_is_abstention: bool,
) -> dict[str, Any]:
    """Citation validity and claim coverage from the final answer text."""
    cited_ids: list[str] = []
    for match in CITATION_MARKER.finditer(answer or ""):
        cited_ids.extend(re.findall(r"S\d+", match.group(0)))
    unique_cited = list(dict.fromkeys(cited_ids))
    invalid = [value for value in unique_cited if value not in valid_source_ids]
    sentences = _sentences(answer)
    cited_sentences = [
        sentence for sentence in sentences if CITATION_MARKER.search(sentence)
    ]
    coverage = (
        len(cited_sentences) / len(sentences)
        if sentences
        else (1.0 if answer_is_abstention else 0.0)
    )
    has_any = bool(unique_cited)
    citation_correct = bool(unique_cited) and not invalid
    if answer_is_abstention:
        # An abstention must not fabricate citations.
        citation_correct = not has_any
        coverage = 1.0 if not has_any else 0.0
    return {
        "citation_count": len(unique_cited),
        "invalid_citations": invalid,
        "citation_validity": 1.0 if citation_correct else 0.0,
        "citation_coverage": round(coverage, 4),
        "unsupported_claim_rate": round(1.0 - coverage, 4),
    }


def faithfulness_overlap(answer: str, evidence_texts: Sequence[str]) -> float:
    """Share of answer tokens that appear in the cited/authorized evidence.

    This is a lexical proxy. It cannot detect a claim that reuses the right
    words in the wrong relation.
    """
    answer_tokens = set(_tokens(answer))
    if not answer_tokens:
        return 0.0
    evidence_tokens: set[str] = set()
    for text in evidence_texts:
        evidence_tokens.update(_tokens(text))
    return round(len(answer_tokens & evidence_tokens) / len(answer_tokens), 4)


def numeric_hallucination_rate(answer: str, evidence_texts: Sequence[str]) -> float:
    """Share of numeric tokens in the answer not present in the evidence."""
    numbers = set(NUMBER_TOKEN.findall(answer or ""))
    if not numbers:
        return 0.0
    evidence = " ".join(evidence_texts)
    evidence_numbers = set(NUMBER_TOKEN.findall(evidence))
    unsupported = {value for value in numbers if value not in evidence_numbers}
    return round(len(unsupported) / len(numbers), 4)


def answer_metrics(
    *,
    answer: str,
    expected_answer: str | None,
    evidence_texts: Sequence[str],
    valid_source_ids: set[str],
    answerable_expected: bool,
    abstained: bool,
) -> dict[str, Any]:
    lowered = (answer or "").strip().lower()
    abstention = abstained or lowered.startswith("i could not")
    metrics = citation_metrics(
        answer,
        valid_source_ids=valid_source_ids,
        answer_is_abstention=abstention,
    )
    metrics.update(
        {
            "answerable_expected": bool(answerable_expected),
            "predicted_answerable": not abstention,
            "answerability_correct": (not abstention) == bool(answerable_expected),
            "answer_token_f1": (
                token_f1(expected_answer or "", answer)
                if expected_answer
                else None
            ),
            "faithfulness_overlap": faithfulness_overlap(answer, evidence_texts),
            "hallucination_numeric_rate": numeric_hallucination_rate(
                answer, evidence_texts
            ),
        }
    )
    return metrics


def aggregate(rows: Sequence[dict[str, Any]], k_values: Sequence[int]) -> dict[str, Any]:
    """Average numeric metrics across cases; count boolean outcomes."""
    if not rows:
        return {"cases": 0}

    numeric_keys: set[str] = {
        "mrr",
        "document_mrr",
        "relevant_evidence_rate",
        "citation_validity",
        "citation_coverage",
        "answer_token_f1",
        "unsupported_claim_rate",
        "faithfulness_overlap",
        "hallucination_numeric_rate",
    }
    for k in k_values:
        numeric_keys.update(
            {
                f"recall_at_{k}",
                f"precision_at_{k}",
                f"ndcg_at_{k}",
                f"document_recall_at_{k}",
            }
        )

    result: dict[str, Any] = {"cases": len(rows)}
    for key in sorted(numeric_keys):
        values = [
            float(row[key])
            for row in rows
            if isinstance(row.get(key), (int, float))
        ]
        if values:
            result[key] = round(sum(values) / len(values), 4)

    result["retrieval_success_rate"] = round(
        sum(1.0 for row in rows if row.get("retrieval_success")) / len(rows), 4
    )
    result["answerability_accuracy"] = round(
        sum(1.0 for row in rows if row.get("answerability_correct")) / len(rows), 4
    )
    result["cases_passed"] = sum(1 for row in rows if row.get("passed"))
    result["cases_failed"] = len(rows) - result["cases_passed"]
    result["authorization_violations"] = sum(
        int(row.get("authorization_violations", 0)) for row in rows
    )
    latencies = [int(row.get("latency_ms", 0)) for row in rows]
    if latencies:
        ordered = sorted(latencies)
        result["average_latency_ms"] = round(sum(ordered) / len(ordered), 2)
        result["p95_latency_ms"] = float(
            ordered[min(len(ordered) - 1, math.ceil(len(ordered) * 0.95) - 1)]
        )
        result["error_rate"] = round(
            sum(1 for row in rows if row.get("error_code")) / len(rows), 4
        )
    return result
