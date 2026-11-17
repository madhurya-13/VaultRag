# Retrieval analytics

Milestone 7 records one structured row per RAG query so administrators can see
retrieval volume, latency and evidence quality without reading questions,
prompts or document text.

Implementation:

- table model: `backend/app/models/retrieval_analytics.py`
- writer and aggregation: `backend/app/services/rag/retrieval_analytics.py`
- migration: `backend/alembic/versions/b7d4c9e2a1f0_milestone7_advanced_rag.py`
- API: `GET /rag/analytics` in `backend/app/routes/rag.py`

## Privacy contract

The table deliberately has **no** columns for question text, prompts, chunk
text, document names, source excerpts, tokens or JWT values. The only identity
columns are `tenant_id` and `user_id`, both required for tenant isolation and
scoped aggregation. The runtime role receives `SELECT` and `INSERT` only;
analytics rows are append-only and there is no update or delete path.

Analytics writes are best-effort and separate from the answer transaction. A
failure logs an exception and never converts a successful answer into an error.
The security audit log remains the authoritative security record.

## Schema

| Column | Type | Meaning |
|---|---|---|
| `query_id` | UUID PK | One row per RAG query |
| `tenant_id` | UUID FK | Tenant boundary; RLS enforces isolation |
| `user_id` | UUID | Caller, for scoped operational analysis |
| `search_mode` | text | `semantic`, `keyword` or `hybrid` (CHECK) |
| `candidate_count` | int | Raw candidates considered |
| `authorized_count` | int | Candidates that passed current authorization |
| `denied_count` | int | Candidates rejected by authorization |
| `retrieval_ms` | int | Retrieval stage duration |
| `rerank_ms` | int | Rerank/merge stage duration |
| `llm_ms` | int | Generation duration (0 when no LLM call) |
| `total_ms` | int | End-to-end query duration |
| `citation_valid` | bool | Answer had at least one valid backend citation |
| `evidence_level` | text | `strong`, `moderate`, `limited`, `insufficient` |
| `insufficient_evidence` | bool | Service abstained for insufficient evidence |
| `retrieval_error` | bool | A retrieval stage failed |
| `error_code` | text nullable | Safe error code, never free text |
| `created_at` | timestamptz | Insert time |

Constraints enforce the `search_mode` and `evidence_level` vocabularies and
non-negative counts/durations. Indexes cover `(tenant_id, created_at)` and
`(tenant_id, search_mode, created_at)`.

RLS: the table has `ENABLE` + `FORCE ROW LEVEL SECURITY` and the same
fail-closed `tenant_isolation` policy as other tenant tables. The API also
filters by the authenticated tenant as defence in depth.

## API

`GET /rag/analytics?days=7`

- Authentication: bearer token.
- Authorization: `VIEW_AUDIT_LOGS` capability (ADMIN by default), reusing the
  existing dependency rather than adding a new capability.
- `days` is bounded to 1-90.
- Response is tenant-scoped and aggregates only.

```json
{
  "days": 7,
  "since": "2026-11-10T09:00:00Z",
  "total_queries": 42,
  "by_search_mode": [
    { "mode": "hybrid", "count": 30 },
    { "mode": "keyword", "count": 8 },
    { "mode": "semantic", "count": 4 }
  ],
  "average_retrieval_ms": 18.4,
  "average_total_ms": 912.7,
  "p95_total_ms": 1840.0,
  "citation_validation_rate": 0.9048,
  "insufficient_evidence_rate": 0.1429,
  "retrieval_error_rate": 0.0238,
  "daily": [
    { "date": "2026-11-16T00:00:00Z", "queries": 12, "errors": 0 }
  ]
}
```

Rates are fractions in `[0, 1]`, not percentages. `p95_total_ms` uses
`percentile_cont(0.95)`; with no rows every aggregate is `0` rather than null.

Viewing analytics is itself audited as `RETRIEVAL_ANALYTICS` /
`ANALYTICS_VIEW` with only the row count and window in the reason string.

## How to interpret the metrics

- **Citation validation rate**: share of queries whose answer retained at least
  one valid backend-generated citation. It does not prove factual correctness of
  every sentence.
- **Insufficient evidence rate**: share of queries where the service abstained.
  A rising rate can mean better abstention discipline or worse retrieval; inspect
  it with the mode breakdown and retrieval error rate.
- **Retrieval error rate**: share of queries with a failed retrieval stage. It
  must normally be 0; a non-zero value indicates a real operational problem.
- **Denied count / candidate count**: a high denied share is expected for
  permission-heavy corpora and confirms that authorization filtering is doing
  work. It is not an error.
- **Evidence level distribution** is stored per row. The API currently exposes
  the insufficient rate; per-level aggregation can be added without changing the
  table.
- **Latency** is server-side. `total_ms` includes generation, so it is dominated
  by Ollama, not by retrieval.

Evidence levels are qualitative bands derived from coverage, relevance,
diversity, citation validity and conflict penalties. They are **not**
calibrated probabilities and must not be presented as confidence percentages.

## Frontend

`frontend/src/pages/security-page.tsx` adds a **Retrieval** tab backed by
`useRetrievalAnalytics(days)`. It shows total queries, citation validation,
insufficient-evidence and retrieval-error rates, a per-mode table, daily volume
and average/p95 latency. The tab is visible to ADMIN users because the backend
enforces the capability; a non-admin request receives `403`.

## Operational notes

- The dashboard window selector (7/30/90 days) applies to both security and
  retrieval tabs.
- Analytics are per tenant. A second tenant sees `total_queries = 0` for the
  same period, which is covered by a test.
- Because rows are append-only, retention requires a future maintenance job.
  No such job exists in this milestone; the migration adds indexes only.
- `RAG_ANALYTICS_ENABLED=false` stops new rows from being written but leaves the
  API and existing data intact.
