# Milestone 10 — Phase 1 Complete System Audit

Date: 2026-11-23
Repository: `C:\Users\Madhurya\OneDrive\Desktop\VaultRag`
Baseline commit: `5b2d127` (`feat: complete Milestone 9 enterprise integrations and document versions`)
Scope of this phase: **read-only audit and planning.** No application code was modified.

---

## 1. Method and evidence

The audit inspected, directly:

- all FastAPI routers, services, schemas, models, dependencies and core configuration;
- all 9 Alembic migrations (single head verified with `alembic heads`);
- all 31 mapped tables;
- the React application, routes, pages, hooks, contexts, API client and UI primitives;
- backend tests, the dedicated `tests/security` suite, the offline evaluation harness and dataset;
- both GitHub Actions workflows, Dockerfiles, Compose stack, Nginx configuration and environment examples;
- existing documentation under `docs/` and the root `README.md`.

Checks executed during the audit (non-mutating):

| Check | Command | Result |
|---|---|---|
| Test discovery | `pytest --collect-only -q` (from `backend`) | 344 tests collected |
| Full backend suite (run immediately before Phase 1 on the same commit) | `pytest -q` | **342 passed, 2 skipped** |
| Integration subset | `pytest app/tests/integrations -q` | 43 passed |
| Migration heads | `alembic heads` | single head `d9a3f1b7c2e4` |
| Frontend unit tests (same commit) | `npm test` | **25 passed (5 files)** |
| Frontend production build | `npm run build` | succeeded |
| Lint | `ruff check` (backend), `npm run lint` | clean at commit time |

Environment limitations observed during the audit:

- Docker Desktop's Linux engine is **not running** (`npipe:////./pipe/dockerDesktopLinuxEngine` unavailable), so the Compose stack, Docker image build and backup/restore test were **not re-executed** in this phase.
- The two skipped tests are opt-in real-model tests (`VAULTRAG_RUN_MODEL_INTEGRATION=1`, `OLLAMA_INTEGRATION=1`).
- Playwright E2E was not executed in this phase.

Nothing in this document is a claim that a check ran when it did not. Where a capability was inspected but not executed, it is marked **Unverified** or **Partial**.

---

## 2. System inventory

### 2.1 Backend (FastAPI, Python 3.12, SQLAlchemy 2, PostgreSQL 16 + pgvector)

Application entry point: `backend/app/main.py`. It installs TrustedHost and CORS middleware, a request-correlation middleware (`X-Request-ID`), security headers, a structured JSON formatter in production, an `AppError` handler, and a lifespan that fails fast if PostgreSQL is unreachable.

Routers registered: `auth`, `organizations`, `users`, `roles`, `attributes`, `documents`, `integrations.oauth_router`, `integrations.router`, `integrations.versions_router`, `knowledge_graph`, `rag`, `security`.

| Area | Implementation | Status |
|---|---|---|
| Authentication | JWT HS256 with issuer/audience validation, Argon2id, user/org/role reload per request | Implemented |
| RBAC | 9 capabilities; `require_capability`; ADMIN-only admin surfaces | Implemented |
| ABAC | department, clearance, employment status, location, designation; deny on missing attributes | Implemented |
| Document permissions | exact action grants to role or user; expiry; soft revocation; audit history | Implemented |
| PostgreSQL RLS | `ENABLE` + `FORCE`, fail-closed `security.current_tenant_id()`, transaction-local context | Implemented |
| Ingestion | PDF upload, PyMuPDF extraction, cleaning, token-aware chunking, MiniLM embeddings, pgvector storage, job guard | Implemented (synchronous) |
| Retrieval | semantic + PostgreSQL full-text + RRF fusion; S3-stage authorization; text fetched only for authorized IDs | Implemented |
| Query intelligence | deterministic intent/rewrite/expansion/decomposition; optional LLM rewriting (off) | Implemented |
| Reranking | optional local cross-encoder, adjacent merge, diversity cap, graceful failure | Implemented (disabled by default) |
| Reasoning | multi-document grouping, numeric/approval conflict detection, evidence assessment | Implemented |
| Citations | backend source IDs, bracketed-ID validation, post-generation re-authorization | Implemented |
| Conversations | owner-scoped CRUD, soft delete, summary, citation references only | Implemented |
| Graph | extraction (rules/rules+spaCy/optional LLM), resolution, traversal, evidence-gated reads, conflicts, governance, worker, analytics | Implemented |
| Integrations | Local/Google Drive/SharePoint connectors, encrypted credentials, sync jobs, ACL mirroring, OAuth, webhooks, worker | Implemented (providers mocked in tests) |
| Versions | immutable `document_versions`, checksum, one current version, audited rollback | Implemented |
| Security audit | append-only tenant-scoped log with request ID/IP; analytics aggregate | Implemented |
| Rate limiting | Nginx `limit_req` for `/api/auth` and `/api` only | Partial (edge only, not app-level) |
| Background processing | graph worker and integration worker scripts exist | Partial (not in Compose, ingestion still synchronous) |
| RAG evaluation | offline deterministic harness + 20-query dataset | Partial (no DB/API/UI, 4 methods, K fixed at 5) |
| User feedback | none | **Missing** |
| Knowledge-gap analysis | none | **Missing** |
| Unified analytics API | only `/security/analytics`, `/rag/analytics`, `/knowledge-graph/analytics` | Partial |
| Tenant settings / retrieval configuration | none | **Missing** |
| Explainability payload | sources, page ranges, excerpt, matched_by, evidence score, conflicts | Partial (no version, no retrieval explanation, no graph-contribution flag) |

