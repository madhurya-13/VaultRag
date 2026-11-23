# Milestone 10 — Acceptance Criteria

Companion documents: [PHASE-1-AUDIT.md](PHASE-1-AUDIT.md) · [IMPLEMENTATION-ROADMAP.md](IMPLEMENTATION-ROADMAP.md)

A phase is accepted only when **every applicable criterion is demonstrated**, not when code merely exists. Evidence must be a command output, a test name/result, an inspected API response or a described UI check. Where a check cannot run (Docker unavailable, no provider credentials, no Ollama), the report must state the blocker and must not mark the criterion as passed.

---

## 1. Global gates (apply to all phases)

### G1 — Security invariants

- [ ] PostgreSQL RLS remains enabled and forced on every tenant table, including new Milestone 10 tables.
- [ ] Every new query/write filters by the server-derived tenant; no endpoint accepts `tenant_id`.
- [ ] Authorization is enforced in the backend; frontend gating is never the only control.
- [ ] Unauthorized chunk text never reaches the reranker, context builder, LLM, graph context, citations, feedback review, evaluation runner or analytics content.
- [ ] Permission revocation takes effect on the next request (existing test must keep passing).
- [ ] Conversation history and stored citations are re-authorized when read.
- [ ] Graph traversal and evidence remain document-authorized.
- [ ] New admin endpoints require an explicit capability; non-admin roles receive 403.
- [ ] No new endpoint can bypass authorization "for evaluation", "for analytics" or "for debugging".
- [ ] No secrets, tokens, prompts or chunk text in logs, responses or new tables.

**Evidence required:** security test run, new endpoint role matrix, RLS/grants inspection for new migrations.

### G2 — Data integrity

- [ ] New Alembic migration(s) produce a single head; `alembic upgrade head` succeeds on a clean test database.
- [ ] `alembic check` reports no pending model changes.
- [ ] New tables have tenant-safe foreign keys, indexes for their access paths, and check constraints for enumerated values.
- [ ] Runtime role grants are least-privilege (no DELETE/UPDATE unless the feature requires it; append-only where stated).
- [ ] Deletion/retraction semantics are explicit (soft vs hard) and tested.

### G3 — Honest reporting

- [ ] Every metric shown is computed from persisted/measured data.
- [ ] No fabricated benchmark, accuracy, performance, deployment or test result.
- [ ] Model-based evaluation is labeled as model-based; deterministic metrics are primary.
- [ ] Unrun checks (live providers, Docker, Playwright, LLM judge) are reported as unrun with the reason.
- [ ] Documentation does not claim capabilities the audit marks Missing or Partial unless the phase completed them.

### G4 — Test and build health

- [ ] Backend suite passes against real PostgreSQL; previously passing tests still pass.
- [ ] New backend code has unit and integration coverage for the happy path, authorization failures, tenant boundaries and validation errors.
- [ ] Frontend `tsc -b`, lint, tests and production build pass after each phase that touches the frontend.
- [ ] Ruff (and Bandit where applicable) clean on changed backend files.
- [ ] No skipped new test without an explicit, documented reason.

### G5 — Documentation

- [ ] Phase documentation updated (feature docs + API table + env vars if any).
- [ ] Any new capability/role mapping documented in both backend constants and frontend mirror.
- [ ] Limitations updated honestly.

### G6 — Reviewability

- [ ] Phase report lists modified files, created files, exact commands, expected output and troubleshooting.
- [ ] No unrelated refactor or restyle is bundled into a phase.
- [ ] Work is committed only when the user asks; each phase leaves the tree in a runnable state.

---

## 2. Phase 2 — Evaluation framework

