# Enterprise Integrations and Document Versions (Milestone 9)

VaultRAG can connect to an external knowledge source, synchronize its documents
into the existing permission-aware corpus, and keep an immutable version history
for every synced file. A synchronized file is **an ordinary VaultRAG document**:
it goes through the same parsing, chunking, embedding, ingestion and
authorization pipeline as an upload. An integration never widens access.

> Nothing from a document the caller cannot read may appear in a graph, a
> citation, a version list or a synchronized answer. Integration provenance is
> metadata, never an authorization input.

## What it adds

| Layer | Location |
| --- | --- |
| Connector contract, capabilities and value objects | `backend/app/integrations/base.py` |
| Provider registry | `backend/app/integrations/registry.py` |
| Credential envelope encryption (AES-256-GCM) | `backend/app/integrations/crypto.py`, `credentials.py` |
| Connection lifecycle | `backend/app/integrations/service.py` |
| Google Drive connector | `backend/app/integrations/providers/google_drive/` |
| SharePoint / Microsoft Graph connector | `backend/app/integrations/providers/sharepoint/` |
| Local-directory reference connector | `backend/app/integrations/providers/local_storage/` |
| Synchronization engine | `backend/app/services/integrations/sync.py` |
| External ACL mirroring | `backend/app/services/integrations/permissions.py` |
| Version history and rollback | `backend/app/services/integrations/versions.py` |
| OAuth authorization-code flow | `backend/app/integrations/oauth.py` |
| Webhook verification | `backend/app/integrations/webhooks.py` |
| HTTP API | `backend/app/routes/integrations.py` |
| Worker | `backend/scripts/integration_worker.py` |
| Schema | `backend/alembic/versions/d9a3f1b7c2e4_milestone9_integrations.py` |
| UI | `frontend/src/pages/integrations-page.tsx` |

## Providers

| Provider | Discovery | Download | Permissions | Versions | Changes | Webhooks | OAuth |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `local_storage` | repositories (directories), files | yes, size-capped | not declared | not declared | cursor snapshot | no | no |
| `google_drive` | My Drive + shared drives | yes, Google-native exported as PDF | Drive permissions | revisions | `changes` feed | channel notifications | yes |
| `sharepoint` | document libraries | yes | Graph permissions | item versions | drive delta | Graph subscriptions | yes |

Only PDF is ingested in this milestone. Google Docs/Sheets/Slides are exported
to PDF; DOCX/TXT are reported as unsupported instead of being silently skipped.

Adding a provider means adding a module that subclasses `Connector` and calls
`register_provider`. No route, model, sync-engine or migration change is needed.

## Storage

Nine tenant-scoped tables, all with `ENABLE` + `FORCE ROW LEVEL SECURITY` and the
shared `tenant_isolation` policy:

- `integrations` — one connection per provider per tenant;
- `integration_credentials` — encrypted credential envelope (never returned);
- `integration_repositories` — selected containers and incremental cursors;
- `external_documents` — source identity and provenance for one VaultRAG document;
- `document_versions` — immutable stored revisions with checksums;
- `integration_sync_jobs` — one active job per integration (partial unique index);
- `integration_sync_events` — de-duplicated webhook notifications;
- `integration_identity_mappings` — explicit external → internal principal map;
- `integration_oauth_states` — single-use, signed, expiring OAuth state.

`document_permissions` gains `permission_origin` and
`managed_by_integration_id`. Only rows with `permission_origin = 'EXTERNAL'` can
be revoked by a synchronization; a manual grant is never touched.

## Security guarantees

1. **Credentials at rest.** AES-256-GCM with a random 96-bit nonce and associated
   data binding the ciphertext to its tenant, integration and provider.
2. **No secret in a response.** The API exposes only whether a credential exists,
   its scopes and expiry.
3. **Deny by default.** An external principal produces a grant only through an
   active `integration_identity_mappings` row. `UNKNOWN` and `COMMENTER` roles
   never become READ.
4. **No write escalation.** Only READ is mirrored. An external WRITER does not
   gain UPDATE or DELETE inside VaultRAG.
5. **Least privilege.** The runtime role has no DELETE grant on any integration
   table or on `documents`. It may DELETE chunks because replacing a live
   revision requires it.