### 2.2 Frontend (React 19, TypeScript, Vite 8, Tailwind 3, TanStack Query 5, Recharts 3, Radix UI)

Pages: `login`, `dashboard`, `assistant` (chat), `documents`, `graph`, `admin/users`, `admin/integrations`, `security`, `forbidden`, `not-found`.
Shared UI: 15 primitives (button, dialog, tabs, table, select, toast, etc.), `PageHeader`, `StatCard`, `EmptyState`, `ErrorState`, `LoadingPanel`, badges.

| Area | Status | Notes |
|---|---|---|
| Routing and protected routes | Implemented | Lazy routes, capability-gated admin routes |
| Authentication state | Implemented | localStorage JWT, 401 event clears token; no refresh token |
| API integration | Implemented | Axios client, typed responses, centralized error messages |
| Employee chat | Implemented | Modes, filters, citations, source groups, evidence level, conflicts, timings, conversations |
| Document management | Implemented | Upload/process/list, permissions, chunks (admin) |
| Security dashboard | Implemented | Charts, filters, audit detail dialog, retrieval tab |
| Knowledge graph explorer | Implemented | Query and conflicts; no node/edge graph visualization |
| Integrations | Implemented | Full admin workflow |
| Version history UI | **Missing** | `DocumentVersion` types exist; no page uses them |
| Feedback UI | **Missing** | — |
| Evaluation UI | **Missing** | — |
| Unified analytics UI | **Missing** | Only security + retrieval aggregates |
| Knowledge-gap UI | **Missing** | — |
| Tenant settings UI | **Missing** | — |
| Onboarding | **Missing** | — |
| Empty/loading/error states | Partial | Good on dashboard/documents/security; not universal |
| Accessibility | Partial | Labels, aria attributes, dialogs, focus styles present; charts have no text/table alternative; not audited with a tool |
| Responsive design | Partial | Responsive grids/tables present; not verified on tablet/mobile in this phase |

### 2.3 Database

Single Alembic head `d9a3f1b7c2e4`. Migrations cover: base tenant schema, Milestone 2 schema, ingestion, HNSW, Milestone 7 advanced RAG, Milestone 8 knowledge graph, Milestone 9 integrations.

31 tables:

- Identity/tenant: `organizations`, `roles`, `users`, `user_roles`, `user_attributes`;
- Documents: `documents`, `document_permissions`, `document_chunks`, `document_ingestion_jobs`, `document_versions`;
- Conversations/analytics: `conversations`, `conversation_messages`, `retrieval_analytics`;
- Audit: `security_audit_logs`;
- Graph: `knowledge_nodes`, `knowledge_edges`, `knowledge_evidence`, `entity_aliases`, `knowledge_entity_reviews`, `knowledge_entity_merges`, `knowledge_conflicts`, `knowledge_extraction_jobs`, `knowledge_query_analytics`;
- Integrations: `integrations`, `integration_credentials`, `integration_repositories`, `external_documents`, `integration_sync_jobs`, `integration_sync_events`, `integration_identity_mappings`, `integration_oauth_states`.

RLS is enabled and forced on tenant tables, with grants tailored per role. `document_chunks` uses `VECTOR(384)` with an optional-to-schema HNSW cosine index and a GIN full-text index. Tenant-safe composite foreign keys are used throughout.

### 2.4 Testing

344 collected: 342 pass, 2 skip. Coverage by area:

