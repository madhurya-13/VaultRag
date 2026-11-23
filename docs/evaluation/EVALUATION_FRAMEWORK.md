# RAG Evaluation Framework (Milestone 10, Phase 2)

VaultRAG evaluation exists to answer one question with evidence: **does the
current retrieval and generation pipeline return the right evidence, cite it,
and refuse when it should?** It is not a tutorial benchmark and it does not
fabricate scores. Every number shown in the API and UI is computed from a
persisted run over the same permission-aware pipeline used in production.

Scores are measured on a small synthetic corpus. They are useful for
regression detection and configuration comparison; they are not a production
quality guarantee.

## Components

| Concern | Location |
|---|---|
| ORM models and status constants | `backend/app/models/evaluation.py` |
| Migration (tables, RLS, grants) | `backend/alembic/versions/e1b2c3d4f506_milestone10_evaluation.py` |
| Dataset format, resolution and synthetic corpus | `backend/app/services/evaluation/dataset.py`, `data/synthetic_enterprise_v1.json` |
| Retrieval, answer and citation metrics | `backend/app/services/evaluation/metrics.py` |
| Run orchestration | `backend/app/services/evaluation/runner.py` |
| Optional LLM judge | `backend/app/services/evaluation/judge.py` |
| Retrieval trace | `backend/app/services/rag/retrieval_trace.py` |
| API schemas and routes | `backend/app/schemas/evaluation.py`, `backend/app/routes/evaluation.py` |
| Dashboard | `frontend/src/pages/evaluation-page.tsx` |
| Tests | `backend/app/tests/evaluation/` |

## Dataset model

A dataset belongs to one tenant and has a stable `name` + `version`. Each case
stores:

- `case_key` — stable identifier inside the dataset.
- `category` — one of the 14 supported categories (see below).
- `question`, `expected_answer`, `expected_answerable`.
- `gold_document_names` and `gold_chunks` as
  `[{"document_name": ..., "chunk_index": ...}]`.
- `allowed_document_names` and `restricted_document_names` for authorization
  expectations.
- `expected_citation_document_names` and optional metadata `filters`.
- `notes` for reviewer context.

Documents are referenced **by name**, never by stored UUIDs, and chunks by
`(document_name, chunk_index)`. Ingestion IDs are resolved when the run starts.
This is deliberate: re-uploading or re-processing a document changes chunk
UUIDs but not the labeled evidence, so the dataset survives normal corpus
maintenance. Missing documents are a case-level `MISSING_DOCUMENTS` error, not
a silent skip.

Datasets are immutable once imported. Importing the same `(name, version)`
twice is a safe conflict rather than an overwrite; publish revised labels
under a new explicit `version` and keep prior versions for reproducibility.

## Categories

`single_document`, `multi_document`, `comparison`, `numeric`, `temporal`,
`conflicting`, `graph_relationship`, `insufficient_evidence`, `unanswerable`,
`restricted_document`, `metadata_filter`, `document_version`,
`permission_change`, `citation`.

The bundled corpus (`synthetic_enterprise_v1`) contains eight fictional
documents and 15 cases covering all 14 categories. It can be imported without
documents (useful for inspecting labels) or materialized through the normal
upload + extraction + chunking + embedding pipeline with
`{"create_documents": true}`. Materialization is skipped for documents that
already exist by name.

## Metrics

All retrieval metrics are computed over the ranked evidence list of the real
pipeline at K = 3, 5 and 10. `top_k` controls the retrieval depth; the trace
keeps up to 10 IDs so K=10 can be scored even when generation context is
smaller.

| Metric | Definition |
|---|---|
| Recall@K | Fraction of gold chunks present in the top K ranked chunks |
| Precision@K | Fraction of top K chunks that are gold |
| MRR | Reciprocal rank of the first relevant chunk |
| NDCG@K | Graded ranking quality with binary relevance |
| document_recall@K | Same as Recall but at document granularity |
| document_mrr | Reciprocal rank of the first gold document |
| retrieval_success | At least one gold chunk retrieved |
| relevant_evidence_rate | Share of returned evidence that is gold |
| answerability_accuracy | Predicted answerable/unanswerable agrees with the label |
| citation_validity | Share of citations that verify after re-authorization |
| citation_coverage | Share of expected citation documents that are cited |
| answer_token_f1 | Token overlap F1 against the reference answer |
| faithfulness | Transparent token-overlap proxy (not an LLM probability) |
| answer_relevance | Transparent token-overlap proxy (not an LLM probability) |
| authorization_violations | Count of gold/allowed/restricted expectation breaches |

A case passes only when its category-specific expectations hold, including
required document hits, abstention on unanswerable cases, citation checks and
zero authorization violations. `cases_failed` therefore includes both errors
and quality failures; `failed_cases` counts execution errors only.

The aggregate endpoint also reports average and p95 latency, an error rate and
the resolved `reranker_implementation`.

## Configurations

