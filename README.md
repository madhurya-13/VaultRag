# VaultRAG — Permission-Aware Multi-Tenant RAG System

> **Milestone 1: Secure Backend and Database Foundation**

VaultRAG is an enterprise-style, multi-tenant knowledge platform. Several
organizations share one application while their users, roles and data remain
isolated. This repository currently contains **only Milestone 1**: the secure
identity, authorization and database foundation.

**Not implemented yet (on purpose):** RAG pipeline, document ingestion,
embeddings, vector search, LLM calls, audit logging, ABAC, PostgreSQL
Row-Level Security, refresh tokens and any frontend. Role-based access control
(RBAC) is implemented in its tenant-scoped form; ABAC comes later.

---

## 1. What Milestone 1 delivers

| Requirement | Status |
|---|---|
| FastAPI application with Swagger docs | Done |
| PostgreSQL schema managed by Alembic | Done |
| Organization (tenant) registration + default roles | Done |
| Secure user registration / onboarding | Done |
| Argon2id password hashing | Done |
| JWT login (signed, expiring, configurable) | Done |
| Reusable authentication dependency | Done |
| Tenant identity derived from the database, never from the request | Done |
| Current roles reloaded from the database on every request | Done |
| Cross-tenant user listing prevented | Done |
| Cross-tenant role assignment blocked by the database itself | Done |
| Inactive users and organizations denied | Done |
| 31 automated security tests | Passing |
| PostgreSQL RLS | **Not yet — Milestone 2** |

---

## 2. Architecture

```
API client / Swagger UI
        │  HTTPS + Authorization: Bearer <JWT>
        ▼
┌──────────────────────────────────────────────┐
│ FastAPI                                       │
│  routes/          HTTP validation & responses │
│  dependencies/    token → trusted context     │
│  services/        business rules + tenant scope│
│  models/          SQLAlchemy 2.0 mappings     │
└──────────────────────┬───────────────────────┘
                       │ SQLAlchemy + psycopg 3
                       ▼
┌──────────────────────────────────────────────┐
│ PostgreSQL                                   │
│  organizations (tenant root)                 │
│  roles          tenant-owned role catalogue  │
│  users          tenant-owned identities      │
│  user_roles     composite tenant-safe links  │
└──────────────────────────────────────────────┘
```

### Request lifecycle (protected endpoint)

1. `HTTPBearer` extracts the bearer token.
2. `decode_access_token` verifies the HMAC signature, expiry, issuer time and
   required claims with an explicit algorithm allow-list.
3. The user is loaded from PostgreSQL by `sub`.
4. The token's `tenant_id` is compared with the **database** user's tenant.
5. User and organization `is_active` flags are checked.
6. Current roles are loaded from `user_roles` / `roles`.
7. `AuthenticatedContext` (user, organization, roles) is passed to the route.
8. Every query uses `context.tenant_id`. No route accepts a tenant identifier
   from the body, query string or a custom header.

The JWT is treated as a **pointer to an identity**, not as the authority. That
is why deactivating a user or revoking a role takes effect immediately, even
when an old token has not expired.

---

## 3. Repository structure

```
VaultRag/
├── backend/
│   ├── app/
│   │   ├── main.py                  FastAPI app, lifespan DB check, error mapping
│   │   ├── core/
│   │   │   ├── config.py            typed settings, secret validation
│   │   │   ├── database.py          engine, DeclarativeBase, get_db session lifecycle
│   │   │   ├── security.py          Argon2id + JWT create/decode
│   │   │   ├── passwords.py         password/username/role policy shared by API + CLI
│   │   │   └── exceptions.py        domain errors → HTTP status codes
│   │   ├── models/
│   │   │   ├── organization.py      tenants
│   │   │   ├── user.py              tenant-scoped users + unique constraints
│   │   │   ├── role.py              tenant-owned roles
│   │   │   └── user_role.py         composite-FK association table
│   │   ├── schemas/                 Pydantic v2 request/response models
│   │   ├── routes/                  auth, organizations, users
│   │   ├── dependencies/            authentication + ADMIN guard
│   │   ├── services/                business logic reused by API, CLI and tests
│   │   ├── scripts/
│   │   │   └── bootstrap_admin.py   operator-only first-admin CLI
│   │   └── tests/                   pytest security suite
│   ├── alembic/                     migration environment + initial revision
│   ├── alembic.ini
│   ├── pytest.ini
│   ├── requirements.txt
│   └── .env.example
├── database/
│   └── init.sql                     creates vaultrag + vaultrag_test, RLS notes
├── docker-compose.yml               PostgreSQL 16 for local development
├── .gitignore
└── README.md
```