- authorization/RLS/tenancy: `test_rls.py`, `test_tenant_isolation.py`, `test_abac.py`, `test_rbac.py`, `test_document_permissions.py`, `test_permission_revocation.py`, `test_retrieval_authorization.py`, `test_rag_security.py`;
- ingestion: upload, PDF extraction, cleaning, chunking, embeddings, metadata, storage, pipeline, security;
- RAG: pipeline, hybrid retrieval, keyword, reranking, query rewriting/decomposition, evidence, citations, conversations, analytics, multi-document reasoning;
- graph: extraction, normalization, conflicts, traversal, seeds, integration/governance suites;
- integrations: connectors, interface, registry, credential encryption, schema, full API sync/version flow;
- evaluation: metric math, dataset coverage, authorization violations, leak measurement;
- `tests/security`: 8 test functions (JWT forgery, issuer/audience, production settings, host/headers, SQL injection, method rejection, tenant isolation + revocation, no raw HTML). These run as a separate CI step and are **not** in the default `pytest` invocation.

Gaps in testing:

- No tests for feedback, knowledge gaps, evaluation APIs (they do not exist yet).
- No tests for tenant settings.
- No analytics API tests beyond security/retrieval.
- No Playwright test beyond `frontend/e2e/milestone5.spec.ts`.
- No multi-tenant E2E covering graph, versions, feedback, analytics or evaluation.
- Only 5 frontend test files (login, chat, documents, api, plus setup).
- No test asserts that archived/deleted documents invalidate graph evidence; graph evidence invalidation exists in the graph job path but is not invoked by integration deletion.

### 2.5 Deployment and operations

- Compose services: `db`, `ollama`, `migrate`, `api`, `web`. Internal network isolation, read-only containers, capability drops, health checks, named volumes. API and Ollama not published; web bound to loopback.
- `migrate` runs `alembic upgrade head`; API waits for migration and Ollama health.
- **Neither `integration_worker.py` nor `graph_worker.py` is a Compose service.** Only the API is deployed, so scheduled sync and graph extraction jobs depend on manual execution.
- Health: `/health` (DB only), `/rag/health` (DB, pgvector, FTS column, embedding label, Ollama). No readiness vs liveness separation; no `/ready`.
- CI: backend (ruff, bandit, pip-audit, full pytest, security suite), frontend (npm audit, oxlint, tsc, vitest, build), gitleaks. Container-scan workflow builds and Trivy-scans both images but runs only on Dockerfile/Compose changes or manually.
- CI does **not** run `alembic check`, a fresh-database migration test, or a Compose smoke test.
- No backup/restore scripts. Backup is described only as prose in `docs/deployment/DEPLOYMENT_GUIDE.md`.
- Secrets are gitignored; no `.env` is tracked. `backend/.local/` remains untracked and should be excluded or removed before release.
- No app-level rate limiting; protection depends on the Nginx edge.
- Ingestion and sync HTTP paths are synchronous; the graph worker exists but is not scheduled by Compose.

### 2.6 Documentation

Existing: root `README.md` (long, milestone-oriented), `docs/SECURITY.md` (Milestones 1–4 only), threat model, security test report, OWASP checklist, deployment guide (43 lines), production checklist, demo script (Milestone 6), architecture note (27 lines), evaluation report (Milestone 7, states DB was unavailable), retrieval analytics, knowledge-graph docs, integration docs.

Documentation gaps:

- `docs/SECURITY.md` is stale relative to Milestones 7–9 (no graph, integrations, versioning, evaluation).
- Architecture document is a short overview, not a full architecture reference.
- Deployment guide lacks backup/restore commands, worker setup, health semantics and troubleshooting depth.
- No consolidated RAG evaluation methodology/results doc for Milestone 10.
- No portfolio-oriented README structure, screenshots list, resume description or interview guide.
- Demo script predates graph, integrations, versions, feedback, evaluation, analytics.
- Integration test report claims from Milestone 7 are outdated (DB suite now passes).

---

## 3. Feature status matrix

Legend: **Done** = implemented and exercised by automated tests; **Partial** = implemented but incomplete against Milestone 10 scope or insufficiently tested; **Missing** = absent; **Unverified** = cannot be executed in the current environment.

