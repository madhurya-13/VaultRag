# VaultRAG — Permission-Aware Multi-Tenant RAG System

> **Milestone 1: Secure Backend and Database Foundation**

VaultRAG is a multi-tenant platform where organizations share one application
while their users, roles and data remain isolated.

This repository contains **only Milestone 1**. The RAG pipeline, embeddings,
vector search, LLM, audit logging, ABAC, PostgreSQL RLS and frontend are **not**
implemented yet.

---

## What is implemented

- FastAPI + SQLAlchemy 2.0 + PostgreSQL 16 + Alembic
- Organization registration with default roles: `ADMIN`, `HR`, `FINANCE`, `ENGINEERING`
- User registration and admin-controlled approval
- Argon2id password hashing
- JWT login with configurable expiry
- Authentication dependency that reloads user, tenant and roles from the database
- Tenant-scoped user administration (`ADMIN` only)
- Cross-tenant isolation enforced in application queries **and** by database constraints
- 31 automated security tests

---

## Architecture

```
API client / Swagger
      │  Authorization: Bearer <JWT>
      ▼
FastAPI
  routes/          HTTP validation and responses
  dependencies/    JWT → trusted AuthenticatedContext
  services/        business rules and tenant scoping
  models/          SQLAlchemy mappings
      │
      ▼
PostgreSQL
  organizations → roles
  organizations → users
  users ↔ roles via user_roles (composite tenant-safe FKs)
```

The JWT identifies the user. It is **not** the source of authority: every
protected request reloads the user, tenant and current roles from PostgreSQL.
Role revocation and user deactivation therefore take effect immediately.

### Tenant isolation

1. **Authenticated context** — `tenant_id` comes from the database user record.
2. **Application filters** — every tenant-sensitive query filters on that tenant.
3. **Database constraints** — `user_roles` uses a single `tenant_id` with:

```sql
FOREIGN KEY (tenant_id, user_id) REFERENCES users (tenant_id, user_id)
FOREIGN KEY (tenant_id, role_id) REFERENCES roles  (tenant_id, role_id)
```

A user from tenant A cannot be linked to a role from tenant B, even by a buggy
script or manual SQL.

**RLS is not implemented yet.** Milestone 1 uses application-level filtering
plus database integrity constraints. The schema is ready for PostgreSQL RLS in
Milestone 2 (every tenant-owned table has a `NOT NULL tenant_id`).

---

## Project structure

```
backend/
  app/
    core/           config, database, security, passwords, exceptions
    models/         organization, user, role, user_role
    schemas/        Pydantic request/response models
    routes/         auth, organizations, users
    dependencies/   authentication + ADMIN guard
    services/       business logic reused by API, CLI and tests
    scripts/        bootstrap_admin.py
    tests/          pytest security suite
  alembic/          migration environment
  requirements.txt
  .env.example
database/init.sql   creates primary + test databases (RLS notes included)
docker-compose.yml  local PostgreSQL 16
README.md
```

---

## Setup on Windows (PowerShell)

### 1. Virtual environment and dependencies

```powershell
cd C:\Users\Madhurya\OneDrive\Desktop\VaultRag
python -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r backend\requirements.txt
```

### 2. Start PostgreSQL

```powershell
docker compose up -d db
docker compose ps
```

The container listens only on `127.0.0.1:5432`. On first start it creates the
`vaultrag` and `vaultrag_test` databases.

### 3. Configure environment variables

```powershell
cd backend
Copy-Item .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

Paste the generated value into `JWT_SECRET_KEY` in `backend\.env`.
Never commit `backend\.env`.

### 4. Apply migrations

```powershell
alembic upgrade head
alembic check
```

`alembic check` should report no pending operations.

### 5. Run the API

```powershell
uvicorn app.main:app --reload
```

- Swagger: <http://127.0.0.1:8000/docs>
- Health: <http://127.0.0.1:8000/health>

### 6. Run tests

In a second terminal:

```powershell
cd C:\Users\Madhurya\OneDrive\Desktop\VaultRag\backend
pytest -q
```

Expected: `31 passed`. Tests use `vaultrag_test` and truncate it between runs.

---

## Create the first administrator

There is deliberately **no public endpoint that creates an admin**.

1. Register the organization:

```powershell
$org = Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8000/organizations/register" `
  -ContentType "application/json" `
  -Body (@{ organization_name = "TechNova" } | ConvertTo-Json)

$org.tenant_id
```

2. Provision the admin out-of-band:

```powershell
python -m app.scripts.bootstrap_admin `
  --tenant-id <TENANT_UUID> `
  --username admin `
  --email admin@technova.com