The extra `services/`, `core/passwords.py` and `scripts/` modules were added
deliberately: they keep routes thin, make security rules reusable from the CLI
and tests, and avoid duplicating tenant checks.

---

## 4. Prerequisites

- Windows 10/11 with PowerShell
- **Python 3.11+** (tested on Python 3.12.10)
- **Docker Desktop** with the engine running (easiest PostgreSQL setup)
- Git
- VS Code (recommended)
- PostgreSQL 16 client tools are **not** required if you use Docker; `psql`
  can be run inside the container.

Check:

```powershell
python --version
docker --version
docker info --format "{{.ServerVersion}}"
git --version
```

If `docker info` fails, start Docker Desktop and wait for the engine.

---

## 5. Setup on Windows (PowerShell)

### 5.1 Verify Python and create the virtual environment

```powershell
cd C:\Users\Madhurya\OneDrive\Desktop\VaultRag
python --version

python -m venv .venv

# If PowerShell blocks Activate.ps1:
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
pip install -r backend\requirements.txt
```

> The virtual environment lives at the repository root; all commands below
> assume it is activated. In VS Code, select `.venv\Scripts\python.exe` as the
> interpreter (`Ctrl+Shift+P` → *Python: Select Interpreter*).

### 5.2 Start PostgreSQL

```powershell
docker compose up -d db
docker compose ps
```

The container binds only to `127.0.0.1:5432`. On first start, `database/init.sql`
creates the `vaultrag_test` database used by pytest.

Verify the databases:

```powershell
docker exec vaultrag-postgres psql -U vaultrag -d vaultrag -c "\l"
```

Expected databases: `vaultrag` and `vaultrag_test`.

> If port 5432 is already used, change the compose port to `127.0.0.1:5433:5432`
> and update both database URLs in `backend\.env`.

### 5.3 Configure environment variables

```powershell
cd backend
Copy-Item .env.example .env

# Generate a strong signing secret:
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

Open `backend\.env` in VS Code and paste the generated value into
`JWT_SECRET_KEY`. Never commit `.env` (it is in `.gitignore`).

`.env.example` contains only placeholders and local-development values.

### 5.4 Create the schema with Alembic

```powershell
cd backend
alembic upgrade head
alembic check
```

`alembic check` should print `No new upgrade operations detected.`

Verify the tables:

```powershell
docker exec vaultrag-postgres psql -U vaultrag -d vaultrag -c "\dt"
```

Expected: `alembic_version`, `organizations`, `roles`, `users`, `user_roles`.

Verify connectivity from Python:

```powershell
python -c "from app.core.database import engine; from sqlalchemy import text; c=engine.connect(); print(c.execute(text('SELECT current_database()')).scalar()); c.close()"
```

Expected: `vaultrag`.

### 5.5 Start the API

```powershell
cd backend
uvicorn app.main:app --reload
```

- Swagger UI: <http://127.0.0.1:8000/docs>
- ReDoc: <http://127.0.0.1:8000/redoc>
- Health: <http://127.0.0.1:8000/health>

The application performs `SELECT 1` at startup and refuses to start if the
database is unreachable.

### 5.6 Run the tests

Open a **second** PowerShell window, activate the virtualenv, then:

```powershell
cd C:\Users\Madhurya\OneDrive\Desktop\VaultRag\backend
pytest -q
```

Expected:

```
31 passed
```

The test suite uses `vaultrag_test` and **truncates** it between tests. A guard
in `app/tests/conftest.py` refuses to run unless the database name contains
`test`.

---

## 6. Bootstrapping the first administrator

There is intentionally **no public endpoint that creates an administrator**.
Organization registration only creates the tenant and its role catalogue.

The first ADMIN is provisioned out-of-band by an operator who already controls
the server or database:

1. Register the organization:

```powershell
$org = Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8000/organizations/register" `
  -ContentType "application/json" `
  -Body (@{ organization_name = "TechNova" } | ConvertTo-Json)

$org | ConvertTo-Json -Depth 5
$org.tenant_id
```

2. Run the CLI with that tenant ID:

```powershell
python -m app.scripts.bootstrap_admin `
  --tenant-id <TENANT_UUID> `
  --username admin `
  --email admin@technova.com
