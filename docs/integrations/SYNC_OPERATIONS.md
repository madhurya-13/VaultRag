# Integration Sync Operations

## Configuration

All settings live in `backend/app/core/config.py` and are environment-driven.

| Setting | Default | Meaning |
| --- | --- | --- |
| `INTEGRATIONS_ENABLED` | `true` | Master switch for the integration surface |
| `INTEGRATION_TOKEN_ENCRYPTION_KEY` | unset | Base64url 32-byte AES-256-GCM key |
| `INTEGRATION_OAUTH_REDIRECT_BASE` | unset | Exact public API origin for OAuth and webhook URLs |
| `INTEGRATION_OAUTH_STATE_TTL_SECONDS` | `600` | Signed state lifetime |
| `INTEGRATION_HTTP_TIMEOUT_SECONDS` | `20` | Provider read timeout |
| `INTEGRATION_HTTP_CONNECT_TIMEOUT_SECONDS` | `5` | Provider connect timeout |
| `INTEGRATION_MAX_DOWNLOAD_BYTES` | `26214400` | Hard per-file ceiling |
| `INTEGRATION_WEBHOOK_MAX_BODY_BYTES` | `65536` | Inbound notification ceiling |
| `INTEGRATION_SYNC_WORKER_POLL_SECONDS` | `5` | Idle poll interval |
| `INTEGRATION_SYNC_MAX_ATTEMPTS` | `3` | Stored attempt ceiling |
| `INTEGRATION_SYNC_BACKOFF_BASE_SECONDS` | `30` | Retry backoff seed |
| `INTEGRATION_SYNC_JOB_STALE_SECONDS` | `1800` | Reclaim window for a stuck run |
| `INTEGRATION_SYNC_DEFAULT_INTERVAL_MINUTES` | `60` | Default schedule |

`INTEGRATION_MAX_DOWNLOAD_BYTES` is enforced by the connector as it receives
bytes, not from a `Content-Length` header.

## Migration

```bash
cd backend
alembic upgrade head
```

Revision `d9a3f1b7c2e4` creates the nine integration tables, their RLS policies,
the external-permission columns on `document_permissions`, and grants
`DELETE ON document_chunks` to the runtime role. Downgrade removes only
Milestone 9 objects.

## Worker

Synchronization runs outside the request/response path. Run one worker per
tenant:

```bash
cd backend
python scripts/integration_worker.py --tenant-id <tenant-uuid>
```

- The worker sets the RLS tenant context on every transaction and never uses a
  `BYPASSRLS` role.
- Each iteration schedules due integrations, then runs queued jobs one at a
  time.
- `--once` drains the queue and exits (backfills and tests).
- `SIGINT`/`SIGTERM` finish the current job before stopping.
- One active job per integration is enforced by the partial unique index
  `uq_integration_sync_jobs_active`, so a second worker cannot duplicate work.

`POST /integrations/{id}/sync` queues and runs a job synchronously and returns
the final job row. Both paths use the same engine.

## Discovery and cursors

- **Full** — enumerate every container with `list_files`, download and compare
  checksums. No cursor is required; a stored cursor is kept.
- **Incremental** — use `detect_changes` from the repository cursor. A first
  incremental run without a cursor falls back to a full listing, then stores the
  cursor returned by the provider.
- Cursors are opaque, bounded by the provider or the local snapshot limit, and
  never treated as authorization input.
- Deletions reported by a delta mark the external document `DELETED` and archive
  the live VaultRAG document (`is_active = false`, status `ARCHIVED`).

## Job states

`PENDING → DISCOVERING → DOWNLOADING → PROCESSING → COMPLETED` with
`PARTIALLY_COMPLETED` when some files fail and `FAILED` when nothing succeeds or
the provider rejects the credential. `CANCELLED` is cooperative: the engine
checks `cancel_requested` before it starts.

Counter meanings are literal: `files_discovered` is files enumerated,
`files_processed` includes unchanged files, `versions_created` counts new
revisions, `files_failed` counts per-file failures, and `documents_deleted`
counts archived documents. Nothing is incremented speculatively.

## Failure handling

- Provider authentication failure marks the integration `REAUTH_REQUIRED` and
  fails the job with a safe error code.
- A single file failure never aborts the run; the file is marked `FAILED` and the
  remaining files continue.
- Retry backoff is recorded in `next_attempt_at`; the worker creates a new job for
  the next due run rather than mutating a finished one.
- A run with no selected repository fails with `INTEGRATION_NO_REPOSITORIES`
  instead of pretending success.
- Stale `PROCESSING` rows older than `INTEGRATION_SYNC_JOB_STALE_SECONDS` can be
  reclaimed by re-queueing at the operator level; there is no destructive
  cleanup.

## Monitoring

- `GET /integrations/{id}/jobs` (ADMIN) shows counts and safe error codes.
- `GET /integrations/{id}/documents` shows per-document sync and permission
  state.
- Useful log lines: `Integration file sync failed ...`,
  `Integration sync failed ...`, `Integration worker iteration failed`.
- Audit actions: `INTEGRATION_SYNC_QUEUED`, `INTEGRATION_SYNC_COMPLETE`,
  `INTEGRATION_PERMISSION_SYNC`, `INTEGRATION_WEBHOOK_REJECTED`.

No log line or analytics row contains a token, a principal email, a document
name or chunk text.

## Troubleshooting

**`INTEGRATION_NOT_CONFIGURED`** — `INTEGRATIONS_ENABLED` is false, or no
credential is stored for a provider that needs one.

**`Stored provider credentials could not be decrypted`** — the deployment key
changed or the row was copied between integrations. Re-enter the credential.

**`INTEGRATION_SYNC_ALREADY_RUNNING`** — a job is active for this integration.
Wait for it or cancel it.

**Jobs stay `PENDING`** — no worker is running for that tenant, or the tenant id
is wrong (RLS hides another tenant's rows).

**A document is `FAILED`** — the provider file is not a text-based PDF, is
encrypted, exceeds the page limit or failed extraction. The source file, not the
integration, is usually the cause. Unsupported MIME types are counted as file
failures and never ingested.

**`graph_hybrid` sees a document but graph mode does not** — graph mode uses only
graph evidence; extraction may still be queued or may have skipped the document.

**`INTEGRATION_UNSAFE_PATH`** — a provider returned an identifier that escapes
the configured root or a continuation link off `graph.microsoft.com`. Treat it as
a provider bug and do not weaken the check.
