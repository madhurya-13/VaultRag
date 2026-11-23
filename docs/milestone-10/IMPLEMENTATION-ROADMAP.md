# Milestone 10 — Implementation Roadmap

Companion documents: [PHASE-1-AUDIT.md](PHASE-1-AUDIT.md) · [ACCEPTANCE-CRITERIA.md](ACCEPTANCE-CRITERIA.md)

This roadmap is based on the audited repository at commit `5b2d127`, not on assumptions. Phases are executed **one at a time with explicit approval**. Each phase ends with the Step 1–7 report format (explain, implement, test, verify, document, report, pause).

Effort is expressed in focused engineer-days for a developer already familiar with the codebase. "Complexity" is S/M/L. Estimates are planning ranges, not measured durations.

---

## 0. Principles that apply to every phase

1. Reuse existing services: `authorization.py`, `retrieval_authorization.py`, `rag_service.answer_question`, `graph.*`, `integrations.*`, `security_audit.py`.
2. No new authorization bypass. Every new read/write is tenant-scoped by `AuthenticatedContext` + RLS.
3. No raw chunk text in new tables unless absolutely required (and never in feedback/evaluation/gap tables).
4. New tables require: tenant-safe FK, indexes, `ENABLE`/`FORCE` RLS, `tenant_isolation` policy, and explicit grants to `vaultrag_app`.
5. New admin endpoints require an explicit capability; new employee endpoints require only authentication plus existing document authorization.
6. New frontend pages use the existing component system, TanStack Query, Recharts and the `api` client; no new UI framework.
7. New metrics are computed from persisted rows; no hardcoded dashboard values.
8. Model-based evaluation is optional and clearly labeled; deterministic metrics are primary.
9. Every phase updates the relevant docs and adds tests that run in CI.
10. Do not claim an unexecuted check. Report blocked environment checks explicitly.

### New capability vocabulary (planned)

| Capability | Purpose | Holder |
|---|---|---|
| `VIEW_FEEDBACK` | Read/review answer feedback | ADMIN |
| `MANAGE_KNOWLEDGE_GAPS` | Review/assign/resolve gaps | ADMIN |
| `MANAGE_EVALUATION` | Run and inspect evaluations | ADMIN |
| `VIEW_ANALYTICS` | Unified analytics dashboard/API | ADMIN |
| `MANAGE_TENANT_SETTINGS` | Retrieval/tenant configuration | ADMIN |

Existing `VIEW_AUDIT_LOGS` remains the security-dashboard capability. Frontend capability mirror must be updated in the same phase as the backend.

### Migration strategy

- Prefer **one migration per phase** with a descriptive revision id, or a single Milestone 10 migration if phases 2–4/7 are approved back-to-back. Default: one per phase to keep rollback simple.
- Each migration must repeat the established pattern: create tables, indexes, enable/force RLS, create policy, grant runtime privileges, and (where needed) seed defaults.
- `alembic check` and `alembic upgrade head` on a clean test database are mandatory in each phase.
- Add `alembic check` and a fresh-migration job to CI in Phase 9.

---

## Phase 2 — RAG evaluation and benchmarking framework

**Objective:** make evaluation a first-class, reproducible product workflow that measures the real permission-aware pipeline (not a lexical proxy) with persisted runs, comparisons, APIs and a dashboard.

**Reuse:**
- `answer_question` for end-to-end runs and `retrieve_*` / `filter_authorized_chunks` for retrieval-only runs.
- Existing `rag_eval_harness` metric functions (recall/precision/MRR/NDCG) — extend, do not replace.
- `AuthenticatedContext`, `admin_bundle` test fixture, fake embedding service, mock LLM.
- existing Recharts + card/table primitives.

**Data model (new migration):**
- `evaluation_datasets` (tenant_id, name, version, description, query_count, created_by, created_at).
- `evaluation_cases` (dataset_id, category, question, expected_answer, expected_answerable, gold_document_ids, gold_chunk_ids, expected_citations, allowed_document_ids, filters JSONB, notes).
- `evaluation_runs` (run_id, tenant_id, dataset_id, config JSONB, status, metrics JSONB, started/completed, created_by, error_code).
- `evaluation_results` (run_id, case_id, retrieval_metrics JSONB, answer_metrics JSONB, citations JSONB, latency_ms, error_code, passed).
- RLS + grants (SELECT/INSERT; UPDATE only where status transitions are required).