```

You are prompted for the password twice without echo. The script refuses to:

- create an admin in a non-existent or inactive tenant;
- silently promote an existing email (it exits with an error instead).

It is safe to keep this as the only admin-creation path: an attacker on the
network cannot invoke it.

> `--password` exists for automation but can leak through shell history or
> process listings. Use the interactive prompt locally.

After bootstrap, use the ADMIN account to create ordinary users through
`POST /users`, or let users self-register and have an admin activate them.

---

## 7. API reference

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `POST` | `/organizations/register` | Public | Create tenant + default roles |
| `GET` | `/organizations/me` | Bearer | Authenticated user's tenant |
| `POST` | `/auth/register` | Public | Pending account request (inactive, role-less) |
| `POST` | `/auth/login` | Public | Exchange email + password + tenant for JWT |
| `GET` | `/auth/me` | Bearer | Current DB-backed identity and roles |
| `GET` | `/users` | **ADMIN** | List users in the caller's tenant |
| `POST` | `/users` | **ADMIN** | Create active user + assign tenant roles |
| `GET` | `/users/{user_id}` | **ADMIN** | One user in the caller's tenant |
| `POST` | `/users/{user_id}/roles` | **ADMIN** | Assign a role by name |
| `DELETE` | `/users/{user_id}/roles/{role_name}` | **ADMIN** | Revoke a role immediately |
| `PATCH` | `/users/{user_id}/status` | **ADMIN** | Activate / deactivate a user |
| `GET` | `/health` | Public | Database-backed health check |
| `GET` | `/docs` | Public | Swagger UI |

All request bodies use `extra="forbid"`; unknown fields return **422**. This is
deliberate mass-assignment protection.

### 7.1 Register an organization

```http
POST /organizations/register
Content-Type: application/json

{ "organization_name": "TechNova" }
```

```json
{
  "tenant_id": "1abb59a6-8ef1-434d-99ef-3597c8a14c43",
  "organization_name": "TechNova",
  "is_active": true,
  "created_at": "2026-11-04T18:48:14.714550Z",
  "roles": ["ADMIN", "ENGINEERING", "FINANCE", "HR"]
}
```

The response contains **no user** and **no token**. Registration does not grant
access.

### 7.2 Login

```http
POST /auth/login
Content-Type: application/json

{
  "tenant_id": "1abb59a6-8ef1-434d-99ef-3597c8a14c43",
  "email": "admin@technova.com",
  "password": "DemoAdminPass123!"
}
```

```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer",
  "expires_in": 1800
}
```

JWT claims:

```json
{
  "sub": "<user-uuid>",
  "tenant_id": "<organization-uuid>",
  "roles": ["ADMIN"],
  "type": "access",
  "iat": 1762282095,
  "exp": 1762283895,
  "jti": "<random-id>"
}
```

### 7.3 Current identity

```powershell
$login = Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8000/auth/login" `
  -ContentType "application/json" `
  -Body (@{
    tenant_id = $org.tenant_id
    email = "admin@technova.com"
    password = "DemoAdminPass123!"
  } | ConvertTo-Json)

$headers = @{ Authorization = "Bearer $($login.access_token)" }

Invoke-RestMethod -Uri "http://127.0.0.1:8000/auth/me" -Headers $headers |
  ConvertTo-Json -Depth 5
```

```json
{
  "user_id": "211ec53f-2697-421c-b157-baeb1612a54c",
  "username": "admin",
  "email": "admin@technova.com",
  "tenant_id": "1abb59a6-8ef1-434d-99ef-3597c8a14c43",
  "organization_name": "TechNova",
  "roles": ["ADMIN"],
  "is_active": true,
  "created_at": "2026-11-04T18:48:15.416133Z"
}
```

### 7.4 Current organization

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/organizations/me" -Headers $headers |
  ConvertTo-Json -Depth 5
```

```json
{
  "tenant_id": "1abb59a6-8ef1-434d-99ef-3597c8a14c43",
  "organization_name": "TechNova",
  "is_active": true,
  "created_at": "2026-11-04T18:48:14.714550Z"
}
```

### 7.5 Admin creates an active user with a role

```http
POST /users
Authorization: Bearer <ADMIN_TOKEN>
Content-Type: application/json