```

The CLI prompts for the password securely. It refuses to create an admin in a
missing/inactive tenant and refuses to silently promote an existing account.

Public signup (`POST /auth/register`) creates an **inactive, role-less**
account. An administrator must activate it and assign a role.

---

## API endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `POST` | `/organizations/register` | Public | Create tenant + default roles |
| `GET` | `/organizations/me` | Bearer | Current user's tenant |
| `POST` | `/auth/register` | Public | Pending self-service signup |
| `POST` | `/auth/login` | Public | Email + password + tenant → JWT |
| `GET` | `/auth/me` | Bearer | Current DB-backed identity and roles |
| `GET` | `/users` | ADMIN | List users in own tenant |
| `POST` | `/users` | ADMIN | Create active user + assign roles |
| `GET` | `/users/{id}` | ADMIN | One user in own tenant |
| `POST` | `/users/{id}/roles` | ADMIN | Assign role by name |
| `DELETE` | `/users/{id}/roles/{role}` | ADMIN | Revoke role immediately |
| `PATCH` | `/users/{id}/status` | ADMIN | Activate/deactivate user |
| `GET` | `/health` | Public | Database health check |

### Login example

```http
POST /auth/login
Content-Type: application/json

{
  "tenant_id": "<tenant-uuid>",
  "email": "admin@technova.com",
  "password": "SecurePassword123!"
}
```

```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIs...",
  "token_type": "bearer",
  "expires_in": 1800
}
```

JWT claims: `sub`, `tenant_id`, `roles`, `type`, `iat`, `exp`, `jti`.

Use the token in Swagger via **Authorize**, or:

```powershell
$headers = @{ Authorization = "Bearer $token" }
Invoke-RestMethod -Uri "http://127.0.0.1:8000/auth/me" -Headers $headers
```

Request bodies reject unknown fields (`422`), including smuggled `tenant_id`,
`role_id`, `roles` and `is_active` values.

---

## Security decisions

- **Tenant ID is never accepted from the client.** It is taken from the
  authenticated database user for every query.
- **Roles are reloaded per request.** The JWT `roles` claim is informational.
- **Cross-tenant lookups return 404**, not 403, to avoid confirming existence.
- **Passwords:** Argon2id, per-password salt, 12–128 characters with letter and
  digit requirements.
- **JWT:** secret from environment, minimum 32 characters, explicit algorithm
  allow-list, expiry enforced, `type=access` required.
- **Inactive users and organizations** cannot log in and existing tokens stop
  working.
- **Login errors are generic** and a dummy password verification is performed
  for unknown accounts to reduce user enumeration.
- **Admin self-lockout** is prevented (cannot deactivate self or remove own last
  `ADMIN` role).

---

## Tests

```powershell
pytest -q
```

The suite covers all required scenarios:

- distinct tenant UUIDs;
- users bound to the correct tenant;
- same email allowed across tenants, rejected within one tenant;
- successful login, wrong password, wrong tenant;
- expired tokens, modified tokens, wrong-signing-key tokens;
- cross-tenant user listing and direct user lookup;
- inactive users and inactive organizations;
- public signup privilege escalation attempts;
- non-admin privilege escalation attempts;
- cross-tenant role assignment rejected by PostgreSQL;
- role revocation taking effect on an existing token;
- mass assignment, missing/malformed bearer tokens, weak passwords.

Each test docstring explains the vulnerability it checks.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `docker info` fails | Start Docker Desktop and wait for the engine |
| Port 5432 in use | Change compose to `127.0.0.1:5433:5432`, update both URLs in `backend\.env` |
| `psql` not recognized | Use `docker exec -it vaultrag-postgres psql -U vaultrag -d vaultrag` |
| `Activate.ps1` blocked | `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` |
| `ModuleNotFoundError: app` | Run from `backend\` with the virtualenv active |
| Email rejected with `.test` | Use a realistic domain such as `technova.com` |
| JWT secret too short | Generate a new 64-character secret |

---

## Git and secrets

Only `backend/.env.example` is tracked. `.env`, `.venv`, logs, caches and local
data are ignored by `.gitignore`.

Before committing, verify:

```powershell
git status --short
git ls-files | Select-String '\.env'
```

Never commit real JWT secrets, production database URLs, dumps or user data.

---

## Limitations and next steps

Not implemented yet:

- PostgreSQL RLS policies
- refresh tokens, email verification, password reset, MFA
- login rate limiting / account lockout
- audit logging
- ABAC and permission-aware retrieval
- document ingestion, embeddings, vector search and RAG

**Milestone 2:** enable RLS with a non-owner database role and
`SET LOCAL app.tenant_id`, then add audit logging before moving to the RAG
pipeline.

> Educational project following production patterns, but not independently
> security-audited. Do not use with real user data without further hardening.
