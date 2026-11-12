# VaultRAG threat model (STRIDE)

## Assets

Tenant records and identifiers; user credentials and access tokens; document bytes, extracted text, embeddings and metadata; permission and ABAC state; prompts and answers; audit events; database credentials, signing key and Ollama endpoint.

## Actors and attack surfaces

Actors include a normal tenant user, a malicious tenant administrator, a malicious employee, a user controlling an uploaded PDF, an unauthenticated internet client, a compromised dependency/container, and an operator with database/host access. Surfaces include login/registration, JSON APIs, PDF upload and processing, document permissions, RAG query/context assembly, the React browser, PostgreSQL pooled sessions, migrations, Nginx, Docker networks and Ollama.

## Trust assumptions / boundaries

The network-facing browser is untrusted. API requests become trusted only after signature and claim validation plus a fresh database lookup. Tenant-scoped SQL is additionally constrained by PostgreSQL FORCE RLS and transaction-local `app.current_tenant_id`. The API role does not own tenant tables and cannot bypass RLS. Migration and host operators remain privileged. Ollama is trusted as a local inference service but receives only already-authorized context. Uploaded content and model output remain untrusted.

## STRIDE analysis

| STRIDE | Scenario | Existing mitigation / evidence | Remaining concern |
|---|---|---|---|
| Spoofing | Forged, expired, wrong-issuer/audience, or cross-tenant JWT | HS allow-list, signature/exp/iat/iss/aud checks, DB identity and active status on each request; `test_auth.py`, M6 API suite | Access-token revocation is through account/role state; no individual token denylist or refresh rotation |
| Tampering | Client changes tenant, roles, classification, or document path | Strict request schemas, server-derived metadata, random private storage keys, filesystem containment; tenant/ingestion/upload tests | Continue fuzzing every new schema and file processor change |
| Repudiation | Admin denies granting/revoking access | Append-only audit table, tenant-scoped audit events, request correlation ID | Audit retention/export and host log integrity need operator controls |
| Information disclosure | Tenant B chunks appear in tenant A answer/context | RLS + retrieval filters + fresh permission check before context builder; tests assert marker absent from recording LLM | The dedicated PostgreSQL role and actual production proxy must be preserved; never run API as owner/superuser |
| Denial of service | Brute force, oversized uploads, expensive embedding/LLM requests | Upload size and PDF validation limits, bounded top-k/question, timeouts, Nginx auth/API rate limits and connection cap | Limits are per proxy IP; distributed deployments need a shared limiter/WAF and capacity-based quotas |
| Elevation of privilege | Employee forges admin role in JWT/API/UI | Roles reloaded from database; admin capability dependencies; registration rejects role/activation mass assignment | Browser localStorage permits token theft after XSS; no MFA today |

## Critical invariant

For every RAG request, unauthorized chunks must not be selected into the context passed to the LLM. Revocation must be reflected by the next query. This is enforced independently of model prompt behavior. `backend/app/tests/test_rag_pipeline.py` and `backend/app/tests/test_rag_security.py` use real PostgreSQL/pgvector integration plus a recording LLM to verify that invariant.

## Gaps and planned controls

- Use HTTPS at the public edge; local Compose binds to loopback and is not a public TLS terminator.
- Keep secrets in a deployment secret manager, rotate on compromise, and use unique production values.
- Add shared rate limiting, MFA, token revocation/rotation and stronger browser token isolation before high-risk production use.
- Back up encrypted database and document volumes; test restore and key recovery.
- Pin and scan base images/dependencies continuously; review scanner findings rather than treating a clean run as certification.
- PyTorch is pinned to 2.15.0, the upstream fixed release for the `weights_only` unpickler advisory; do not load user-supplied checkpoints.
- Production deployment, penetration testing, privacy review and legal/regulatory review are outside this repository verification.
