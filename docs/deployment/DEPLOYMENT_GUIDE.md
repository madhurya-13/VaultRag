# Deployment guide

## Local demonstration (Docker Compose)

Requirements: Docker Desktop with Linux containers, 8 GB or more available RAM recommended, free space for model weights, and network access to download the images and selected Ollama model. PowerShell:

```powershell
Copy-Item .env.example .env
# Generate four independent values and paste them into their matching entries.
1..4 | ForEach-Object { python -c "import secrets; print(secrets.token_hex(32))" }
docker compose config
docker compose up --build -d
docker compose ps
docker compose exec ollama ollama pull qwen2.5:3b
Invoke-WebRequest http://127.0.0.1:3000/healthz
Invoke-WebRequest http://127.0.0.1:3000/api/health
```

Generate each secret independently and review `.env` before starting. The sample PowerShell replacement loop can be run repeatedly, but for best results generate and set each value independently with a password manager. The runtime URL requires alphanumeric DB passwords to avoid URL escaping problems. The Compose stack migrates before the API starts. The UI is available at `http://localhost:3000`.

Ollama is not mapped to a host port or public interface. It has a separate outbound-capable network so `ollama pull` can fetch weights; only the API and Ollama share the private application network. Model weights persist in `vaultrag_ollama`. Documents and the database persist in separate named volumes. The Compose DB volume initialization runs only on a fresh volume; if changing bootstrap credentials after initialization, update database roles deliberately rather than deleting volumes.

The Nginx service rate-limits login and API requests and caps uploads. Add an HTTPS reverse proxy or TLS load balancer before making this service reachable beyond the local machine. The included bind is loopback-only.

## Student project demonstration

Use synthetic Finance and HR organizations, synthetic PDF text, and four demo identities. Follow [DEMO_SCRIPT.md](../demo/DEMO_SCRIPT.md). Do not put real personal or company data into this instance. Use the included local Ollama model to avoid sending documents to an external model provider.

## Production-style deployment

Use a TLS-terminating reverse proxy, private network placement, managed PostgreSQL with pgvector/RLS-compatible role behavior, private persistent object/file storage, a dedicated compute host for Ollama, secret manager, encrypted backups and tested restore, central immutable audit logs, monitoring and alerting. Restrict DB access to migrations and API networks. Keep Ollama private and size its CPU/GPU/RAM against measured concurrency. The repository Compose file is a local deployment baseline, not a ready-made public cloud deployment. No cloud resources are created by this project work.

### Backup / restore

Schedule encrypted `pg_dump` backups of the application database and separate consistent backups of the private document volume. Store keys separately from backups, test restore into an isolated environment, verify migration state and sample file hashes, and document RPO/RTO. Model weights can be re-downloaded. Do not restore untrusted database dumps into a networked production environment without review.

## Common troubleshooting

- Docker pipe unavailable: start Docker Desktop Linux engine, then retry `docker info` and `docker compose config`.
- API does not become healthy: inspect `docker compose logs migrate api db`; verify `.env` and PostgreSQL bootstrap output.
- Ollama responds but RAG returns unavailable: `docker compose exec ollama ollama list`, then pull the configured model and check API logs.
- Browser API error: verify frontend `/api` proxy and `PUBLIC_ORIGIN`; inspect `docker compose logs web api`.
- DB role/password mismatch after an initialized volume: reset the role with superuser administration. Do not remove a volume containing needed data.