**Backend:**
- `app/services/evaluation/metrics.py` — configurable K, recall/precision/MRR/NDCG, success rate, relevant-evidence rate, answerability accuracy, citation coverage/validity, unsupported-claim proxy.
- `app/services/evaluation/dataset.py` — JSONL import/export, validation (gold chunks belong to allowed documents, no cross-tenant references), versioning.
- `app/services/evaluation/runner.py` — sync runner for tests/CLI, async job for API using the existing worker pattern; runs each configuration through the real pipeline with a per-case authenticated context; isolates failures per case.
- `app/services/evaluation/judge.py` — **optional** Ollama judge with stored prompt version, model name and deterministic fallback; never required for a run to succeed.
- `app/services/evaluation/comparison.py` — configurations: semantic, keyword, hybrid, hybrid+rerank, hybrid+graph, hybrid+graph+rerank. Authorization always on.
- `app/services/evaluation/datasets/` + generator/CLI: synthetic corpus covering all required categories (single/multi-doc, comparison, numeric, date, contradiction, graph, insufficient, unanswerable, restricted, metadata filter, versions, permission change, citation).
- `app/routes/evaluation.py` + `app/schemas/evaluation.py` — ADMIN endpoints: run, list/get runs, results, failures, datasets, metrics, compare; strict models; tenant-derived only.
- `app/scripts/evaluation_worker.py` (or extend an existing worker pattern) for async runs.

**Frontend:**
- `src/pages/evaluation-page.tsx` + route/nav behind `MANAGE_EVALUATION`.
- Run list, run detail, configuration selector, metric cards, comparison table/chart, per-case failure inspector with authorized citations.
- Dataset list/version display.

**Tests:**
- Metric unit tests at K=3/5/10.
- Dataset validation tests (allowed-document invariant, tenant isolation, required categories).
- Runner integration test with fake embeddings + recording LLM: authorization bypass impossible; restricted chunk never retrieved; insufficient-evidence cases abstain.
- API tests: ADMIN required, other roles 403, tenant isolation, run persistence, failure isolation.
- Comparison test: each configuration is labeled and reproducible.
- Deterministic tests must not require Ollama; judge tests skip unless enabled.

**Dependencies/risks:** run time on real embeddings; LLM judge nondeterminism; risk of an eval-only authorization bypass (forbidden); RLS grants.

**Complexity/effort:** L · 6–8 days (excluding large dataset authoring, +1–2 days).

**Exit criteria:** a run executes through the real pipeline for two tenants; metrics persist; results are inspectable; authorization violations are reported and must be zero for authorized configs; dashboard shows measured values only.

---

## Phase 3 — User feedback and answer-quality improvement

**Objective:** capture per-answer feedback with review workflow, without changing permissions or training models.

**Reuse:** conversation messages (`message_id`, conversation ownership), citation re-authorization, audit log, admin query patterns.

**Data model:** `answer_feedback` (feedback_id, tenant_id, user_id, conversation_id, message_id, category, sentiment, comment, created_at, updated_at, review_status, review_notes, reviewer_id, reviewed_at, unique active feedback per user/message). Indexes on `(tenant_id, created_at)`, `(tenant_id, review_status)`, `(message_id)`.

**Backend:**
- `app/models/feedback.py`, service with create/update/retract (owner only, message ownership verified), list/summary (ADMIN), review (ADMIN, capability `VIEW_FEEDBACK`).
- Review payload must include only citations re-authorized for the reviewer; never chunk text.
- Audit feedback submission, update, retract and review.
- Routes: `POST /feedback`, `PATCH /feedback/{id}`, `DELETE /feedback/{id}` (retract), `GET /feedback/mine`, `GET /admin/feedback`, `GET /admin/feedback/summary`, `POST /admin/feedback/{id}/review`.

**Frontend:**
- Compact thumbs up/down + "report a problem" popover on assistant messages in `chat-page.tsx`; category list; optional comment; edit/retract; toast confirmation.
- `admin/feedback` review page: summary cards, category/status filters, detail dialog with question, answer, authorized citations, review notes and categorization.

**Tests:** ownership and tenant isolation; duplicate prevention; edit/retract; admin-only review; reviewer cannot see unauthorized citations; feedback does not alter document permissions.

