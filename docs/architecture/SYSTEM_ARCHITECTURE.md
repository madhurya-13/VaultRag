# VaultRAG system architecture

VaultRAG is an existing FastAPI, PostgreSQL/pgvector and React application. The API authenticates a bearer token, reloads the user and effective roles from PostgreSQL, binds tenant context in the current transaction, and uses both application authorization and forced RLS. Document chunks are filtered against current document and ABAC permissions before context is built. Citations are checked against the authorized retrieval set.

```mermaid
flowchart LR
  U[User / React] --> A[JWT authentication]
  A --> T[Server-derived tenant context]
  T --> Z[RBAC + ABAC authorization]
  Z --> R[PostgreSQL FORCE RLS]
  R --> V[pgvector candidate retrieval]
  V --> P[Current document permission validation]
  P --> C[Authorized context builder]
  C --> L[Private Ollama]
  L --> Q[Citation validation]
  Q --> U
```

## Milestone 10 flows

### Permission-aware answer and explainability

```mermaid
flowchart LR
  U[Authenticated user] --> R[/rag/ask/]
  R --> S[Tenant settings resolver]
  S --> Q[Query understanding]
  Q --> H[Hybrid retrieval + optional graph]
  H --> A[Document authorization filter]
  A -->|authorized chunks only| RR[Rerank / evidence builder]
  RR --> L[LLM generation]
  L --> C[Citation verification]
  C --> V[Version metadata lookup]
  V --> P[Response + bounded explanation]
  P --> M[(Conversation citations)]
  A -->|denied| X[No evidence, insufficient answer]
```

### Feedback and knowledge gaps

```mermaid
flowchart TD
  M[Owned assistant message] --> F[Feedback upsert / retract]
  F --> AU[(Audit events)]
  F --> AR[Admin review: re-authorized citations]
  Q[RAG insufficient evidence] --> D{Permission denial?}
  D -->|yes| SK[Excluded from gaps]
  D -->|no| N[Normalize + redact question]
  N --> SG[(Fingerprint signal, no user ID)]
  SG --> G[Aggregate at >= 3]
  G --> W[Assign / status / re-evaluate]
  W --> RAG[Real RAG pipeline re-run]
```

### Evaluation, analytics and configuration

```mermaid
flowchart LR
  DS[(Synthetic dataset)] --> EV[Evaluation runner]
  PIPE[Real permission-aware pipeline] --> EV
  EV --> RES[(Metrics + case results)]
  RES --> DASH[Comparison dashboard]
  T[(Tenant tables)] --> AN[Analytics aggregates]
  AN --> ADASH[Analytics dashboard]
  TS[(Versioned tenant settings)] --> PIPE
  TS --> HIST[(Append-only history)]
```

All arrows crossing a tenant or document boundary carry the server-derived
tenant and authorization decision; the frontend is never the enforcement point.

## Trust boundaries

- Browser input, uploaded PDFs, retrieved text, user questions and model output are untrusted.
- API identity is taken from signed JWT claims and checked against current database state; tenant identity is not accepted from a RAG request body.
- `vaultrag_app` is a non-owner, `NOBYPASSRLS` role; Alembic uses a separate `vaultrag_migrator` role.
- The Docker deployment publishes only the frontend proxy on loopback. PostgreSQL and Ollama have no host port mapping.
- Local uploaded files and Ollama model state use separate persistent volumes.

See [THREAT_MODEL.md](../security/THREAT_MODEL.md) for attacker scenarios and [DEPLOYMENT_GUIDE.md](../deployment/DEPLOYMENT_GUIDE.md) for service boundaries.
