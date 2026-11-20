# Integration Security Model

This document describes the controls that protect external provider credentials,
synchronized content and authorization state. It complements
[`../SECURITY.md`](../SECURITY.md) and the general threat model.

## 1. Credential confidentiality

- Provider credentials are encrypted with **AES-256-GCM** under
  `INTEGRATION_TOKEN_ENCRYPTION_KEY`, a base64url 32-byte deployment key.
- A fresh 96-bit random nonce is used for every write. The nonce and
  `key_version` are stored beside the ciphertext.
- Associated data is
  `vaultrag.integration:<tenant_id>:<integration_id>:<provider>`. A ciphertext
  copied to another tenant, integration or provider cannot be decrypted even by
  someone with database write access.
- The plaintext is never logged, returned by an API, written to an audit row or
  placed in an error message. `decrypt_secret` maps every failure to one safe
  error, so a wrong key and a tampered tag are indistinguishable to a caller.
- Disconnecting an integration overwrites the ciphertext and marks the row
  revoked; the audit trail is retained because the runtime role cannot DELETE.
- `key_version` is stored for future rotation. Rotating the key requires
  re-entering credentials or a controlled re-wrap job; the current worker does
  not rotate keys automatically.

Generate a key once per deployment and keep it in the platform secret store:

```bash
python -c "import base64,os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
```

Losing the key makes stored credentials unreadable and forces every provider to
be reconnected. It does not affect documents already ingested.

## 2. OAuth

The authorization-code flow uses PKCE and a signed, single-use state value:

1. `POST /integrations/{id}/oauth/start` requires ADMIN. It creates an
   `integration_oauth_states` row containing a SHA-256 of the state, the exact
   redirect URI and the **encrypted** PKCE verifier.
2. The state sent to the provider is
   `<state_id>:<tenant>:<integration>:<expiry>:<HMAC-SHA256>`, signed with the
   deployment secret. The callback derives the tenant only from that signature.
3. `GET /integrations/oauth/callback` is public. It validates the MAC, expiry and
   unconsumed state hash, marks the state consumed, decrypts the verifier and
   exchanges the code. Every failure is a safe 400.
4. Access and refresh tokens are stored encrypted. The browser never receives
   them.
5. `INTEGRATION_OAUTH_REDIRECT_BASE` must be an exact configured origin; it is
   never derived from a request `Host` header.

## 3. Webhooks

`POST /integrations/webhooks/{id}` is public by necessity. It requires:

- a URL token `HMAC(tenant:integration)` signing the tenant binding;
- a provider-specific proof: `X-Goog-Channel-Token` equal to the stored
  `webhook_secret`, or a Microsoft `clientState` that matches on every event;
- a bounded body size;
- de-duplication on `(tenant, integration, external_event_id)`.

An integration without a stored webhook secret rejects every notification. The
endpoint can only queue a synchronization; it cannot read or return document
content.

## 4. Authorization and tenancy

- Every integration table has `ENABLE` + `FORCE ROW LEVEL SECURITY` and the
  shared fail-closed `tenant_isolation` policy.
- `vaultrag_app` owns no tables and is `NOSUPERUSER`/`NOBYPASSRLS`.
- The runtime role has `SELECT, INSERT, UPDATE` on integration tables. It has no
  DELETE grant, so history cannot be destroyed through the API.
- `document_permissions` is the only place an external ACL lands. Rows created
  by synchronization always have `permission_origin = 'EXTERNAL'` and
  `managed_by_integration_id`; a check constraint rejects an external grant
  without an owner.
- Revocation only updates rows whose `managed_by_integration_id` matches. A
  manual grant to the same principal is untouched.
- Synchronized documents are created with `uploaded_by` = the integration
  creator and pass through the same ABAC/clearance checks as an upload.

## 5. Deny-by-default permission mapping

`EXTERNAL_MIRROR` mode maps an external grant only when an active
`integration_identity_mappings` row matches
`(external_principal_type, lower(external_principal_id))`.

- No email-domain matching, display-name matching or automatic user creation.
- `USER` maps to a tenant user, `GROUP`/`ROLE` map to a tenant role.
- `DOMAIN` and `ANYONE` never resolve automatically.
- `UNKNOWN` and `COMMENTER` roles never grant READ.
- Only READ is mirrored, so an external writer cannot edit or delete the
  VaultRAG document.
- A document with no resolved principal stays `UNRESOLVED` and is readable only
  through VaultRAG-managed grants (ADMIN/department).

## 6. Content handling

- Download size is enforced by the connector while receiving bytes; a provider
  `Content-Length` is not trusted.
- The SHA-256 of every downloaded payload is recomputed locally and becomes the
  document's `file_hash` and version checksum.
- Files are written to a private temporary file, copied into generated storage
  keys, and the temporary file is removed in a `finally` block.
- The local connector refuses to point at the private document root, ignores
  symlinks, and rejects `.`/`..`/absolute identifiers.
- Graph continuation links must be HTTPS on `graph.microsoft.com`.
- A failed download or ingestion never leaves a `COMPLETED` document with a
  partial chunk set: the existing ingestion pipeline records FAILED.

## 7. Auditing

Integration actions are recorded in `security_audit_logs` under:

- `INTEGRATION_MANAGEMENT` — create, update, connect, disconnect, credentials,
  OAuth, repositories, identity mappings;
- `INTEGRATION_SYNCHRONIZATION` — queue, completion and version restore;
- `INTEGRATION_PERMISSION_SYNC` — grants and revocations applied from an ACL.

Reasons are counts and statuses. They never contain a token, a principal email,
a document name or chunk text.

## 8. Remaining risks

- A compromised deployment encryption key exposes stored refresh tokens. Key
  custody and rotation are operator responsibilities.
- Provider APIs are trusted for document identity and ACL input; a malicious
  provider response can only affect the tenant that connected it.
- `ADMIN_MAPPED` and `VAULTRAG_MANAGED` modes do not refresh ACLs at all; this is
  deliberate, not a partial mirror.
- The local reference connector has no ACL model by design.
- Rate limiting and outbound egress policy are deployment concerns.