{
  "username": "hr_user",
  "email": "hr@technova.com",
  "password": "SecurePassword123!",
  "role_names": ["HR"]
}
```

```json
{
  "user_id": "6637d83a-082e-47c2-9c98-f6acd2ca0f52",
  "tenant_id": "1abb59a6-8ef1-434d-99ef-3597c8a14c43",
  "username": "hr_user",
  "email": "hr@technova.com",
  "is_active": true,
  "created_at": "2026-11-04T18:48:15.762065Z",
  "roles": ["HR"]
}
```

The request cannot contain `tenant_id` or `role_id` (422 if it does). The
tenant comes from the admin's token; roles are resolved by name **inside that
tenant**.

### 7.6 List users (tenant-scoped)

```http
GET /users?limit=50&offset=0
Authorization: Bearer <ADMIN_TOKEN>
```

```json
[
  {
    "user_id": "211ec53f-2697-421c-b157-baeb1612a54c",
    "tenant_id": "1abb59a6-8ef1-434d-99ef-3597c8a14c43",
    "username": "admin",
    "email": "admin@technova.com",
    "is_active": true,
    "created_at": "2026-11-04T18:48:15.416133Z",
    "roles": ["ADMIN"]
  },
  {
    "user_id": "6637d83a-082e-47c2-9c98-f6acd2ca0f52",
    "tenant_id": "1abb59a6-8ef1-434d-99ef-3597c8a14c43",
    "username": "hr_user",
    "email": "hr@technova.com",
    "is_active": true,
    "created_at": "2026-11-04T18:48:15.762065Z",
    "roles": ["HR"]
  }
]
```

`?tenant_id=<other-tenant>` is ignored. The endpoint always filters on the
authenticated user's tenant.

### 7.7 Self-service signup (pending)

```http
POST /auth/register
Content-Type: application/json

{
  "tenant_id": "1abb59a6-8ef1-434d-99ef-3597c8a14c43",
  "username": "intern",
  "email": "intern@technova.com",
  "password": "SecurePassword123!"
}
```

```json
{
  "user_id": "4137b42a-94be-40ae-bdd2-a5ee7a85c0ec",
  "tenant_id": "1abb59a6-8ef1-434d-99ef-3597c8a14c43",
  "username": "intern",
  "email": "intern@technova.com",
  "is_active": false,
  "roles": [],
  "message": "Account created in pending state. An administrator of this organization must activate it and assign a role."
}
```

The account cannot log in until an admin activates it. This prevents outsiders
from joining a tenant or choosing their own role.

### 7.8 Admin activates and assigns a role

```powershell
# Activate
Invoke-RestMethod -Method Patch `
  -Uri "http://127.0.0.1:8000/users/<USER_ID>/status" `
  -Headers $headers `
  -ContentType "application/json" `
  -Body (@{ is_active = $true } | ConvertTo-Json)

# Assign HR
Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8000/users/<USER_ID>/roles" `
  -Headers $headers `
  -ContentType "application/json" `
  -Body (@{ role_name = "HR" } | ConvertTo-Json)
