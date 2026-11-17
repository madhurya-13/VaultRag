# Milestone 7 RAG evaluation report

Date: 2026-11-17
Scope: offline, deterministic retrieval benchmark for the Milestone 7 pipeline.
Status: **executed**; numbers below are measured from this repository. The
PostgreSQL/pgvector integration suite and production-corpus benchmark were
**not** executed because Docker/PostgreSQL was unavailable in the environment
(see [Limitations](#limitations-and-unexecuted-checks)).

## What was measured

The harness in `backend/app/tests/evaluation/rag_eval_harness.py` runs four
retrieval strategies over the labeled synthetic dataset in
`tests/evaluation/dataset.json`:

1. **Semantic only** - cosine ranking over a deterministic hashed lexical
   embedding (256 dimensions).
2. **Keyword only** - token overlap, exact-phrase bonus and identifier bonus.
3. **Hybrid (RRF)** - reciprocal rank fusion (`k = 60`) of semantic and keyword
   candidate lists.
4. **Hybrid + reranking** - RRF candidates reordered by a deterministic
   cross-encoder-style proxy, with duplicate removal and at most two chunks per
   document.

Dataset: 8 documents, 8 chunks, 20 labeled queries (2 per category), K = 5.
Documents belong to tenant A and tenant B. Each query declares the documents the
simulated caller may read, so retrieval is measured **after** the document
allow-list filter.

Categories: `simple_factual`, `exact_keyword`, `semantic`, `multi_document`,
`comparison`, `ambiguous`, `insufficient_evidence`, `conflicting_documents`,
`permission_restricted`, `cross_tenant_attack`.

All queries are answerable from at most two chunks; two queries per category
have an empty gold set and must be abstained on. No LLM is called: the LLM and
Ollama are out of scope for this benchmark.

## Measured results

Command (repository root, Windows PowerShell):

```powershell
.\.venv\Scripts\python.exe tests\evaluation\run_evaluation.py
```

| Method | Recall@5 | Precision@5 | MRR | NDCG@5 | Context precision | Context recall | Faithfulness (proxy) | Answer relevance (proxy) | Citation validity |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Semantic only | 0.800 | 0.750 | 0.775 | 0.781 | 0.750 | 0.800 | 0.800 | 0.480 | 0.750 |
| Keyword only | 0.850 | 0.825 | 0.825 | 0.832 | 0.825 | 0.850 | 0.850 | 0.580 | 0.800 |
| Hybrid (RRF) | 0.800 | 0.750 | 0.750 | 0.763 | 0.750 | 0.800 | 0.800 | 0.480 | 0.700 |
| Hybrid + reranking | 0.800 | 0.750 | 0.750 | 0.763 | 0.750 | 0.800 | 0.800 | 0.480 | 0.700 |

Per-category recall@5 for hybrid + reranking, as printed by the runner:

| Category | Recall@5 | Queries |
|---|---:|---:|
| simple_factual | 1.000 | 2 |
| exact_keyword | 1.000 | 2 |
| semantic | 1.000 | 2 |
| multi_document | 1.000 | 2 |
| comparison | 1.000 | 2 |
| ambiguous | 1.000 | 2 |
| conflicting_documents | 1.000 | 2 |
| insufficient_evidence | 0.000 | 2 |
| permission_restricted | 0.500 | 2 |
| cross_tenant_attack | 0.500 | 2 |

Security measurements:

- **Authorization violations: 0** across all four methods and all 20 queries.
  No returned chunk belonged to a document outside the caller's allow-list.
- **Unfiltered restricted leak rate: 1.000.** When the same semantic ranking is
  run over the whole corpus without the allow-list, every query that has a
  restricted document present surfaces a restricted chunk in its top five.
  This is the measured reason the authorization filter is mandatory and not an
  optional ranking hint.

## Interpretation (including negative results)

- On this small, lexically overlapping corpus, **keyword-only ranked higher than
  hybrid** (recall 0.850 vs 0.800; MRR 0.825 vs 0.750). The benchmark therefore
  **does not demonstrate a hybrid gain**. Publishing a claim that hybrid is
  better would contradict these measurements.
- The likely causes are concrete and should be stated: the semantic side is a
  hashed lexical embedding rather than MiniLM, the corpus has one chunk per
  document and only eight chunks total, and both methods see the same tokens.
  RRF can only reorder lists that are already near-identical; the keyword-only
  exact-identifier bonus (for example `INC-2026-14`) is a strong advantage on
  this dataset.
- **Reranking did not change the ranking** (0.800/0.750/0.750 identical to
  hybrid without reranking). The proxy reranker scores token coverage and exact
  phrases, the same signals used by the retriever, so it mostly preserves the
  fusion order. It is not evidence about the real cross-encoder
  (`cross-encoder/ms-marco-MiniLM-L-6-v2`), which remains disabled by default.
- `insufficient_evidence` recall is 0.000 **by construction**: the retriever
  always returns its top five, and abstention is decided downstream by
  similarity/evidence gating, which this harness does not model. The metric is
  reported as-is rather than hidden. Overall recall is therefore capped at 0.900
  (18 answerable queries out of 20); keyword-only reaches 0.850 of that cap.
- `permission_restricted` and `cross_tenant_attack` category averages are 0.500
  because each contains one answerable query (recall 1.000) and one empty-gold
  query (recall 0.000). The security result that matters is
  `authorization_violations = 0` and the measured unfiltered leak rate.
- `answer_faithfulness_proxy` is **not** a factuality score. It measures whether
  labeled required terms appear in the top two retrieved chunks. It does not
  evaluate generated text, and no LLM output was scored.
- The evidence indicator, citation coverage and conflict detection are covered
  by unit/integration tests under `backend/app/tests/rag/`, not by this
  retrieval benchmark.

## Reproduction and artifacts

| Artifact | Path |
|---|---|
| Dataset | `tests/evaluation/dataset.json` |
| Harness | `backend/app/tests/evaluation/rag_eval_harness.py` |
| Runner | `tests/evaluation/run_evaluation.py` |
| Harness tests | `backend/app/tests/evaluation/test_rag_evaluation.py` |

The harness tests assert metric correctness on hand-computed values, dataset
coverage, bounded metrics, zero authorization violations, and that unfiltered
retrieval leaks restricted chunks. They were executed directly with the project
interpreter (8/8 passed) because the repository's autouse database fixture
prevents `pytest` from running without PostgreSQL.

## Limitations and unexecuted checks

- **No database or pgvector run.** Docker was unavailable
  (`npipe:////./pipe/dockerDesktopLinuxEngine`), so the Alembic migration, RLS
  policies, full-text index, real `pgvector` retrieval and the DB-backed RAG
  tests could not execute. Treat the SQL-heavy Milestone 7 behavior as
  reviewed-by-test-source, not as executed integration evidence.
- **No MiniLM or cross-encoder measurement.** The harness uses a deterministic
  lexical embedding and a reranking proxy; it downloads no model and makes no
  claim about the production embedding quality.
- **Synthetic corpus.** Eight documents are not representative of a real
  permission-heavy corpus. Production recall, latency, RRF weight tuning and
  reranker value must be measured against ingested documents.
- **No answer-level evaluation.** Hallucination, helpfulness and abstention
  quality need a human-labeled answer set and a running Ollama model.

## What to measure next (production)

1. Run `pytest -q` with PostgreSQL, then `alembic upgrade head` and the RAG
   integration tests.
2. Ingest a representative tenant corpus and compute recall@k / MRR / NDCG for
   semantic, keyword and hybrid with MiniLM embeddings and the real
   `content_tsv` index.
3. Compare hybrid with and without the real cross-encoder, including its
   latency and the effect of `RAG_RERANK_MIN_SCORE`.
4. Measure citation coverage and insufficient-evidence rates from
   `GET /rag/analytics` over real traffic rather than from synthetic labels.