| Capability | Status | Evidence / gap |
|---|---|---|
| Multi-tenant organizations | Done | RLS + tenant-safe FKs + isolation tests |
| JWT auth, role reload, revocation of users/roles | Done | `test_auth.py`, `test_api_security.py`; no refresh/MFA |
| RBAC + ABAC + document permissions | Done | Dedicated suites |
| PostgreSQL RLS (real PostgreSQL) | Done | `test_rls.py`, `tests/security` |
| PDF upload → cleanup → chunk → embed | Done | Pipeline tests; synchronous only |
| Semantic / keyword / hybrid / RRF | Done | Retrieval tests; benchmark is offline lexical |
| Reranking | Partial | Implemented, disabled by default; no real cross-encoder measurement |
| Query rewriting/decomposition | Done | Deterministic tests |
| Multi-document reasoning/conflicts | Done | Reasoner tests + UI display |
| Citation validation | Done | Service + pipeline + security tests |
| Conversation memory with re-authorization | Done | Memory tests + history re-auth |
| Knowledge graph extraction/resolution/traversal | Done | Graph suites; spaCy optional |
| Graph governance/conflicts/temporal fields | Done | Governance/integration tests |
| Graph-enhanced RAG modes | Done | `graph`, `graph_hybrid` wired in `rag_service` |
| Google Drive / SharePoint connectors | Partial | Deterministic mock transports; no live credentials |
| Sync, ACL mirroring, OAuth, webhooks | Done | Integration API/sync tests |
| Document versions + rollback | Partial | Backend done and tested; **no frontend version history** |
| Security dashboard | Done | Live API data + charts |
| Integration admin UI | Done | Create/connect/discover/sync/credentials/identities |
| RAG evaluation (offline) | Partial | 8-doc/20-query dataset, 4 methods, K=5 fixed, no DB/API/UI/graph methods |
| Evaluation persistence/API/dashboard | Missing | — |
| Answer feedback capture/review | Missing | — |
| Knowledge-gap detection/management | Missing | Only `insufficient_evidence` counts exist |
| Explainability (version, verification status, retrieval explanation) | Partial | Sources/evidence/conflicts exist; no version or retrieval explanation |
| Unified analytics | Partial | Security + retrieval + graph aggregates only |
| Tenant settings / retrieval configuration | Missing | — |
| Onboarding | Missing | — |
| Workers deployed | Partial | Scripts exist; not in Compose |
| Readiness endpoint | Missing | Only `/health` and `/rag/health` |
| Backup/restore scripts and verified restore | Missing | Prose only |
| E2E multi-tenant validation | Partial | One legacy Playwright spec; backend multi-tenant tests exist |
| Portfolio documentation | Partial | Good raw material; not consolidated for Milestone 10 |

### Broken features

No reproducible functional breakage was found in the audited commit: the full backend suite, integration suite, frontend tests and production build pass. The following are **not "broken"** but are correctness/consistency risks found by reading code:

1. **Archived external documents do not invalidate graph evidence.** `integration sync` sets `documents.is_active = False` and `processing_status = ARCHIVED` but does not call `invalidate_document_evidence`. Graph reads re-authorize evidence documents, so archived content is not returned; however stale evidence rows remain "valid" and node/edge counts can include them until reprocessing. This must be closed for global requirement 18.
2. **No document deletion/reprocessing API.** Documents are only deactivated; derived chunks, embeddings, versions and graph evidence have no unified cleanup path.
3. **`backend/app/tests` is the default test path**, so `tests/security` only runs in CI's dedicated step; a local `pytest` does not cover it.
4. **Version history is API-only.** Users cannot see or restore versions in the UI.
5. **Signed-in users' JWT is stored in localStorage** (XSS surface). It is a common trade-off but must be documented as a limitation, not claimed as hardened.
6. **Conversation message content** stores user questions and answers; feedback and gap analysis must not widen access to it.

---

## 4. Security review

### Controls that are strong

- Authorization happens in SQL prefilter, service-layer re-authorization, and RLS; text is fetched only after authorization.
- The reranker/LLM only ever see the authorized set; tests assert unauthorized markers never reach the recording LLM.
- Permissions are re-evaluated per request; conversation citations are re-authorized on read.
- Graph reads are evidence-gated through `authorize_document_ids` and hide nodes without visible evidence.
- External credentials are AES-GCM encrypted with tenant/integration/provider-bound AAD; no secret is returned.
- OAuth uses signed single-use state + PKCE; webhooks are signed.
- Upload validates extension, MIME, PDF structure, encryption, page count and text presence; storage paths are not exposed.
- Production settings reject wildcard hosts and privileged DB roles; docs disabled in production; safe error codes.
- Nginx supplies CSP, rate limits, body cap and loopback bind.
- Audit log is append-only from the application role's perspective.

### Risks and gaps (to be closed or documented in Milestone 10)