### Functional
- [ ] A tenant admin can create/import a dataset, run an evaluation, list runs, inspect results and failures, and compare configurations from the UI and API.
- [ ] Runs execute through the real permission-aware pipeline (`answer_question` and/or the real retriever + `filter_authorized_chunks`) with a real authenticated context.
- [ ] Configurations exist for: semantic, keyword, hybrid, hybrid+rerank, hybrid+graph, hybrid+graph+rerank; each run stores its configuration.
- [ ] K is configurable (at least 3, 5, 10); metrics stored per K or per configured K.
- [ ] Metrics implemented and unit-tested on hand-computed examples: Recall@K, Precision@K, MRR, NDCG@K, retrieval success rate, relevant-evidence rate.
- [ ] Answer metrics implemented where deterministic: answerability accuracy, citation coverage, citation validity, unsupported-claim indicator; answer relevance/correctness only with a labeled reference.
- [ ] Optional Ollama judge is off by default, records model + prompt version, and cannot fail a run.
- [ ] Dataset covers every required category, including version and permission-change cases; gold chunks always belong to allowed documents.
- [ ] Synthetic only; no confidential content.

### Security
- [ ] `authorization_violations == 0` for every run; the runner fails the run or flags it if any unauthorized chunk appears.
- [ ] A restricted document never contributes evidence or citations for a caller who cannot read it.
- [ ] Evaluation endpoints are ADMIN-only and tenant-scoped; cross-tenant run/dataset access returns 404.
- [ ] Evaluation results do not store chunk text or prompts beyond what is necessary and documented.

### UI
- [ ] Evaluation page shows runs, dataset version, config, metrics, latency, error rate and comparison chart from API data.
- [ ] Failure inspector shows the question, expected vs retrieved evidence, and only authorized citations.
- [ ] Empty state (no runs/datasets) guides the admin to run or import a dataset; loading and error states exist.

### Verification
- [ ] API test matrix: admin success, non-admin 403, foreign tenant 404, invalid config 422.
- [ ] Re-running the same dataset/config produces the same deterministic metrics (modulo LLM-judge metrics when enabled).
- [ ] Documented reproduction command and measured results (or an explicit "not executed" if the environment blocks it).

---

## 3. Phase 3 — Feedback

- [ ] Users can submit positive/negative feedback and choose a problem category with an optional comment on an answer they own.
- [ ] Duplicate submissions are prevented or update the existing entry; users can edit and retract.
- [ ] Submission is confirmed in the UI without cluttering the chat.
- [ ] Feedback rows are tenant/user/message scoped with indexes and constraints.
- [ ] Admins with `VIEW_FEEDBACK` see totals, sentiment, category breakdown, trends, unreviewed/reviewed counts and can filter by date/category/status.
- [ ] Review shows question, answer and only citations the reviewer may currently read; no chunk text.
- [ ] Review supports status, categorization, internal notes, reviewer ID and timestamp; ability to flag missing document/retrieval/ingestion/permission issue.
- [ ] Feedback never changes permissions, roles or retrieval policy and never trains a model.
- [ ] Cross-tenant and cross-user access returns 404/403; tests cover both.
- [ ] Audit events recorded for submit/update/retract/review.

---

## 4. Phase 4 — Knowledge gaps

- [ ] System distinguishes missing knowledge, poor retrieval, insufficient permissions, outdated, conflicting and unanswerable.
- [ ] Permission-denied queries are **not** counted as knowledge gaps; a test proves this.
- [ ] Aggregation is tenant-scoped and groups similar queries using normalization plus local embedding similarity (or a documented deterministic alternative).
- [ ] Raw restricted query content is not stored or exposed; minimum group size and retention are enforced.
- [ ] Dashboard shows frequent gaps, trends, topic clusters, severity, status, assignment and resolution history from real data.
- [ ] Admin can assign, resolve, link a resolving document and re-evaluate; re-evaluation uses the actual RAG pipeline and records whether answerability improved.
- [ ] Individual users are not identified unnecessarily in analytics.
- [ ] Tests cover clustering determinism, isolation, privacy thresholds, resolution verification.

---

## 5. Phase 5 — Explainability

