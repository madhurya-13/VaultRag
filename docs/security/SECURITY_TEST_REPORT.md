# Security test report

## Coverage

The security suite uses PostgreSQL `vaultrag_test`, the real Alembic migrations, pgvector and the restricted `vaultrag_app` runtime role. The fixture refuses non-test database names. The deterministic recording LLM captures exactly what would have been sent to a model.

| Attack family | Regression coverage |
|---|---|
| Cross-tenant retrieval, tenant tampering, guessed document IDs | `test_tenant_isolation.py`, `test_rag_pipeline.py`, `test_rag_security.py` |
| Immediate permission revocation | `test_permission_revocation.py`, `test_rag_security.py`, and combined M6 proof below |
| Role escalation and forged role claims | `test_role_escalation.py`, backend RBAC/auth tests |
| Prompt injection and citation validation | `test_prompt_injection.py`, backend RAG pipeline tests |
| Invalid, encrypted, misleading-extension, path-traversal, oversized and excessive-page uploads | `test_document_upload_security.py`, backend ingestion/upload tests |
| Authentication/API validation and SQL injection payloads | `test_api_security.py`, backend authentication/API tests |
| Frontend model-output XSS | `test_frontend_security.py` and rendered Vitest regression |
| RLS and connection/tenant isolation | backend `test_rls.py`, `test_rag_security.py`, tenant suite |

## Execution results

- Dedicated M6 attack suite: **37 passed** in 25.33 seconds against the local PostgreSQL/pgvector test database.
- Full backend suite: **194 passed, 2 skipped** in 118.64 seconds. The real embedding-model integration and real Ollama integration remain opt-in.
- Frontend Vitest: **23 passed**. Ruff, TypeScript checks and the production Vite build passed; the build reports a >500 kB chunk warning.
- Database role audit: the integration suite verified `vaultrag_app` and `vaultrag_migrator` are non-superuser, non-`BYPASSRLS` roles and that FORCE RLS policies and least-privilege grants are active.
- Bandit: no medium/high findings; three low findings remain recorded in the checklist.
- pip-audit: no known Python dependency vulnerabilities. npm audit: zero high-severity frontend vulnerabilities.
- Gitleaks and Trivy are configured in workflow files but were not run locally; their release status remains pending until CI executes them.

## Central proof executed

`tests/security/test_tenant_isolation.py::test_two_tenant_retrieval_then_immediate_permission_revocation` creates synthetic Finance and HR tenants, uploads a distinct marker to each, and performs the same Finance-user query before and after revocation. The Finance marker reaches the recording LLM while the HR marker never does. A guessed HR document ID returns 404; a client-supplied tenant ID is rejected. After revocation, the repeated query returns no sources and the LLM call count does not increase. This proof passed locally as part of the 37-test suite.

## Run from PowerShell

```powershell
docker compose -p vaultrag-m6 -f docker-compose.yml -f docker-compose.test.yml --env-file .env.m6.local run --rm --no-deps security-tests
```

The fixture drops/recreates schemas and truncates only the database named `vaultrag_test`; it must never target production or another data-bearing database.

## Limits

This test report is not a penetration test, certification, or proof against every attack. Prompt-injection defenses do not replace authorization. The recording LLM establishes what is passed across the model boundary, but does not assess model behavior. Gitleaks, Trivy and remote CI findings remain unknown until those jobs run.