6. **SSRF resistance.** Provider continuation links are accepted only over HTTPS
   to `graph.microsoft.com`. The local connector refuses to point at VaultRAG's
   own private storage and ignores symlinks.
7. **Fail closed.** A missing key, expired credential, unknown provider, invalid
   cursor, oversized download or invalid webhook signature produces a controlled
   error, never partial trust.

See [INTEGRATION_SECURITY_MODEL.md](INTEGRATION_SECURITY_MODEL.md),
[DOCUMENT_VERSIONING.md](DOCUMENT_VERSIONING.md) and
[SYNC_OPERATIONS.md](SYNC_OPERATIONS.md).

## API

All endpoints require `MANAGE_INTEGRATIONS` (ADMIN-only) except where noted.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/integrations/providers` | Registered providers and capabilities |
| `GET` | `/integrations` | Tenant connections |
| `POST` | `/integrations` | Create a connection |
| `GET` | `/integrations/{id}` | One connection (no secrets) |
| `PATCH` | `/integrations/{id}` | Rename, permissions mode, interval, enable |
| `DELETE` | `/integrations/{id}` | Disconnect and revoke credentials |
| `POST` | `/integrations/{id}/credentials` | Store an encrypted credential payload |
| `POST` | `/integrations/{id}/connect` | Validate the credential |
| `POST` | `/integrations/{id}/oauth/start` | Begin an OAuth authorization |
| `GET` | `/integrations/oauth/callback` | Public, signed single-use callback |
| `GET` | `/integrations/{id}/repositories?discover=` | List/discover containers |
| `PUT` | `/integrations/{id}/repositories/{rid}` | Select a container and sync mode |
| `POST` | `/integrations/{id}/sync` | Queue and run one synchronization |
| `GET` | `/integrations/{id}/jobs` | Recent runs |
| `POST` | `/integrations/{id}/jobs/{jid}/cancel` | Request cancellation |
| `GET` | `/integrations/{id}/documents` | External document provenance |
| `GET` | `/integrations/{id}/identities` | Identity mappings |
| `POST` | `/integrations/{id}/identities` | Create an explicit mapping |
| `DELETE` | `/integrations/{id}/identities/{mid}` | Deactivate a mapping |
| `POST` | `/integrations/webhooks/{id}?token=` | Public provider notification |
| `GET` | `/documents/{id}/versions` | Version history (document READ) |
| `POST` | `/documents/{id}/versions/{vid}/restore` | Re-ingest a stored revision |

## Local walkthrough

```powershell
cd backend
alembic upgrade head

# 1. Create a local connection (ADMIN token)
Invoke-RestMethod -Method Post http://127.0.0.1:8000/integrations `
  -Headers @{ Authorization = "Bearer $TOKEN" } -ContentType application/json `
  -Body '{"provider":"local_storage","integration_name":"Local policies",
          "configuration":{"root":"D:\\shared","department":"ENGINEERING"}}'

# 2. Validate, discover, select a repository, synchronize
Invoke-RestMethod -Method Post http://127.0.0.1:8000/integrations/$ID/connect -Headers $H
Invoke-RestMethod http://127.0.0.1:8000/integrations/$ID/repositories -Headers $H
Invoke-RestMethod -Method Put http://127.0.0.1:8000/integrations/$ID/repositories/$RID `
  -Headers $H -ContentType application/json -Body '{"selected":true,"sync_mode":"FULL"}'
Invoke-RestMethod -Method Post http://127.0.0.1:8000/integrations/$ID/sync `
  -Headers $H -ContentType application/json -Body '{"sync_type":"FULL"}'
```

Run the per-tenant worker for scheduled and webhook-queued work:

```powershell
cd backend
python scripts/integration_worker.py --tenant-id <tenant-uuid>
```

The UI is at **Administration → Integrations**.

## Tests

```powershell
cd backend
pytest app/tests/integrations -q
```

The suite uses the local reference connector, deterministic fake embeddings and
a mocked provider transport. It proves schema/RLS guarantees, credential
encryption, connector normalization, a full sync, idempotent re-sync, version
creation and rollback, tenant isolation and deny-by-default ACL mapping. No
Google, Microsoft or network credentials are required.
