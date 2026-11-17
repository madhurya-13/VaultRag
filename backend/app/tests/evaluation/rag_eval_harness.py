"""Deterministic offline evaluation harness for retrieval strategies.

Honesty about scope
-------------------
This harness runs without PostgreSQL, pgvector, Sentence Transformers or
Ollama. It uses a deterministic hashed lexical embedding and a deterministic
reranking proxy so the ranking algorithms (semantic ordering, keyword ordering,
RRF fusion, reranking/diversity) can be compared reproducibly on a labeled
synthetic corpus.

It is **not** a substitute for the production MiniLM/pgvector benchmark. Scores
must be reported as offline ranking measurements, not as production answer
quality. No score in this module is fabricated: every number is computed from
the dataset and the algorithms below.

The harness also measures a security property: when retrieval is run *without*
the caller's document allow-list, restricted or cross-tenant chunks do appear in
the top results. That measured leak rate is why the authorization filter exists.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

DATASET_PATH = (
    Path(__file__).resolve().parents[4] / "tests" / "evaluation" / "dataset.json"
)
EMBEDDING_DIMENSION = 256
DEFAULT_K = 5
RRF_K = 60
PER_DOCUMENT_LIMIT = 2

STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "can",
        "do",
        "does",
        "for",
        "from",
        "how",
        "i",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "the",
        "to",
        "what",
        "when",
        "which",
        "who",
        "with",
    }
)


@dataclass(frozen=True, slots=True)
class EvalChunk:
    id: str
    document_id: str
    tenant: str
    document_name: str
    page: int
    text: str


def load_dataset(path: Path | None = None) -> dict:
    with (path or DATASET_PATH).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def corpus(dataset: dict) -> list[EvalChunk]:
    chunks: list[EvalChunk] = []
    for document in dataset["documents"]:
        for chunk in document["chunks"]:
            chunks.append(
                EvalChunk(
                    id=chunk["id"],
                    document_id=document["id"],
                    tenant=document["tenant"],
                    document_name=document["name"],
                    page=int(chunk["page"]),
                    text=chunk["text"],
                )
            )
    return chunks


def tokens(text: str) -> list[str]:
    cleaned = "".join(
        character.lower() if character.isalnum() else " "
        for character in text
    )
    return [
        token for token in cleaned.split() if token and token not in STOPWORDS
    ]


def normalize(text: str) -> str:
    return " ".join(text.lower().split())


def embed(text: str, dimension: int = EMBEDDING_DIMENSION) -> list[float]:
    """Deterministic hashed bag-of-words/bigram embedding."""
    vector = [0.0] * dimension
    words = tokens(text)
    for index, word in enumerate(words):
        digest = hashlib.sha256(word.encode("utf-8")).digest()
        vector[int.from_bytes(digest[:4], "big") % dimension] += 1.0
        if index + 1 < len(words):
            bigram = f"{word}_{words[index + 1]}"
            digest = hashlib.sha256(bigram.encode("utf-8")).digest()
            vector[int.from_bytes(digest[:4], "big") % dimension] += 0.5
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


def cosine(left: Sequence[float], right: Sequence[float]) -> float:
    return float(sum(a * b for a, b in zip(left, right)))


def semantic_ranking(
    query: str, chunks: Sequence[EvalChunk], limit: int = 10
) -> list[EvalChunk]:
    query_vector = embed(query)
    scored = [
        (cosine(query_vector, embed(chunk.text)), chunk) for chunk in chunks
    ]
    scored.sort(key=lambda pair: (-pair[0], pair[1].id))
    return [chunk for _score, chunk in scored[:limit]]


def keyword_score(query: str, text: str) -> float:
    query_tokens = set(tokens(query))
    text_tokens = set(tokens(text))
    if not query_tokens:
        return 0.0
    overlap = len(query_tokens & text_tokens)
    phrase = 1.0 if normalize(query) in normalize(text) else 0.0
    identifier_bonus = sum(
        1.0
        for token in query_tokens
        if any(character.isdigit() for character in token)
        and token in text_tokens
    )
    if overlap == 0 and phrase == 0.0:
        return 0.0
    return float(overlap) + 3.0 * phrase + 3.0 * identifier_bonus


def keyword_ranking(
    query: str, chunks: Sequence[EvalChunk], limit: int = 10
) -> list[EvalChunk]:
    scored = [
        (keyword_score(query, chunk.text), chunk)
        for chunk in chunks
    ]
    scored = [pair for pair in scored if pair[0] > 0]
    scored.sort(key=lambda pair: (-pair[0], pair[1].id))
    return [chunk for _score, chunk in scored[:limit]]


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[EvalChunk]],
    *,
    rrf_k: int = RRF_K,
    limit: int = 10,
) -> list[EvalChunk]:
    scores: dict[str, float] = {}
    chunks: dict[str, EvalChunk] = {}
    for ranking in rankings:
        for rank, chunk in enumerate(ranking, start=1):
            chunks.setdefault(chunk.id, chunk)
            scores[chunk.id] = scores.get(chunk.id, 0.0) + 1.0 / (rrf_k + rank)
    ordered = sorted(scores.items(), key=lambda pair: (-pair[1], pair[0]))
    return [chunks[chunk_id] for chunk_id, _score in ordered[:limit]]


def rerank_proxy(
    query: str,
    chunks: Sequence[EvalChunk],
    *,
    limit: int = DEFAULT_K,
    per_document_limit: int = PER_DOCUMENT_LIMIT,
) -> list[EvalChunk]:
    """Deterministic cross-encoder-style reranking proxy."""

    def score(chunk: EvalChunk) -> float:
        query_tokens = set(tokens(query))
        text_tokens = set(tokens(chunk.text))
        if not query_tokens:
            return 0.0
        coverage = len(query_tokens & text_tokens) / len(query_tokens)
        phrase = 1.0 if normalize(query) in normalize(chunk.text) else 0.0
        return 2.0 * coverage + 2.0 * phrase

    scored = [(score(chunk), chunk) for chunk in chunks]
    scored.sort(key=lambda pair: (-pair[0], pair[1].id))

    selected: list[EvalChunk] = []
    counts: dict[str, int] = {}
    for _score, chunk in scored:
        if counts.get(chunk.document_id, 0) >= per_document_limit:
            continue
        counts[chunk.document_id] = counts.get(chunk.document_id, 0) + 1
        selected.append(chunk)
        if len(selected) >= limit:
            break
    return selected


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


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
    for rank, chunk_id in enumerate(retrieved, start=1):
        if chunk_id in gold_set:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(gold: Iterable[str], retrieved: Sequence[str], k: int) -> float:
    gold_set = set(gold)
    ranked = list(retrieved[:k])
    if not gold_set:
        return 1.0 if not ranked else 0.0
    dcg = sum(
        1.0 / math.log2(rank + 1)
        for rank, chunk_id in enumerate(ranked, start=1)
        if chunk_id in gold_set
    )
    ideal = sum(
        1.0 / math.log2(rank + 1)
        for rank in range(1, min(len(gold_set), k) + 1)
    )
    return dcg / ideal if ideal else 0.0


def evaluate_query(
    query: dict, results: Sequence[EvalChunk], k: int = DEFAULT_K
) -> dict:
    gold = query.get("gold_chunks", [])
    ids = [chunk.id for chunk in results]
    expect_empty = bool(query.get("expect_empty"))
    required_terms = query.get("required_terms") or []

    top_text = normalize(" ".join(chunk.text for chunk in results[:2]))
    if expect_empty:
        faithfulness = 1.0 if not results else 0.0
        relevance = 1.0 if not results else 0.0
    else:
        faithfulness = (
            sum(1.0 for term in required_terms if normalize(term) in top_text)
            / len(required_terms)
            if required_terms
            else 0.0
        )
        query_tokens = set(tokens(query["question"]))
        relevance = (
            len(query_tokens & set(tokens(top_text))) / len(query_tokens)
            if query_tokens
            else 0.0
        )

    if gold:
        citation_validity = 1.0 if ids and ids[0] in set(gold) else 0.0
    else:
        citation_validity = 1.0 if not results else 0.0

    return {
        "recall_at_k": recall_at_k(gold, ids, k),
        "precision_at_k": precision_at_k(gold, ids, k),
        "mrr": mean_reciprocal_rank(gold, ids),
        "ndcg_at_k": ndcg_at_k(gold, ids, k),
        "context_precision": precision_at_k(gold, ids, len(ids) or 1),
        "context_recall": recall_at_k(gold, ids, len(ids) or 1),
        "answer_faithfulness_proxy": faithfulness,
        "answer_relevance_proxy": relevance,
        "citation_validity": citation_validity,
        "abstained_correctly": bool(expect_empty and not results),
    }


def _aggregate(rows: list[dict]) -> dict:
    if not rows:
        return {}
    keys = [
        "recall_at_k",
        "precision_at_k",
        "mrr",
        "ndcg_at_k",
        "context_precision",
        "context_recall",
        "answer_faithfulness_proxy",
        "answer_relevance_proxy",
        "citation_validity",
    ]
    aggregate = {
        key: round(sum(row[key] for row in rows) / len(rows), 4) for key in keys
    }
    aggregate["queries"] = len(rows)
    empty_rows = [row for row in rows if row.get("expects_empty")]
    aggregate["abstention_accuracy"] = (
        round(
            sum(1.0 for row in empty_rows if row["abstained_correctly"])
            / len(empty_rows),
            4,
        )
        if empty_rows
        else 0.0
    )
    return aggregate


def _method_results(
    method: str,
    query: str,
    allowed_chunks: Sequence[EvalChunk],
) -> list[EvalChunk]:
    if method == "semantic":
        return semantic_ranking(query, allowed_chunks, DEFAULT_K)
    if method == "keyword":
        return keyword_ranking(query, allowed_chunks, DEFAULT_K)
    semantic = semantic_ranking(query, allowed_chunks, 10)
    keyword = keyword_ranking(query, allowed_chunks, 10)
    fused = reciprocal_rank_fusion([semantic, keyword], limit=10)
    if method == "hybrid":
        return fused[:DEFAULT_K]
    if method == "hybrid_reranked":
        return rerank_proxy(query, fused, limit=DEFAULT_K)
    raise ValueError(f"Unknown method: {method}")


METHODS = ("semantic", "keyword", "hybrid", "hybrid_reranked")


def benchmark(dataset: dict | None = None) -> dict:
    data = dataset or load_dataset()
    chunks = corpus(data)
    queries = data["queries"]

    per_query: dict[str, list[dict]] = {method: [] for method in METHODS}
    violations = 0
    leak_queries = 0
    restricted_queries = 0

    for query in queries:
        allowed = set(query["allowed_documents"])
        allowed_chunks = [chunk for chunk in chunks if chunk.document_id in allowed]
        restricted = [chunk for chunk in chunks if chunk.document_id not in allowed]

        for method in METHODS:
            results = _method_results(method, query["question"], allowed_chunks)
            violations += sum(
                1 for chunk in results if chunk.document_id not in allowed
            )
            row = evaluate_query(query, results)
            row["query_id"] = query["id"]
            row["category"] = query["category"]
            row["expects_empty"] = bool(query.get("expect_empty"))
            per_query[method].append(row)

        if restricted:
            restricted_queries += 1
            unfiltered = semantic_ranking(query["question"], chunks, DEFAULT_K)
            if any(chunk.document_id not in allowed for chunk in unfiltered):
                leak_queries += 1

    categories: dict[str, int] = {}
    for query in queries:
        categories[query["category"]] = categories.get(query["category"], 0) + 1

    return {
        "dataset": {
            "name": data.get("name", "unnamed"),
            "documents": len(data["documents"]),
            "chunks": len(chunks),
            "queries": len(queries),
            "categories": categories,
            "k": DEFAULT_K,
            "rrf_k": RRF_K,
        },
        "methods": {
            method: _aggregate(per_query[method]) for method in METHODS
        },
        "per_query": per_query,
        "security": {
            "authorization_violations": violations,
            "restricted_queries": restricted_queries,
            "unfiltered_restricted_leak_rate": round(
                leak_queries / restricted_queries, 4
            )
            if restricted_queries
            else 0.0,
        },
    }


def render_markdown(results: dict) -> str:
    dataset = results["dataset"]
    lines = [
        "| Method | Recall@5 | Precision@5 | MRR | NDCG@5 | Context precision | Context recall | Faithfulness (proxy) | Answer relevance (proxy) | Citation validity |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    labels = {
        "semantic": "Semantic only",
        "keyword": "Keyword only",
        "hybrid": "Hybrid (RRF)",
        "hybrid_reranked": "Hybrid + reranking",
    }
    for method in METHODS:
        m = results["methods"][method]
        lines.append(
            "| {label} | {r:.3f} | {p:.3f} | {mrr:.3f} | {ndcg:.3f} | {cp:.3f} | {cr:.3f} | {f:.3f} | {ar:.3f} | {cv:.3f} |".format(
                label=labels[method],
                r=m["recall_at_k"],
                p=m["precision_at_k"],
                mrr=m["mrr"],
                ndcg=m["ndcg_at_k"],
                cp=m["context_precision"],
                cr=m["context_recall"],
                f=m["answer_faithfulness_proxy"],
                ar=m["answer_relevance_proxy"],
                cv=m["citation_validity"],
            )
        )
    lines.append("")
    lines.append(
        f"Dataset: {dataset['documents']} documents, {dataset['chunks']} chunks, "
        f"{dataset['queries']} labeled queries, K={dataset['k']}."
    )
    lines.append(
        "Security: authorization violations = "
        f"{results['security']['authorization_violations']}; unfiltered restricted "
        "leak rate = "
        f"{results['security']['unfiltered_restricted_leak_rate']:.3f}."
    )
    return "\n".join(lines)
