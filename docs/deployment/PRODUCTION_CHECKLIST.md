# Production readiness checklist

- [ ] Generate unique database and JWT secrets; store and rotate them in a secret manager.
- [ ] Set `ENVIRONMENT=production`, exact `ALLOWED_HOSTS`, exact HTTPS `CORS_ORIGINS`, `SQL_ECHO=false`, and `vaultrag_app` runtime DB URL.
- [ ] Run Alembic only with `vaultrag_migrator`; keep this credential out of the API runtime environment where deployment tooling allows.
- [ ] Keep the API behind TLS reverse proxy; enable HSTS only when all relevant domains are HTTPS.
- [ ] Keep Postgres and Ollama private; publish no direct host ports.
- [ ] Configure shared/distributed rate limiting and quotas for multi-replica deployment; local Nginx limiter is per proxy instance/IP.
- [ ] Set database pool limits against the DB connection budget; configure upstream and model timeouts.
- [ ] Encrypt database and document backups; test a restore and key recovery.
- [ ] Configure centralized, access-controlled audit/log retention and security alerts; avoid logging prompts or document text.
- [ ] Run Ruff, Bandit, pip-audit, npm audit, Gitleaks and Trivy; resolve or explicitly assess each high/critical finding.
- [ ] Run PostgreSQL RLS integration tests and the synthetic cross-tenant/revocation demonstration against the release candidate.
- [ ] Run load tests on declared hardware, record p95/p99, failures, resource usage and model token speed.
- [ ] Review dependency/container image updates, pin released images by digest and maintain rollback procedures.
- [ ] Conduct independent penetration test and privacy/compliance review appropriate to the actual data and jurisdiction.
- [ ] Decide MFA, token revocation/rotation, browser token storage and incident-response requirements before handling high-risk data.

VaultRAG has not been independently audited or certified. Passing CI is not equivalent to production approval.