| Configuration | Search | Rerank | Graph |
|---|---|---|---|
| `semantic` | semantic | no | no |
| `keyword` | keyword | no | no |
| `hybrid` | hybrid | no | no |
| `hybrid_rerank` | hybrid | yes | no |
| `hybrid_graph` | hybrid | no | yes |
| `hybrid_graph_rerank` | hybrid | yes | yes |

Reranking uses the real cross-encoder only when `RAG_RERANK_ENABLED=true`.
Otherwise the evaluation run uses a deterministic lexical proxy and records
`reranker_implementation = "lexical_proxy"` in its metrics, so the comparison
is never presented as a cross-encoder result.

Keyword-only evaluation is sensitive to PostgreSQL `simple` full-text
semantics: all query terms must occur, without stemming. Natural-language
questions may therefore score low under `keyword`. That is a measured property
of the configuration, not a hidden fallback.

## Answer modes and the LLM judge

- `retrieval` stops after authorization, retrieval, fusion, optional reranking
  and context construction. It calls no LLM and is the cheap default.
- `generated` runs the full `answer_question` generation path and can compute
  citation and answer metrics.
- `judge_enabled` optionally asks the configured LLM for structured scores.
  Judge output is labeled `judge_*` in the metrics and never replaces the
  deterministic metrics. If the judge is unavailable the run records that
  fact; it does not invent a score.

## Security model

- Every endpoint requires the `MANAGE_EVALUATION` capability (ADMIN).
- Every query and write is tenant-scoped; RLS is enabled and forced on all four
  evaluation tables, with `tenant_isolation` policies and least-privilege
  grants.
- A run always executes with a real authenticated context. By default that is
  the calling administrator. An administrator may select a same-tenant
  `viewer_user_id` to measure what that user can retrieve; the context is
  loaded server-side and the caller cannot supply roles, attributes or a
  foreign tenant.
- No authorization shortcut exists for evaluation. If evaluation finds
  restricted evidence, that is recorded as a violation.
- Results store IDs, document names, scores and generated answer text only;
  they do not copy chunk text into the evaluation tables.

## API

All routes are prefixed with `/evaluation` and require ADMIN with
`MANAGE_EVALUATION`.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/datasets` | List tenant datasets |
| `POST` | `/datasets` | Import a JSON dataset |
| `POST` | `/datasets/synthetic` | Import the bundled corpus; optional `create_documents` |
| `GET` | `/datasets/{id}` | Dataset with its cases |
| `GET` | `/configurations` | Supported configurations |
| `POST` | `/runs` | Execute a run synchronously |
| `GET` | `/runs` | Recent runs |
| `GET` | `/runs/{id}` | Run summary |
| `GET` | `/runs/{id}/results` | Per-case results; `failures_only=true` filters |
| `GET` | `/metrics` | Dashboard aggregates and comparison |

## Running an evaluation

### API (PowerShell)

```powershell
$headers = @{ Authorization = "Bearer $env:VAULTRAG_TOKEN" }

Invoke-RestMethod -Method Post -Uri http://localhost:8000/evaluation/datasets/synthetic `
  -Headers $headers -ContentType "application/json" `
  -Body '{"create_documents": true}'

Invoke-RestMethod -Method Post -Uri http://localhost:8000/evaluation/runs `
  -Headers $headers -ContentType "application/json" `
  -Body '{"configuration": "hybrid","top_k": 5,"answer_mode": "retrieval"}'
```

`POST /evaluation/runs` runs synchronously. A 15-case retrieval-only run is
typically seconds; generated answers are bounded by the configured LLM. The
dashboard disables the button while a run is in flight.

### UI

Sign in as an administrator and open **RAG evaluation** in the sidebar. The
page offers dataset import, configuration/answer-mode selection, run history,
a configuration comparison chart and a per-case failure inspector. It is
hidden unless the signed-in user holds `MANAGE_EVALUATION`.

## Tests

```powershell
cd backend
..\.venv\Scripts\python.exe -m pytest app/tests/evaluation -q
```

Coverage includes metric correctness, dataset import/idempotency, synthetic
materialization, end-to-end retrieval and generated runs through the real
pipeline, restricted-viewer enforcement, permission revocation, tenant
isolation, repeatability and capability enforcement.

## Known limitations

- Evaluation is synchronous; there is no background worker or progress polling
  yet. Very large datasets should be split or run from a job in a later phase.
- The synthetic corpus is small and fictional. It is a regression and
  demonstration tool, not a statistically meaningful benchmark.
- `keyword` uses AND semantics with the `simple` dictionary, so natural
  language questions can score lower than hybrid or semantic configurations.
- Faithfulness and answer relevance are deterministic token-overlap proxies,
  clearly named as such. The optional LLM judge is additive and labeled.
- Graph-relationship cases require the knowledge graph to have been built for
  the corpus; otherwise those cases fail with a recorded error rather than a
  fabricated score.