| ID | Risk | Severity | Proposed treatment |
|---|---|---|---|
| S1 | Graph evidence not invalidated when a synced document is archived | Medium | Call `invalidate_document_evidence` + `refresh_edge_counts` in the archive path; add a test |
| S2 | No app-level rate limiting (only the Nginx edge; direct API exposure bypasses it) | Medium | Document the edge requirement; add bounded per-identity limits only if the architecture supports it without a new dependency |
| S3 | Query text is not stored, which is good for privacy but blocks gap clustering | Medium | Store a redacted/normalized query fingerprint and embedding only with explicit consent/config; never store raw restricted content |
| S4 | Feedback review could expose source content | High (if built naively) | Re-authorize every cited document at review time; never store chunk text in feedback |
| S5 | LLM-judge evaluation could be treated as ground truth | Medium | Keep deterministic metrics primary; label judge metrics as model-based; version prompts/model |
| S6 | Evaluation runner could bypass authorization to get "better" numbers | Critical | Reuse `answer_question` with a real `AuthenticatedContext`; never add an eval-only bypass; persist the authorization decision |
| S7 | Tenant settings could weaken authorization | Critical | Settings may only tune retrieval, never RBAC/ABAC/RLS; validate server-side and audit changes |
| S8 | JWT in localStorage | Medium | Document; do not claim XSS immunity |
| S9 | Analytics/gap aggregation can leak content via small groups | High | Aggregate + threshold minimum group size; admin-only; tenant-scoped |
| S10 | Stale `backend/.local/` and local `.env` files near the repo | Low | Verify ignore rules and remove generated artifacts before release |
| S11 | No verified backup/restore | High | Script + documented restore; execute if Docker is available, otherwise report blocked |
| S12 | Workers are not deployed, so sync/graph jobs silently depend on manual runs | Medium | Add worker services with health/restart policy |
| S13 | Ingestion failure can leave a `PROCESSING` job after a crash | Medium | Add stale-job recovery on startup or a bounded cleanup path |
| S14 | Evaluation/feedback/gap endpoints must not accept `tenant_id` or user-supplied scope | Critical | Strict Pydantic models, server-derived tenant/user, RLS, admin capability |

Prompt-injection defenses remain: system/context separation, delimiter neutralization, untrusted-context labeling, citation validation, and the absolute authorization gate. These must not be weakened by explainability or evaluation features.

---

## 5. Technical debt

1. **Single large RAG orchestrator** (`app/services/rag_service.py`) mixes retrieval, graph, rerank, LLM, persistence and analytics. Adding evaluation and explainability without a seam risks duplication. Extract a retrieval-only entry point that returns authorized evidence plus a trace object.
2. **Metrics/analytics duplicated by concern** (`retrieval_analytics`, security analytics, graph analytics). Phase 6 should compose them behind one analytics service rather than re-querying in routes.
3. **No evaluation persistence model.** The offline harness is test code, not a product feature.
4. **Fixed K in the harness** and no graph method; dataset too small and lacks version/permission-change cases.
5. **Document lifecycle is incomplete**: no reprocess/delete, stale graph evidence, no unified derived-data cleanup.
6. **No tenant settings table**, so retrieval defaults are environment-only. Per-tenant configuration needs a versioned, audited model.
7. **Workers are scripts without deployment**; graph and integration jobs have no scheduler in Compose.
8. **Health checks are not split** into liveness/readiness; `/health` only checks DB.
9. **Request timing is not logged**; no per-request latency metric outside RAG analytics.
10. **Docs drifted** across milestones; root README is long and milestone-centric rather than product-centric.
11. **Frontend coverage** is thin (5 files / 25 tests) and there is no shared state pattern for new admin pages beyond TanStack Query.
12. **No migration/rollback convention documented** for Milestone 10 additions (RLS + grants must be included every time).
13. **`.local/`, Playwright reports and build output** must be checked before release.
14. **No retention/cleanup policies** for analytics, evaluation runs, feedback or gap data.

---

## 6. Phase 1 conclusion

VaultRAG has a credible, tested core: tenant isolation, authorization-before-context, hybrid retrieval, citations, graph, integrations and versioning all exist and are exercised by 342 passing tests on real PostgreSQL. Milestone 10 is therefore mostly **productization and intelligence layered on existing services**, not a rebuild.

The largest genuine gaps are:

1. evaluation is offline-only and cannot be run as a product workflow;
2. no feedback or knowledge-gap loop;
3. no unified analytics or tenant configuration;
4. partial explainability and missing version UI;
5. deployment/operations hardening (workers, readiness, backup/restore, CI coverage);
6. portfolio-grade documentation.

No phase should introduce a second retrieval, authorization, analytics or graph implementation. Every new surface must call the existing services with the existing `AuthenticatedContext` and RLS transaction discipline.

See [IMPLEMENTATION-ROADMAP.md](IMPLEMENTATION-ROADMAP.md) for the sequenced plan and [ACCEPTANCE-CRITERIA.md](ACCEPTANCE-CRITERIA.md) for measurable gates.
