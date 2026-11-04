# VaultRAG — Permission-Aware Multi-Tenant RAG System

> **Milestone 1: Secure Backend and Database Foundation**

Multi-tenant platform where organizations share one application while users,
roles and data remain isolated.

This repository contains **only Milestone 1**. RAG, embeddings, vector search,
LLM, audit logging, ABAC, PostgreSQL RLS and the frontend are not implemented yet.

Detailed security design: [docs/SECURITY.md](docs/SECURITY.md)

---

## Included

- FastAPI, SQLAlchemy 2.0, PostgreSQL 16, Alembic
- Organization registration + roles `ADMIN`, `HR`, `FINANCE`, `ENGINEERING`
- Argon2id password hashing
- JWT login with configurable expiry
- Authentication context reloaded from the database on every request
- Admin-only, tenant-scoped user management
- Cross-tenant isolation via query filters **and** composite database FKs
- 31 automated security tests

---

## Quick start (Windows PowerShell)

```powershell
cd C:\Users\Madhurya\OneDrive\Desktop\VaultRag
python -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
pip install -r backend\requirements.txt

docker compose up -d db

cd backend
Copy-Item .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(64))"
# Paste the output into JWT_SECRET_KEY in backend\.env

alembic upgrade head
uvicorn app.main:app --reload
```

- Swagger: <http://127.0.0.1:8000/docs>
- Health: <http://127.0.0.1:8000/health>

Run tests in a second terminal:

```powershell
cd C:\Users\Madhurya\OneDrive\Desktop\VaultRag\backend
pytest -q
```

Expected: `31 passed`.

---

## First administrator

There is no public endpoint that creates an admin.

```powershell
$org = Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8000/organizations/register" `
  -ContentType "application/json" `
  -Body (@{ organization_name = "TechNova" } | ConvertTo-Json)

python -m app.scripts.bootstrap_admin `
  --tenant-id $org.tenant_id `
  --username admin `
  --email admin@technova.com
```

The CLI prompts for the password securely. Public signup creates an **inactive,
role-less** account; an admin must activate it and assign a role.

---

## API

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `POST` | `/organizations/register` | Public | Create tenant + default roles |
| `GET` | `/organizations/me` | Bearer | Current tenant |
| `POST` | `/auth/register` | Public | Pending signup |
| `POST` | `/auth/login` | Public | Email + password + tenant → JWT |
| `GET` | `/auth/me` | Bearer | Current identity + roles |
| `GET` | `/users` | ADMIN | List users in own tenant |
| `POST` | `/users` | ADMIN | Create user + assign roles |
| `GET` | `/users/{id}` | ADMIN | One user in own tenant |
| `POST` | `/users/{id}/roles` | ADMIN | Assign role |
| `DELETE` | `/users/{id}/roles/{role}` | ADMIN | Revoke role |
| `PATCH` | `/users/{id}/status` | ADMIN | Activate/deactivate |
| `GET` | `/health` | Public | Health check |

Login body:

```json
{
  "tenant_id": "<tenant-uuid>",
  "email": "admin@technova.com",
  "password": "SecurePassword123!"
}
```

JWT claims: `sub`, `tenant_id`, `roles`, `type`, `iat`, `exp`, `jti`.
In Swagger, click **Authorize** and paste the token.

---

## Security essentials

- The JWT identifies the user; it is not the authority. User, tenant and roles
  are reloaded from PostgreSQL on every request, so revocation is immediate.
- Tenant ID comes only from the authenticated database record — never from a
  query parameter, body or custom header.
- `user_roles` uses a shared `tenant_id` with composite FKs to `users` and
  `roles`, so cross-tenant role links are rejected by PostgreSQL itself.
- Public signup cannot choose roles or activate itself. The first admin is
  provisioned by an operator CLI.
- Passwords use Argon2id; login errors are generic to limit enumeration.
- RLS is **not enabled**. Milestone 1 uses application filtering + database
  constraints; the schema is ready for RLS in Milestone 2.

---

## Limitations and next steps

Not implemented: refresh tokens, email verification, password reset, MFA,
rate limiting, audit logging, ABAC, RLS, document ingestion, embeddings, vector
search and RAG.

**Milestone 2:** enable PostgreSQL RLS with a non-owner role and
`SET LOCAL app.tenant_id`, then add audit logging and permission-aware retrieval.

Never commit `backend/.env` or real secrets. This is an educational project
following production patterns, but it has not been independently security-audited.
