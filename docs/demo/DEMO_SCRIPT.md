# Synthetic tenant-isolation and revocation demo

## Goal and setup

Prove the invariant: a chunk that the current user is not permitted to read is not included in the context passed to the LLM. Run the local Docker stack, pull the configured Ollama model, and prepare synthetic accounts/documents only. For deterministic proof, run `tests/security/test_tenant_isolation.py` and `test_document_permissions.py`; those use PostgreSQL/pgvector and a recording LLM that captures the exact authorized context.

Create two organizations: `Finance Department` and `Human Resources Department`. Public organization registration does not create administrators. Provision Alice and Charlie through the operator command (run from `backend/` after registering each organization); it seeds the required ABAC attributes as part of trusted bootstrap:

```powershell
python -m app.scripts.bootstrap_admin `
  --tenant-id <FINANCE_TENANT_UUID> `
  --username alice `
  --email alice@example.com `
  --department FINANCE `
  --clearance-level 3

python -m app.scripts.bootstrap_admin `
  --tenant-id <HR_TENANT_UUID> `
  --username charlie `
  --email charlie@example.com `
  --department HR `
  --clearance-level 3
```

The command prompts for passwords without echoing them. Sign in as each admin and create Bob (Finance EMPLOYEE) and David (HR EMPLOYEE) with the appropriate roles and department/clearance attributes. Then upload/process these generated synthetic PDFs:

- Finance Policy: “Synthetic demo finance policy. Approved travel expenses require a receipt and manager approval.”
- Internal Expense Guidelines: “Synthetic demo guideline. Meals are capped at 40 demo credits per day.”
- Employee Leave Policy: “Synthetic demo HR policy. Employees receive 18 demo leave days each year.”
- Recruitment Guidelines: “Synthetic demo HR guide. Candidate interviews use a consistent scorecard.”
- Confidential Salary Structure: “Synthetic demo confidential record. No real salary or person data is present.”

Use distinct synthetic markers in each file, e.g. `FINANCE-ONLY-ALPHA` and `HR-ONLY-BETA`, so search results can be distinguished. Set department/classification/clearance through the admin UI. Grant Bob READ access to Finance Policy and Expense Guidelines only; grant David READ access to HR employee documents; keep the salary record admin-only.

## Live action sequence

1. Sign in as Alice, upload and process Finance Policy. Capture the document list and successful processing status.
2. Sign in as Bob and ask “What do I need for an approved travel expense?” Capture the grounded answer and Finance Policy citation.
3. Sign in as Charlie, upload/process Employee Leave Policy and Recruitment Guidelines.
4. As Bob, ask “How many leave days do employees receive?” Capture the no-source/abstention response. Try opening Charlie’s document URL using its ID; expect safe 404/denial and no content.
5. As Alice, revoke Bob’s Finance Policy READ permission.
6. As Bob, repeat the exact travel-expense query. Expect zero Finance Policy citations and a safe no-context answer immediately after revocation.
7. As an admin, open Security Monitoring and filter for document permission grant/revocation and denied access events. Capture the audit trail.

## API request examples (PowerShell)

Login with one synthetic account and retain the token in a local variable:

```powershell
$base = 'http://localhost:3000/api'
$tenantId = '<synthetic-tenant-uuid>'
$login = Invoke-RestMethod "$base/auth/login" -Method Post -ContentType 'application/json' -Body (@{ tenant_id=$tenantId; email='<synthetic-user>'; password='<synthetic-password>' } | ConvertTo-Json)
$headers = @{ Authorization = "Bearer $($login.access_token)" }
Invoke-RestMethod "$base/auth/me" -Headers $headers
Invoke-RestMethod "$base/documents" -Headers $headers
Invoke-RestMethod "$base/rag/ask" -Method Post -Headers $headers -ContentType 'application/json' -Body (@{ question='What do I need for an approved travel expense?'; top_k=10 } | ConvertTo-Json)
```

Use admin document upload form fields in the UI or follow the upload schema from `/docs` in development. Keep tenant IDs and tokens out of screenshots and source control. Production disables API docs.

## Strongest security demonstration

Run the regression suite while observing the test's recording LLM: the Finance marker may appear only when the user is authorized; the HR marker never appears in Finance user's captured LLM context. After revoking access, the identical request returns no sources and the recorder call count does not increase. This establishes access-control behavior independently of prompt-injection resistance or model output.

## Screenshot checklist

- Authorized finance answer with citation.
- HR answer as Bob with no sources.
- Finance document access before and after permission revocation.
- Audit view showing grant, revoke and denied access.
- Test output for cross-tenant context and immediate-revocation regressions.

All content and identities must remain synthetic.