- [ ] Each source exposes document title, page range, document version (ID/number), bounded excerpt and citation verification status when available.
- [ ] Response includes retrieval explanation: mode, semantic/keyword/graph contribution, rerank usage, filters, authorized candidate count and evidence rationale.
- [ ] Explanation contains no system prompt, secrets, raw reasoning/chain-of-thought, unauthorized candidate IDs or sensitive policy internals.
- [ ] Conflicting evidence is displayed with authorized sources, dates/versions, and no silent winner selection.
- [ ] Invalid citations are removed/flagged before rendering; citation links cannot fetch unauthorized content.
- [ ] Version metadata is correct after a rollback (integration test).
- [ ] UI panel is expandable, keyboard accessible and does not leak filtered sources.

---

## 6. Phase 6 — Analytics

- [ ] One tenant-scoped analytics API serves overview, usage, documents, retrieval, integrations, feedback, gaps and security sections.
- [ ] All values derive from real tables; no hardcoded cards.
- [ ] Date filters are bounded and validated; empty tenants return zeros/null states rather than errors.
- [ ] Document analytics only include documents the requesting admin may read (current authorization), or are explicitly aggregate-only and documented.
- [ ] Security analytics never expose raw secrets or unrestricted logs; existing audit page remains the detail view.
- [ ] Dashboard renders charts and tables from the API, with loading/empty/error states and accessible summaries.
- [ ] Endpoint-by-endpoint tenant isolation tests pass.

---

## 7. Phase 7 — Tenant settings

- [ ] Settings are stored per tenant, versioned/audited, and validated server-side with safe bounds and cross-field rules.
- [ ] Supported settings only: default search mode, default/top-K limits, rerank/graph enable flags, candidate/evidence thresholds, feedback enable, retention, upload limits/types where already supported.
- [ ] Settings cannot disable or weaken RBAC, ABAC, RLS, document authorization or citation validation.
- [ ] Request values cannot exceed server maxima; invalid values return 422 with a safe message.
- [ ] Changes and rollbacks are audited; rollback restores a prior version.
- [ ] Settings page is organized, admin-only; employees never see it in navigation or routes.
- [ ] RAG behavior reflects settings (integration test) and defaults still apply when no settings exist.

---

## 8. Phase 8 — UX, onboarding, accessibility

- [ ] Role-aware onboarding appears for new users, can be dismissed, and never exposes admin content to employees.
- [ ] Meaningful empty states exist for documents, search, conversations, feedback, gaps, integrations, graph, evaluation and analytics.
- [ ] Error states distinguish network, auth, permission, validation, upload/ingestion, sync and RAG failures with actionable text; technical detail stays in logs.
- [ ] Keyboard-only navigation works for primary flows; focus is visible; dialogs trap/return focus; form errors are associated with inputs.
- [ ] Charts have a text/table alternative or accessible summary.
- [ ] Layout is usable at 375 px, 768 px and 1280 px for the key pages; no horizontal page overflow.
- [ ] Documented accessibility checks performed (manual + any tool used); **no WCAG certification claim**.
- [ ] Frontend tests cover new interactions/states; build/lint/typecheck pass.

---

## 9. Phase 9 — Observability, reliability, deployment

- [ ] Structured request logs include method, path, status, duration and request ID without content or secrets.
- [ ] `/health/live` and `/health/ready` are separate; readiness checks DB, pgvector, FTS and configuration; no secrets; existing `/health` remains compatible.
- [ ] Workers (graph, integration, evaluation if applicable) are defined in Compose with restart/health settings, wait for migrations, and expose no host ports.
- [ ] Stale job/ingestion recovery or bounded retry is implemented and tested, or the limitation is documented with a concrete failure mode.
- [ ] Timeouts/retries exist at external boundaries (LLM, providers) and fail safely.
- [ ] Backup script produces a dump and verifies it; restore procedure is tested **if Docker/PostgreSQL environment allows**, otherwise reported blocked with exact reason and unverified procedure.
- [ ] `docker compose config` is valid (requires Docker) and Compose remains loopback-bound.
- [ ] CI covers `alembic check`, fresh-DB migration, backend tests, frontend tests/build, lint and security scans; no automatic production deploy.
- [ ] Security scans executed with results recorded (Bandit, pip-audit, npm audit, gitleaks/Trivy where available); high/critical findings fixed or documented.