```

### 7.9 Error response shape

```json
{ "detail": "Invalid credentials" }
```

| Status | Meaning |
|---|---|
| 400 | Invalid request / tenant-integrity violation |
| 401 | Missing, expired, tampered or inactive credentials |
| 403 | Authenticated but lacks ADMIN |
| 404 | Resource not found **in the caller's tenant** |
| 409 | Duplicate email/username, duplicate role |
| 422 | Validation error or unknown/mass-assigned field |

Login failures use one generic message for unknown email, wrong password and
inactive account. A dummy Argon2 verification is performed for unknown accounts
to reduce timing-based user enumeration.

---

## 8. Multi-tenant security architecture

### 8.1 Three enforcement layers

1. **Authenticated context**: tenant ID and roles are loaded from PostgreSQL.
   The token is never trusted for authorization decisions.
2. **Application scoping**: every tenant-sensitive query filters on
   `context.tenant_id`. Cross-tenant IDs return 404, not 403, to avoid
   confirming that another tenant's object exists.
3. **Database constraints**: composite foreign keys make cross-tenant
   user-role rows impossible, even if application code is bypassed.

RLS would be a fourth layer in Milestone 2. It is **not present today**.

### 8.2 Composite foreign keys (TEST 12)

`user_roles` stores a single `tenant_id` and references:

```sql
FOREIGN KEY (tenant_id, user_id) REFERENCES users (tenant_id, user_id)
FOREIGN KEY (tenant_id, role_id) REFERENCES roles  (tenant_id, role_id)
```

For a row linking user A (tenant A) to role B (tenant B), the first FK needs
`(A, userA)` and the second needs `(A, roleB)`. The role with tenant A and
`roleB` does not exist, so PostgreSQL raises a foreign-key violation. This is
why `users` and `roles` both have `UNIQUE (tenant_id, <id>)` constraints.

### 8.3 Why roles are reloaded from the database

The token includes a `roles` claim for diagnostics and client convenience. The
authentication dependency **ignores it** and reloads `user_roles` on every
request. Test `test_role_revocation_takes_effect_immediately` proves that an
unexpired token loses ADMIN access the moment the role is revoked.

### 8.4 Tenant identity is never client-controlled

- No endpoint accepts `tenant_id` for an authenticated action.
- `users`/`roles` creation derive the tenant from `AuthenticatedContext`.
- A custom `X-Tenant-ID` header has no effect (tested).
- A correctly signed token whose `tenant_id` claim disagrees with the user row
  is rejected (defence in depth, tested).

### 8.5 Password storage

Argon2id via `argon2-cffi`, random per-password salt, encoded parameters stored
with the digest. Passwords are 12–128 characters, must contain a letter and a
digit, and a small common-password deny-list is applied. Login re-hashes when
Argon2 parameters are upgraded.

### 8.6 JWT hardening

- Secret loaded from `JWT_SECRET_KEY`; minimum 32 characters.
- Production startup rejects placeholder secrets.
- Algorithm is an explicit allow-list (`HS256`/`HS384`/`HS512`), never `none`.
- `exp`, `iat`, `sub`, `tenant_id` and `type` are required.
- `type` must be `access`.
- `sub` and `tenant_id` must parse as UUIDs.
- Lifetime is configurable through `ACCESS_TOKEN_EXPIRE_MINUTES`.
- No refresh tokens yet.

**OAuth2 note:** token extraction follows RFC 6750 bearer-token semantics. The
password exchange is JSON rather than `application/x-www-form-urlencoded`
because this application needs **three** login factors (tenant, email,
password), while the standard `OAuth2PasswordRequestForm` only carries a
username and password. Swagger's **Authorize** dialog accepts the JWT directly,
which is the normal way to exercise the bearer-token flow.

### 8.7 Application-level filtering vs PostgreSQL RLS

Milestone 1 has **application-level tenant filtering** plus **database
integrity constraints**. It does **not** have RLS.

- Application filtering: `SELECT ... WHERE tenant_id = :authenticated_tenant`.
  If a developer forgets the clause, the query can return too much.
- RLS: PostgreSQL rewrites every query against a policy such as
  `USING (tenant_id = current_setting('app.tenant_id')::uuid)`. It protects even
  against forgotten filters and ad-hoc SQL, provided the application connects
  as a non-owner role and sets the session variable in each transaction.

RLS is planned for Milestone 2. The schema is already RLS-ready because every
tenant-owned table has a `NOT NULL tenant_id` and the association table is
tenant-consistent. `database/init.sql` documents the intended policy without
enabling it.

---

## 9. Security test suite

Run:

```powershell
cd backend
pytest -q
```

Coverage of the required scenarios:

| Spec test | Test function |
|---|---|
| TEST 1 – distinct tenant UUIDs | `test_register_two_organizations_receive_distinct_uuids` |
| TEST 2 – users bound to correct tenant | `test_public_registration_is_pending_and_tenant_scoped` |
| TEST 3 – same email, different orgs | `test_same_email_allowed_in_different_organizations` |
| TEST 4 – same email, same org rejected | `test_same_email_rejected_within_one_organization` |
| TEST 5 – successful login/JWT | `test_login_returns_signed_jwt_with_expected_claims` |
| TEST 6 – wrong password | `test_login_rejects_wrong_password_without_account_disclosure` |
| TEST 7 – expired JWT | `test_expired_jwt_is_rejected` |
| TEST 8 – modified JWT | `test_modified_jwt_is_rejected` |
| TEST 9 – cross-tenant user access | `test_user_listing_is_scoped_to_the_authenticated_tenant` |
| TEST 10 – inactive user | `test_inactive_user_cannot_authenticate_or_use_existing_token` |
| TEST 11 – self-assigned ADMIN | `test_public_registration_cannot_assign_roles_or_activation`, `test_non_admin_cannot_grant_themselves_admin` |
| TEST 12 – cross-tenant role link | `test_cross_tenant_user_role_row_is_rejected_by_the_database` |

Additional tests cover:

- inactive organizations;
- role revocation with an existing token;
- token tenant-claim mismatch;
- wrong tenant at login;
- IDOR on `GET /users/{id}`;
- admin operations against another tenant;
- mass assignment of `tenant_id`, `role_id`, `roles`, `is_active`;
- missing/malformed/`Basic` authorization headers;
- weak password rejection;
- Argon2id storage verification;
- self-lockout guardrails.

Every test docstring states the vulnerability it checks.

---

## 10. Troubleshooting

**`docker info` fails**
Start Docker Desktop and wait for "Engine running".

**Port 5432 already in use**
Change `docker-compose.yml` to `127.0.0.1:5433:5432`, then update
`DATABASE_URL` and `TEST_DATABASE_URL` in `backend\.env`.

**`psql` is not recognized**
Use Docker:

```powershell
docker exec -it vaultrag-postgres psql -U vaultrag -d vaultrag
```

**`Activate.ps1 cannot be loaded`**
```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