**Complexity/effort:** M · 4–5 days.

---

## Phase 4 — Knowledge-gap analysis and unanswered-question intelligence

**Objective:** turn repeated insufficient-evidence and denied/absent knowledge into a tenant-scoped, privacy-preserving gap workflow.

**Reuse:** `retrieval_analytics` rows (numeric, no query text), `answer_question` for re-evaluation, graph/gap clustering with local embeddings, audit log.

**Data model:** `knowledge_gap_signals` (signal per query event: tenant, normalized fingerprint, topic key, embedding vector or hash, classification, evidence counts, created_at, expiring), `knowledge_gaps` (aggregate: tenant, topic, category, severity, status, occurrence count, first/last seen, assigned_to, resolved_at, resolution_document_id, resolution_verified), `knowledge_gap_events` (assignment/status/resolution history).

**Classification:** missing knowledge, poor retrieval, insufficient permissions (**excluded from gap count**), outdated knowledge, conflicting knowledge, unanswerable. Use evidence counts + permission-denied signal; never classify a denial as a content gap.

**Privacy:** store only normalized/redacted question fingerprints and locally computed embeddings; enforce minimum group size before surfacing; admin-only; no raw conversation text; retention window.

**Backend:** `app/services/knowledge_gaps/{signals,clustering,lifecycle,resolution}.py`; routes `GET /admin/knowledge-gaps`, detail, assign, resolve, re-evaluate; resolution re-runs the original (stored normalized) question through `answer_question` and records whether evidence improved.

**Frontend:** `admin/knowledge-gaps` dashboard with clusters, trends, severity, status filters, assignment and resolution; empty/loading/error states.

**Tests:** permission-denied exclusion, tenant isolation, clustering stability, minimum group size, resolution verification with fake embeddings/LLM, retention.

**Complexity/effort:** L · 5–6 days.

---

## Phase 5 — Explainability and evidence transparency

**Objective:** extend the existing response payload and chat evidence panel with version, verification status, retrieval explanation and graph contribution — without exposing prompts, internal reasoning or unauthorized evidence.

**Reuse:** `ContextSource`, `RAGSource`, evidence assessment, conflicts, graph result, timings.

**Backend:**
- Extend `RAGSource` / context source with `document_version_id`, `version_number`, `citation_verified`, `retrieval_explanation` (mode, semantic/keyword/graph contribution flags, rerank flag, filters applied, authorized candidate count, evidence rationale).
- Thread version metadata from `documents`/`document_versions` only for authorized documents.
- Include a machine-readable trace object built from measured counters; no chain-of-thought.
- Persist citation verification status in conversation citations for later history display.

**Frontend:** expandable "Why this answer" panel per assistant message: sources with version/page, verification badges, retrieval mode/contribution, evidence rationale, conflict cards. Ensure unauthorized documents never render.

**Tests:** payload contract, version fields correct after rollback, citation verification status, no prompt/secret fields, unauthorized citation filtered, conflict sources authorized.

**Complexity/effort:** M · 3–4 days.

---

## Phase 6 — Advanced enterprise analytics

**Objective:** one tenant-scoped analytics API and dashboard composed from existing data, with no fabricated values.

**Reuse:** `security_audit_logs`, `retrieval_analytics`, `knowledge_query_analytics`, documents, ingestion jobs, integrations/sync jobs, documents/versions, feedback (Phase 3), gaps (Phase 4), evaluation runs (Phase 2).

**Backend:** `app/services/analytics.py` aggregators + `app/routes/analytics.py` with ADMIN capability `VIEW_ANALYTICS`:
`GET /analytics/overview`, `/usage`, `/documents`, `/retrieval`, `/integrations`, `/feedback`, `/knowledge-gaps`, `/security`. Shared date-range parser (bounded), tenant scoping, content-free aggregates; document-level analytics must pass through current document authorization for the caller.

**Frontend:** `admin/analytics` page with summary cards, trend charts, document/integration/graph/feedback/gap/security sections, date filter; link to existing security page instead of duplicating raw logs.

**Tests:** tenant isolation per endpoint, date filtering, seed-data assertions (not snapshots of hardcoded values), unauthorized document exclusion, empty-tenant behavior.

**Complexity/effort:** L · 5–6 days.

---

## Phase 7 — Administrative controls and product configuration