---

## 10. Phase 10 — End-to-end validation

- [ ] Two synthetic tenants with distinct users, documents, permissions, graph data, versions, integrations and analytics.
- [ ] Authentication, logout/token failure and unauthorized access verified.
- [ ] Cross-tenant isolation verified for documents, queries, graph, conversations, feedback, analytics, integrations, versions and evaluation (404/denial, no content).
- [ ] RBAC/ABAC/document permission and revocation verified, including "unauthorized chunks never reach reranker/LLM/graph/citations".
- [ ] Document upload/process/list/filter/delete-or-archive/version flows verified.
- [ ] RAG modes, reranking, graph modes, query rewriting, multi-document, insufficient evidence, citations and conflicts verified.
- [ ] Graph extraction/traversal/permissions/conflict/update/evidence invalidation verified.
- [ ] Integration flows verified with mocks; live Google/SharePoint explicitly marked unverified if no credentials.
- [ ] Evaluation, feedback, gap, analytics and settings workflows verified.
- [ ] Playwright covers the main frontend routes, or any blocked run is reported with the reason.
- [ ] `docs/milestone-10/FINAL-VALIDATION-REPORT.md` lists executed/passed/failed/skipped tests, security checks, performance measurements, limitations and remaining bugs.

---

## 11. Phase 11 — Documentation

- [ ] README answers: what, why, how, architecture, stack, features, security, setup, testing, evaluation, demo, limitations, license.
- [ ] Architecture doc includes Mermaid diagrams for all pipelines named in the prompt.
- [ ] `docs/SECURITY.md` reflects Milestones 1–10, including graph, integrations, versioning and evaluation.
- [ ] `docs/RAG-EVALUATION.md` documents dataset methodology, configurations, metrics, **measured results**, limitations and reproduction.
- [ ] `docs/DEPLOYMENT.md` covers prerequisites, env vars, Docker/Ollama, workers, health, backup, restore, troubleshooting, shutdown, recovery.
- [ ] `docs/DEMO-GUIDE.md` is a timed 10–15 minute synthetic demo.
- [ ] `docs/PORTFOLIO.md` contains a screenshot checklist (no fabricated images), resume bullets limited to verified features, and 30 interview questions with answers reflecting the code.
- [ ] No claim contradicts the audit or validation report.

---

## 12. Phase 12 — Release

- [ ] Backend suite, frontend tests/build, migrations, Compose validation, security scans and documentation completeness verified.
- [ ] `.gitignore` covers env files, dumps, logs, reports, build output, `.local`, private documents; repository scan finds no secrets.
- [ ] No confidential/synthetic-private documents, credentials or generated artifacts tracked.
- [ ] Version and changelog updated; tag `v1.0.0` proposed and **awaiting explicit user approval**.
- [ ] `docs/milestone-10/FINAL-RELEASE-REPORT.md` states status, verified features, exact results, performance/evaluation results (or "not measured"), limitations, risks and release verdict.
- [ ] Release report distinguishes "deployed" from "verified working"; nothing is described as deployed unless it actually was.

---

## 13. Definition of done for Milestone 10

Milestone 10 is complete when:

1. Phases 2–12 are implemented or a phase is explicitly descoped by the user.
2. All global gates G1–G6 pass with recorded evidence.
3. Every phase's acceptance list is satisfied or honestly reported as blocked.
4. The final validation report and release report exist and agree with the test outputs.
5. The repository is free of secrets and generated artifacts, documentation is portfolio-ready, and the release tag is proposed (not created without approval).
