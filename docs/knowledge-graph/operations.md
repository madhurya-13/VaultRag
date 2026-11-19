# Knowledge Graph Operations

## Configuration

All settings live in `backend/app/core/config.py` and are environment-driven.

| Setting | Default | Meaning |
| --- | --- | --- |
| `KNOWLEDGE_GRAPH_ENABLED` | `true` | Master switch for extraction, query and API |
| `KNOWLEDGE_GRAPH_EXTRACTION_METHOD` | `RULES` | `RULES`, `RULES+SPACY`, `RULES+SPACY+LLM` |
| `KNOWLEDGE_GRAPH_SPACY_MODEL` | `en_core_web_sm` | Model loaded lazily when method includes spaCy |
| `KNOWLEDGE_GRAPH_LLM_ENABLED` | `false` | Enables the Ollama extractor |
| `KNOWLEDGE_GRAPH_MIN_CONFIDENCE` | `0.55` | Drops low-confidence claims |
| `KNOWLEDGE_GRAPH_AUTO_MERGE_THRESHOLD` | `0.95` | Fuzzy auto-merge floor (non-PERSON only) |
| `KNOWLEDGE_GRAPH_REVIEW_THRESHOLD` | `0.82` | Fuzzy review floor |
| `KNOWLEDGE_GRAPH_DEFAULT_DEPTH` / `_MAX_DEPTH` | `2` / `3` | Traversal depth |
| `KNOWLEDGE_GRAPH_MAX_NODES` / `_MAX_EDGES` | `200` / `400` | Traversal budgets |
| `KNOWLEDGE_GRAPH_MAX_EVIDENCE` | `100` | Evidence rows per lookup |
| `KNOWLEDGE_GRAPH_MAX_SEEDS` | `8` | Seed cap |
| `KNOWLEDGE_GRAPH_FINDINGS` | `6` | Findings returned per query |
| `KNOWLEDGE_GRAPH_WORKER_POLL_SECONDS` | `2` | Idle poll interval |
| `KNOWLEDGE_GRAPH_JOB_MAX_ATTEMPTS` | `3` | Retry budget |
| `KNOWLEDGE_GRAPH_JOB_STALE_SECONDS` | `900` | Stale `PROCESSING` reclaim window |
| `KNOWLEDGE_GRAPH_ANALYTICS_ENABLED` | `true` | Query metrics |

The relation between thresholds is validated at startup: the review threshold
must be at or below the auto-merge threshold, and default depth must not exceed
max depth.

## Migration

```bash
cd backend
alembic upgrade head
```

Revision `c8f1d2e3a4b5` creates the nine graph tables, their RLS policies, the
`(tenant_id, document_id, chunk_id)` uniqueness required by the evidence foreign
key, and extends the `retrieval_analytics.mode` check to include `graph` and
`graph_hybrid`.

Downgrade removes only the Milestone 8 objects; M1–M7 data is untouched.

## Worker

Graph extraction runs outside the API process.

```bash
cd backend
python scripts/graph_worker.py --tenant-id <tenant-uuid>
```

- The worker is **per tenant** and sets the RLS tenant context on every
  transaction; it never uses a BYPASSRLS role.
- `--once` drains the queue and exits (useful for tests and backfills).
- It handles `SIGINT`/`SIGTERM`, finishing the current job before exiting.
- Run one worker per tenant under a supervisor.

To backfill an existing corpus, queue jobs through the API:

```
POST /knowledge-graph/documents/{document_id}/reprocess
```

or insert `knowledge_extraction_jobs` rows with `status = 'PENDING'`.

## Monitoring

`GET /knowledge-graph/analytics?window_days=30` (ADMIN) returns per-mode query
counts, average traversal and total latency, denied-document counts and retrieval
errors. No question text, entity names, quotes or document names are stored in
analytics.

Useful log lines:

- `KG stage=retrieval seeds=... nodes=... edges=... denied=... truncated=...`
- `KG extraction document_id=... chunks=... nodes=... edges=... evidence=...`
- `KG stage=conflicts detected=... recorded=...`
- `Graph evidence denied policy=... document_id=...`

## Troubleshooting

**`The knowledge graph is not enabled`** — `KNOWLEDGE_GRAPH_ENABLED` is false.

**Graph queries return no nodes** — the caller has no authorized evidence for
any matching entity. Check `Graph evidence denied` log lines and confirm the
source documents are `COMPLETED` and readable.

**Graph mode returns no chunks, `graph_hybrid` does** — graph-only mode uses
only evidence chunks. If the evidence quotes came from chunks whose document
model label differs from the configured embedding model, the shared candidate
predicate excludes them.

**Jobs stay PENDING** — no worker is running for that tenant, or the tenant ID
given to the worker is wrong (RLS makes another tenant's jobs invisible).

**Jobs repeatedly fail with `GRAPH_EXTRACTION_FAILED`** — the stage is in the
job's `error_message`. The document itself is unaffected and remains retrievable.

**spaCy is installed but returns nothing extra** — the model is missing; the
pipeline logs `spaCy model ... unavailable` and runs rules only. This is
expected, not an error.
