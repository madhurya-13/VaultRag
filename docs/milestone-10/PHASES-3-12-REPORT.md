# Milestone 10 — Phases 3–12 Implementation Report

Companion documents:
[Phase 1 audit](PHASE-1-AUDIT.md) ·
[Roadmap](IMPLEMENTATION-ROADMAP.md) ·
[Acceptance criteria](ACCEPTANCE-CRITERIA.md) ·
[Evaluation framework](../evaluation/EVALUATION_FRAMEWORK.md) ·
[Final validation](FINAL-VALIDATION-REPORT.md) ·
[Release report](FINAL-RELEASE-REPORT.md)

This report describes work completed after Phase 2. It records what was built,
where, and what was actually executed. Any unrun check is labeled as such; no
benchmark or deployment claim is invented.

---

## Phase 3 — Answer feedback and review

**Objective:** capture per-answer feedback without changing permissions or
training a model.

### Implemented

- `answer_feedback` table: tenant/user/message-scoped, a partial unique index
  prevents two active feedback rows for the same user and message.
- Categories: incorrect answer, missing/wrong citation, incomplete answer,
  irrelevant sources, missing document, retrieval, permission, ingestion and
  other. Sentiment is `POSITIVE` / `NEGATIVE`.
- Feedback can be created, updated in place, listed for the owner and retracted
  (timestamped soft delete). Retraction requires no `DELETE` grant.
- Assistant-message ownership is verified server-side; another tenant's message
  returns 404.
- ADMIN review with `VIEW_FEEDBACK`: list/filter, aggregates, detail and review
  status/notes/category. The detail endpoint returns the question, answer and
  only citations currently readable by the reviewer. Chunk text is never copied.
- Every submit, update, retract and review writes a security audit event.
- A tenant-level `feedback_enabled` setting is honored server-side.
- UI: compact feedback form under completed assistant answers in the chat, and
  `/admin/feedback` review page with summary cards, filters and a detail panel.

### Tests

`backend/app/tests/test_feedback_api.py` covers owner flow, duplicate upsert,
retraction, non-admin denial, admin review and cross-tenant targeting.

---

## Phase 4 — Knowledge gaps

**Objective:** turn repeated insufficient-evidence queries into a private,
tenant-scoped gap workflow without treating permission denials as missing
content.

### Implemented

- `knowledge_gap_signals`, `knowledge_gaps`, `knowledge_gap_events`.
- Signals are recorded only on insufficient/no-evidence answers from the live
  RAG path (not from evaluation traces).
- Deterministic classification: missing knowledge, poor retrieval, insufficient
  permissions, outdated knowledge, conflicting knowledge, unanswerable.
- **Permission denials are excluded from gap counts** before a signal is stored.
- Privacy: questions are lowercased, collapsed, and emails/UUIDs/long numbers
  are redacted. Only a SHA-256 fingerprint, bounded normalized question, and
  topic keywords are stored. No user identity is stored on a signal.
- Aggregates surface only when `occurrence_count >= 3` (minimum group size).
- Lifecycle: assign to a tenant user, acknowledge/in-progress/resolve/dismiss,
  notes and append-only events.
- Re-evaluation runs the stored normalized question through the real
  `answer_question` pipeline with the administrator context and records whether
  retrieval became answerable.
- UI: `/admin/knowledge-gaps` summary, clusters, detail, status, notes and
  re-evaluation result.

### Limitations

- Clustering is deterministic fingerprint/topic grouping rather than vector
  clustering; this is the documented deterministic alternative allowed by the
  roadmap.
- Retention is enforced when listing (180 days); because the runtime role has no
  DELETE grant, old signal rows are not physically purged by the API.

### Tests

`backend/app/tests/test_analytics_gaps_api.py` covers classification,
permission-denial exclusion, redaction, topic normalization and access control.

---

## Phase 5 — Explainability and evidence transparency

### Implemented

- `RAGSource` now carries `document_version_id`, `version_number` and
  `citation_verified`.
- The response includes a bounded `explanation` object: mode, semantic/keyword/
  graph contribution, rerank usage, filters, authorized/denied candidate counts
  and a short evidence rationale. It contains no prompts, secrets or reasoning
  traces.
- Current document versions are resolved only for authorized sources and stored
  in conversation citations for later history display.
- Chat UI: expandable “Why this answer” panel plus per-source version and
  verification context.

### Verification

`backend/app/tests/test_explainability_api.py` asserts the explanation contract,
citation verification, absence of prompt/system fields and history persistence.
Version metadata is populated when a `document_versions` row exists; rollback
correctness is covered by the existing version tests, not a dedicated
explainability-rollback test.

---

## Phase 6 — Enterprise analytics

### Implemented

- `GET /analytics/{overview,usage,documents,retrieval,integrations,feedback,
  knowledge-gaps,security}` behind `VIEW_ANALYTICS`.
- Every value is computed from persisted tables; ranges are bounded to 1–365
  days. Empty tenants return zeros.
- Document analytics are aggregate-only (counts by status, classification and
  department) and never return document titles or content.
- Security analytics expose counts and action labels; raw audit detail remains
  on the existing Security page.
- UI: `/admin/analytics` with cards, query-volume area chart, security action
  chart, retrieval quality and gap classification.

### Tests