**Objective:** tenant-scoped, audited, validated retrieval/product settings — without weakening security.

**Data model:** `tenant_settings` (tenant_id PK/FK, settings JSONB, version, updated_by, updated_at) + `tenant_settings_history` (append-only) or reuse the audit log for history. Only settings the system actually supports: default search mode, default top-K, rerank enable, graph enable, candidate limits, evidence thresholds, feedback enable, analytics retention, allowed file types/size, processing limits.

**Backend:** schema validation with existing Pydantic bounds and cross-field rules; merge order: safe defaults → tenant settings → request (request cannot exceed server maxima); `answer_question` reads tenant settings from a single service; audit every change; capability `MANAGE_TENANT_SETTINGS`; optional rollback to a prior version.

**Frontend:** `admin/settings` split into Organization, Retrieval, Documents, Privacy sections; server-validated errors; no technical settings visible to employees.

**Tests:** validation rejects unsafe values, settings cannot disable authorization/RLS, request cannot exceed maxima, audit record written, rollback works, tenant isolation.

**Complexity/effort:** M/L · 4–5 days.

---

## Phase 8 — UX polish, onboarding and product experience

**Objective:** consistent, accessible, role-aware product experience on top of the existing design system.

**Frontend only (plus minimal a11y markup):**
- Onboarding: role-aware welcome dialog/tour (employee vs admin), dismissible, persisted locally (no new backend requirement).
- Shared empty/error/loading components used across new and existing pages; specific empty states for feedback, gaps, evaluation, integrations, graph, conversations.
- Chat polish: version/explanation panel (Phase 5), feedback controls (Phase 3), source navigation, filters.
- Admin navigation grouping (Knowledge, Operations, Governance) without breaking routes.
- Accessibility: skip link, focus-visible, dialog labels, chart summaries/table alternatives, form error association, keyboard checks.
- Responsive check at 375/768/1280 widths; fix table overflow and nav.

**Tests:** extend Vitest for feedback interactions, onboarding visibility by role, empty/error states, accessibility attributes; Playwright smoke for new routes.

**Complexity/effort:** M · 4–5 days.

---

## Phase 9 — Observability, reliability and deployment hardening

**Objective:** make the stack operable and reproducible.

**Backend/ops:**
- Request-duration middleware + structured fields (method, path, status, duration, request_id) with no content.
- `/health/live` (process) and `/health/ready` (DB, pgvector, FTS, embedding config, Ollama optional); keep `/health` backward compatible; no secrets.
- Stale ingestion/job recovery and bounded retries/timeouts; graceful shutdown already exists for workers — document and test.
- Compose: add `graph-worker` and `integration-worker` services with restart policies, health checks, resource limits, no host ports; ensure they wait for migrations.
- Backup/restore scripts under `scripts/` (PowerShell + documented bash equivalent) for `pg_dump`/`pg_restore` + document volume; verification query; execute restore if Docker becomes available, otherwise report blocked.
- CI: `alembic check`, fresh-DB migration, Docker build (or reuse container-scan), keep security scans; no auto-deploy.
- Security scans executed locally where possible (bandit, pip-audit, npm audit, gitleaks if installed); report results honestly.

**Tests:** readiness/liveness tests, middleware log-shape test, worker startup smoke, compose config validation (`docker compose config` — requires Docker), backup/restore verification.

**Complexity/effort:** M/L · 4–5 days (backup restore execution depends on Docker).

---

## Phase 10 — Final end-to-end validation

**Objective:** prove the complete product across two synthetic tenants.

**Work:**
- Seed script for two tenants, admins/employees, synthetic PDFs, permissions, graph, versions, integrations (local connector), feedback, gaps and an evaluation dataset/run.
- Backend E2E suite covering auth, isolation, permissions (including revocation), documents, RAG modes, graph, integrations, versions, evaluation, analytics, feedback and gaps.
- Playwright E2E across login, chat, citations, feedback, documents, graph, integrations, versions, evaluation, analytics, settings.
- Run full suites and record exact results; distinguish mocked providers from live.
- Produce `docs/milestone-10/FINAL-VALIDATION-REPORT.md`.

**Complexity/effort:** L · 4–6 days.

---

## Phase 11 — Documentation and portfolio preparation

**Objective:** portfolio-grade documentation grounded in verified behavior.

