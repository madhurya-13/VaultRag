# VaultRAG — Portfolio Summary

## Problem

Enterprises want to ask questions across internal documents, but a generic RAG
demo leaks restricted content, cannot explain its evidence and gives operators no
way to measure or improve retrieval quality. VaultRAG treats authorization,
evaluation, feedback and operations as first-class product surfaces.

## What it does

- Permission-aware retrieval over PDFs with attribute- and permission-based
  access control, enforced in the application and by PostgreSQL row-level
  security.
- Grounded answers with grouped citations, evidence strength, document versions
  and citation verification.
- **Evaluation:** labeled synthetic corpus, real-pipeline runs, Recall,
  Precision, MRR and NDCG at K=3/5/10, answer/citation metrics, an optional
  labeled LLM judge and six retrieval configurations with a comparison dashboard.
- **Feedback loop:** per-answer sentiment/category feedback, owner retraction and
  an administrator review queue that re-authorizes citations for the reviewer.
- **Knowledge gaps:** privacy-preserving detection of repeated unanswered
  questions, with permission denials excluded, minimum group size, assignment,
  lifecycle and real-pipeline re-evaluation.
- **Explainability:** retrieval mode and contribution flags, authorized vs denied
  candidate counts, version metadata and verification status — without prompts or
  chain-of-thought.
- **Analytics:** aggregate usage, documents, retrieval quality, integrations,
  feedback, gaps and security.
- **Tenant settings:** bounded, versioned retrieval defaults with history and
  rollback that cannot weaken security controls.
- Enterprise plumbing: multi-tenant roles/capabilities, audit logs, knowledge
  graph, integrations, evaluation, health probes, worker services and backups.

## Engineering highlights

- FastAPI + SQLAlchemy + Alembic, PostgreSQL with pgvector and RLS, React +
  TypeScript + TanStack Query + Recharts, Docker Compose, GitHub Actions.
- 368 backend tests and 27 frontend unit tests passing at the Milestone 10
  release candidate, plus a Playwright suite for real-browser flows.
- Tenant-safe composite foreign keys, explicit grants and migration drift checks.
- Deterministic, content-free telemetry: request duration logs without query
  strings or bodies; aggregate-only document analytics.
- Honest limitations: no independent security audit; browser E2E and restore
  testing require a provisioned environment.

## Documentation

- [Architecture](architecture/SYSTEM_ARCHITECTURE.md)
- [Security](SECURITY.md)
- [Evaluation framework](evaluation/EVALUATION_FRAMEWORK.md)
- [Milestone 10 report](milestone-10/PHASES-3-12-REPORT.md)
- [Validation](milestone-10/FINAL-VALIDATION-REPORT.md) ·
  [Release](milestone-10/FINAL-RELEASE-REPORT.md)
- [Deployment](deployment/DEPLOYMENT_GUIDE.md) · [Demo](demo/DEMO_SCRIPT.md)

## Resume bullets (verified features only)

- Built a permission-aware multi-tenant RAG platform (FastAPI, PostgreSQL +
  pgvector, React/TypeScript) with database-enforced tenant isolation and
  attribute/permission-based document authorization.
- Implemented a RAG evaluation framework running the real retrieval pipeline,
  with deterministic Recall/Precision/MRR/NDCG metrics, answer/citation checks,
  six configurations and a comparison dashboard.
- Added an answer-feedback and administrator review loop, privacy-preserving
  knowledge-gap analysis with real-pipeline re-evaluation, and explainable
  responses with document-version and citation-verification metadata.
- Hardened operations with liveness/readiness probes, content-free duration
  logging, worker services, migration-drift CI checks and verified backup
  scripts; validated with 368 backend and 27 frontend tests.

## Screenshot checklist

Screenshots are intentionally not fabricated. Capture these from a running,
synthetic environment:

- Employee assistant: grounded answer, grouped citations, confidence.
- “Why this answer” panel: mode, contributions, versions, verification.
- Feedback form and feedback review queue with reviewer-scoped citations.
- Knowledge-gap cluster, redacted question and re-evaluation result.
- Analytics overview and one chart with its text summary.
- Tenant settings, history and rollback confirmation.
- Role-aware onboarding and mobile navigation.
- `/health/ready` response and a verified backup TOC listing.

Redact tokens, tenant IDs and any real documents.

## 30 interview questions

