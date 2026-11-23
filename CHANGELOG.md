# Changelog

All notable changes to VaultRAG. Versions before 1.0.0 are development
milestones; no release tag is created by this file.

## [1.0.0] — Milestone 10

### Added

- RAG evaluation framework: versioned synthetic datasets, real-pipeline runs,
  Recall/Precision/MRR/NDCG at K=3/5/10, answer and citation metrics, optional
  labeled LLM judge, six configurations, comparison dashboard and admin API.
- Answer feedback with owner edit/retract, administrator review, reviewer-scoped
  citation visibility and audit events.
- Privacy-preserving knowledge-gap signals and clusters: permission-denied
  queries excluded, redacted questions, minimum group size, lifecycle,
  assignment and real-pipeline re-evaluation.
- Explainable answers: document version/number, citation verification, retrieval
  mode and contribution flags, authorized/denied candidate counts and bounded
  evidence rationale.
- Unified analytics for overview, usage, documents, retrieval, integrations,
  feedback, gaps and security, with aggregate-only document measures.
- Versioned tenant settings with validation, history and rollback, applied as
  narrowing defaults in the RAG pipeline.
- Role-aware onboarding, skip link, accessible chart summaries and new empty/
  error states.
- Live/ready health probes, content-free request-duration logging, Compose worker
  services, CI migration drift checks, and PowerShell/Bash backup scripts.
- Two-tenant demo seed script and a Milestone 10 Playwright spec.

### Changed

- RAG responses now include an `explanation` object, source version fields,
  citation verification and `feedback_enabled`.
- ADMIN gained `VIEW_FEEDBACK`, `MANAGE_KNOWLEDGE_GAPS`, `VIEW_ANALYTICS` and
  `MANAGE_TENANT_SETTINGS`.
- Application version moved to `1.0.0`.

### Security

- New tables use tenant-safe composite keys, indexes, RLS enabled and forced,
  `tenant_isolation` policies and least-privilege grants.
- Feedback and gaps are soft-deleted; settings history is append-only.
- No new endpoint accepts a client-supplied tenant ID.
- Unauthorized chunk text remains outside feedback, gap, analytics and
  explainability payloads.
