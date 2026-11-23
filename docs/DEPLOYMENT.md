# Deployment

Detailed instructions live in
[deployment/DEPLOYMENT_GUIDE.md](deployment/DEPLOYMENT_GUIDE.md) and the
[production checklist](deployment/PRODUCTION_CHECKLIST.md). This page summarizes
the required operational surface.

## Prerequisites

- Docker Desktop / Docker Engine with Compose v2.
- A `.env` derived from `.env.example` with strong PostgreSQL, application and
  JWT secrets.
- For generation: Ollama with the configured model, or a supported LLM
  configuration. Embeddings run locally through the configured model.
- Private persistent storage for uploaded documents.

## Services

`db`, `migrate`, `api`, `web` and optional `ollama`. The `workers` profile adds
`graph-worker` and `integration-worker`; they wait for migrations, expose no host
ports and mount the private document volume. Local ports bind to loopback.

## Environment

Copy `.env.example`; never commit `.env`. Required variables cover PostgreSQL,
the migrator/runtime roles, JWT, CORS/origins, document storage, Ollama/model
names and provider credentials used by enabled integrations. Provider OAuth
secrets belong in a secret manager in production.

## Health

- `/health/live` — process liveness.
- `/health/ready` — database, pgvector, full-text column and configuration;
  returns no secrets.
- `/health` — backward-compatible aggregate.

Use readiness for routing; use liveness for restarts.

## Backup and restore

`scripts/backup.ps1` (PowerShell) and `scripts/backup.sh` (Bash) create a
custom-format `pg_dump`, verify it with `pg_restore --list`, and archive the
document volume. Output is Git-ignored. Test restore into an isolated database,
verify migration state (`alembic current`) and sample document hashes. The
scripts do not restore automatically; this is deliberate.

## Troubleshooting

- API unhealthy: `docker compose logs migrate api db`; validate `.env` and DB
  bootstrap.
- Readiness failing: check PostgreSQL, `vector` extension, `content_tsv`
  column and embedding configuration.
- No graph/integration progress: start the `workers` profile and inspect job
  status and logs.
- Windows path issues: run scripts from the repository root.

## Shutdown and recovery

`docker compose down` stops services but preserves volumes. Never delete volumes
holding documents or the database without a verified backup. On recovery, start
`db`, let `migrate` finish, then start `api`/`web` and workers; verify readiness
and a sample authorized query before restoring traffic.