1. **Why build this?** Generic RAG demos ignore authorization and quality
   measurement; VaultRAG makes both product-level concerns.
2. **How is tenant isolation enforced?** Server-derived tenant context, every
   query filters by tenant, composite tenant-safe keys, RLS enabled/forced and a
   `tenant_isolation` policy.
3. **What stops a client choosing a tenant?** No endpoint accepts `tenant_id`;
   it comes from the authenticated context.
4. **How does document authorization work?** Explicit permissions plus ABAC
   attributes are evaluated before chunks reach reranking, context or citations.
5. **How fast does revocation apply?** On the next request; the pipeline
   re-filters authorized chunks and regression tests cover it.
6. **Why store citations, not evidence text?** Conversations must not become a
   second copy of confidential documents or an authorization bypass.
7. **How are citations re-authorized on history reads?** Stored references are
   resolved and filtered against current permissions; missing sources are
   omitted/flagged.
8. **How does retrieval combine modes?** Semantic (pgvector), keyword
   (full-text) and optional graph evidence are merged, optionally reranked, then
   authorization-filtered before generation.
9. **How is the prompt protected from document injection?** Retrieved text is
   delimited/untrusted context, output is treated as text, and authorization
   never depends on the model.
10. **What does explainability expose?** Retrieval mode and contribution flags,
    authorized/denied counts, filters, rerank use, versions and verification —
    not prompts or chain-of-thought.
11. **How is citation validity checked?** Source IDs must reference chunks that
    were actually supplied and authorized; invalid citations are flagged/removed.
12. **What does evaluation measure?** Deterministic retrieval metrics at K,
    answerability, citation coverage/validity and unsupported-claim indicators;
    optional labeled LLM judging is separate.
13. **Why six configurations?** To compare semantic, keyword, hybrid, rerank and
    graph variants under the same dataset and authorization context.
14. **How do you know evaluation is honest?** Runs use the real pipeline, gold
    data is labeled, metrics are deterministic with fakes in tests, and
    authorization violations flag a run.
15. **What is feedback scoped to?** Tenant, user and an owned assistant message;
    duplicates update, retraction is soft, and audit events are recorded.
16. **What can an admin see in review?** Question, answer, feedback and only
    citations the reviewer may currently read; never chunk text copies.
17. **What is a knowledge gap?** A repeated insufficient-evidence query pattern,
    not a single failed search and not a permission denial.
18. **How is gap privacy preserved?** Permission denials are excluded, questions
    are normalized/redacted, only a SHA-256 fingerprint and keywords are stored,
    and no user identity is attached to a signal.
19. **Why a minimum group size?** To avoid exposing or acting on one-off private
    questions; clusters surface at three occurrences.
20. **What does gap re-evaluation do?** Runs the normalized question through the
    real RAG pipeline with admin context and records whether evidence improved.
21. **Why are document analytics aggregate-only?** An admin may not read every
    document, so returning titles would leak metadata; counts are documented as
    aggregate-only.
22. **How are analytics bounded?** Date ranges are validated (1–365 days), all
    values come from persisted tables, and empty tenants return zeros.
23. **How are tenant settings safe?** They only narrow retrieval defaults within
    server maxima; they cannot alter RBAC/ABAC, RLS, authorization or citation
    validation.
24. **How are settings changed safely?** Server-side bounds and cross-field
    validation, append-only history, audit events and rollback-as-new-version.
25. **How do settings affect RAG?** Defaults merge into each request; explicit
    request values are clamped by tenant and server maxima.
26. **How is the app observed?** Content-free request logs with method, path,
    status, duration and request ID; separate live/ready probes.
27. **What do the workers do?** Graph extraction and integration sync run as
    outbound-only Compose services behind a profile, waiting for migrations.
28. **How is data protected operationally?** Least-privilege runtime grants,
    append-only audit/history, soft deletion, verified `pg_dump` backups and a
    documented isolated restore procedure.
29. **What is tested?** 368 backend tests (RLS, ABAC, isolation, RAG, graph,
    integrations, versions, evaluation, feedback, gaps, settings, analytics,
    probes) and 27 frontend unit tests, plus a discovered Playwright suite.
30. **What remains unverified?** Browser E2E execution, live Ollama/embedding
    paths, the demo seed, backup restore, and independent security/accessibility
    review; no performance benchmark or deployment is claimed.