`test_analytics_gaps_api.py` exercises all sections, empty tenant behavior and
non-admin denial. Tenant isolation is enforced by every query and RLS; a
dedicated second-tenant analytics assertion is not present yet.

---

## Phase 7 — Tenant settings

### Implemented

- `tenant_settings` and append-only `tenant_settings_history`.
- Supported settings only: default search mode, default/max top K, rerank/graph
  toggles, candidate counts, minimum similarity, evidence thresholds, feedback
  toggle, analytics retention, upload size and the fixed PDF allowlist.
- Pydantic bounds and cross-field rules: top K ordering, candidate counts,
  strong > moderate > limited thresholds, PDF-only.
- Versioned updates with audit records and rollback (restore a prior version as
  a new version).
- `answer_question` merges defaults → tenant settings → request. Tenant settings
  can narrow candidates and top K, but cannot disable authorization, RLS,
  citation validation or exceed server maxima.
- UI: `/admin/settings` with retrieval defaults, feature toggles, evidence
  thresholds, save and history/rollback.

### Tests

`test_settings_api.py` covers defaults, update, history, rollback, invalid
cross-field values returning 422 and non-admin denial.

---

## Phase 8 — UX, onboarding and accessibility

### Implemented

- Role-aware, dismissible onboarding for employees and administrators, persisted
  per user in local storage and containing no admin content for employees.
- Skip-to-content link and a focus-visible target for the main region.
- New pages use the shared card/table/chart/states components and have loading,
  empty and error states.
- Navigation includes Feedback, Knowledge gaps, Analytics and Settings behind
  their capabilities.
- Charts have adjacent numeric summaries or tables. Dialog semantics come from
  the existing Radix primitives.

### Tests

`frontend/src/components/layout/onboarding.test.tsx` covers employee/admin
content and durable dismissal. Frontend lint, typecheck, unit tests and build
were executed. No browser-based responsive or screen-reader audit was performed;
no WCAG certification is claimed.

---

## Phase 9 — Observability, reliability and deployment

### Implemented

- Content-free request log line: method, path, status, duration and request ID.
- `/health/live` (process only) and `/health/ready` (database, pgvector,
  full-text column, embedding model name; Ollama marked optional).
- `/health` remains backward compatible.
- Compose services for `graph-worker` and `integration-worker` under the
  `workers` profile, with restart policy, health check, no host ports and
  migration dependency.
- PowerShell and Bash backup scripts with `pg_dump`, `pg_restore --list`
  verification and document-volume archive.
- CI adds `alembic upgrade head` + `alembic check` and `docker compose config`.

### Verification

`test_health_and_logging.py` covers liveness, readiness and the log shape.
Compose config and backup execution require Docker; the validation report states
whether they were run in this environment.

---

## Phase 10 — End-to-end validation

- Added `backend/scripts/seed_demo.py` for two synthetic tenants using HTTP APIs
  plus the out-of-band `bootstrap_admin` operator command (documents,
  permissions, conversations, feedback, gaps, evaluation).
- Added `frontend/e2e/milestone10.spec.ts` covering new admin routes and
  employee route denial.
- The existing Playwright setup depends on externally seeded credentials
  (`E2E_*`). The new spec was not executed in this session; see the validation
  report for the exact blocker.
- Two-tenant backend coverage exists across the API test suite; a single
  consolidated two-tenant seed-and-verify script was authored but not executed
  against a running stack here.

---

## Phase 11 — Documentation

- README updated with Phases 3–12 features and API routes.
- Evaluation framework documented separately.
- Milestone reports, validation and release documents added.
- `CHANGELOG.md` and `docs/PORTFOLIO.md` added.
- Existing architecture, security, deployment and demo docs remain the detailed
  references; no unverified metric is repeated.

---

## Phase 12 — Release readiness

- Version updated to `1.0.0` in API metadata, root version file and frontend
  package metadata where applicable.
- Changelog added.
- No tag is created. Tagging requires explicit user approval.
- The release report records the verification matrix and remaining limitations.

---

## Files added or changed (summary)

| Area | Files |
|---|---|
| Models | `backend/app/models/feedback.py`, `knowledge_gap.py`, `tenant_settings.py`, `models/__init__.py` |
| Migration | `backend/alembic/versions/f2c3d4e5a6b7_milestone10_product_phases.py` |
| Services | `services/feedback.py`, `services/knowledge_gaps.py`, `services/tenant_settings.py`, `services/analytics.py`; RAG settings/trace/gap/version hooks in `rag_service.py` |
| API | `routes/feedback.py`, `knowledge_gaps.py`, `tenant_settings.py`, `analytics.py`; RAG response explanation |
| Schemas | `schemas/feedback.py`, `knowledge_gap.py`, `tenant_settings.py`, `rag.py` |
| Capabilities | `core/security_constants.py`, `frontend/src/lib/capabilities.ts` |
| UI | `feedback-page.tsx`, `knowledge-gaps-page.tsx`, `analytics-page.tsx`, `settings-page.tsx`, onboarding, chat feedback/explanation, navigation/routes |
| Ops | `main.py` probes/logging, `docker-compose.yml` workers, `scripts/backup.ps1`, `scripts/backup.sh`, CI |
| Tests | feedback, settings, analytics/gaps, explainability, health/logging, onboarding, Playwright spec |