**`ModuleNotFoundError: No module named 'app'`**
Run commands from `backend\` with the virtualenv active. Alembic and pytest both
use `backend\` as the root.

**Login returns 422 for a valid-looking email**
`email-validator` rejects reserved TLDs such as `.test` and `.invalid`. Use a
realistic domain (e.g. `technova.com`) in tests and demos.

**`JWT_SECRET_KEY must be at least 32 characters`**
Generate a new secret with `python -c "import secrets; print(secrets.token_urlsafe(64))"`
and paste it into `backend\.env`.

**`alembic check` reports changes**
Do not hand-edit the applied database schema. Run `alembic revision --autogenerate`
and review the migration, or reset the dev database with
`docker compose down -v` (destroys local data) and `alembic upgrade head`.

**Tests refuse to run**
The safety guard requires the test database name to contain `test`. Set:

```powershell
$env:TEST_DATABASE_URL = "postgresql+psycopg://vaultrag:vaultrag_local_dev_password@localhost:5432/vaultrag_test"
pytest -q
```

---

## 11. Prepare for GitHub

```powershell
cd C:\Users\Madhurya\OneDrive\Desktop\VaultRag
git init
git add .
git status --short
```

Before committing, verify that **no** `.env`, `.venv`, database dump or secret is
staged. `.gitignore` covers `.env`, `.env.*` (except `.env.example`), virtual
environments, caches and logs.

```powershell
git commit -m "feat: milestone 1 secure multi-tenant auth foundation"
git branch -M main
git remote add origin https://github.com/<your-username>/vaultrag.git
git push -u origin main
```

Never commit:

- `backend/.env`
- real JWT secrets
- production database URLs or dumps
- user data or documents

---

## 12. Milestone 1 completion checklist

- [x] FastAPI server runs
- [x] PostgreSQL connects
- [x] Organizations can be registered
- [x] Users can be registered securely (pending + admin provisioning)
- [x] Passwords are Argon2id-hashed
- [x] JWT login works
- [x] Protected routes require authentication
- [x] Current identity comes from the database
- [x] Roles are loaded from the database per request
- [x] Organization A cannot access Organization B users
- [x] Cross-tenant user-role assignments are rejected by PostgreSQL
- [x] Inactive users and organizations are blocked
- [x] 31 automated tests pass
- [x] README has complete setup and security documentation

---

## 13. Known limitations and next steps

Milestone 1 is a foundation, not a finished product.

**Security hardening still recommended**

- Email verification and invitation emails (currently admin approval only).
- Rate limiting / account lockout on `/auth/login` and `/auth/register`.
- MFA and password reset.
- Audit logging for authentication and role changes.
- RLS enabled with a non-owner database role.
- Refresh-token rotation and revocation lists.

**Milestone 2+ roadmap**

1. PostgreSQL RLS policies + per-request `SET LOCAL app.tenant_id`.
2. ABAC policies (department, classification, document ownership).
3. Document ingestion, chunking and embeddings.
4. Vector search with tenant/permission filters pushed into the query.
5. Permission-aware retrieval and RAG answer generation.
6. Audit trail and cross-tenant leakage detection tests.

---

## 14. Security disclaimer

This is an educational, milestone-based implementation. It follows production
patterns (server-side tenant resolution, Argon2id, signed JWTs, composite FKs,
mass-assignment protection, automated security tests), but it has not undergone
an independent security audit, penetration test or compliance review. Do not
deploy it with real user data until the limitations above are addressed.