**Files:**
- Rewrite `README.md` (product overview, problem/solution, differentiators, architecture, stack, features, security, install, Docker/Ollama, testing, evaluation, demo, limitations, license).
- Expand `docs/architecture/ARCHITECTURE.md` with Mermaid diagrams for backend/frontend/database/tenant/security/RAG/graph/integration/version/evaluation/feedback/analytics pipelines.
- Rewrite `docs/SECURITY.md` for Milestones 1–10.
- Create `docs/RAG-EVALUATION.md` with methodology, dataset, metrics, measured results, limitations, reproduction.
- Rewrite `docs/DEPLOYMENT.md` with prerequisites, env vars, workers, health, backup/restore, troubleshooting, shutdown.
- Rewrite `docs/DEMO-GUIDE.md` as a 10–15 minute synthetic demo.
- Add `docs/PORTFOLIO.md` (screenshots checklist, resume bullets from verified features, 30 interview Q&A).
- Ensure no unverified claims or fabricated numbers.

**Complexity/effort:** M · 3–4 days.

---

## Phase 12 — Final release

**Objective:** release readiness, not a deployment.

- Full verification matrix (backend, frontend, migrations, compose config, security scans, E2E).
- Repository hygiene: `.gitignore`, generated artifacts (`.local`, logs, reports), secrets scan, no private documents.
- `CHANGELOG.md` + version bump to 1.0.0; propose tag `v1.0.0` and wait for explicit approval.
- `docs/milestone-10/FINAL-RELEASE-REPORT.md` with status, verified features, exact test/security/evaluation results, limitations, risks and release verdict.

**Complexity/effort:** S/M · 1–2 days.

---

## Implementation order, dependencies and estimates

| Order | Phase | Depends on | Complexity | Effort (days) |
|---|---|---|---|---|
| 1 | 2 — Evaluation | — | L | 6–8 (+1–2 dataset) |
| 2 | 3 — Feedback | 2 (dashboard conventions) | M | 4–5 |
| 3 | 4 — Knowledge gaps | 3 (feedback categories), 2 (re-eval) | L | 5–6 |
| 4 | 5 — Explainability | — (can run parallel to 4) | M | 3–4 |
| 5 | 6 — Analytics | 2, 3, 4 | L | 5–6 |
| 6 | 7 — Settings | 6 (config affects metrics) | M/L | 4–5 |
| 7 | 8 — UX polish | 2–7 (surfaces must exist) | M | 4–5 |
| 8 | 9 — Ops hardening | workers, phases 2–4 jobs | M/L | 4–5 |
| 9 | 10 — E2E validation | all | L | 4–6 |
| 10 | 11 — Documentation | 10 | M | 3–4 |
| 11 | 12 — Release | 11 | S/M | 1–2 |

Total planning range: **43–58 focused engineer-days**. Phases 2, 4, 6 and 10 are the critical path.

Recommended approval granularity: Phase 2 alone first (largest new subsystem); then 3+5 together if desired (independent, small); then 4; then 6+7; then 8; then 9; then 10–12.

### Explicitly out of scope (unless the user asks)

- Cloud deployment, Kubernetes, managed infrastructure.
- A paid LLM/embedding provider or external evaluation SaaS.
- OCR, new document formats, S3 storage.
- New auth mechanisms (MFA, refresh tokens, SSO).
- Rebuilding existing pages or the design system.
- Model fine-tuning from feedback.

---

## Testing strategy per phase (summary)

| Layer | Approach |
|---|---|
| Unit | Pure metric/validation/clustering functions with hand-computed expectations |
| Integration (PostgreSQL) | Real DB, real RLS, fake embeddings, recording mock LLM; assert authorization invariants |
| API | FastAPI TestClient, ADMIN/non-admin/tenant-boundary matrices |
| Frontend | Vitest + Testing Library for interactions and states |
| E2E | Playwright against a running stack with two seeded tenants (skips clearly if the stack is unavailable) |
| Security | Existing suite + new attack tests for feedback/eval/gap/settings endpoints |
| Migration | Clean database `alembic upgrade head`, `alembic check`, RLS/grants assertions |
| Ops | `docker compose config`, health endpoints, backup/restore verification |

---

## Phase 1 exit state

- No application code changed.
- Three planning documents created under `docs/milestone-10/`.
- Awaiting explicit approval for Phase 2.
