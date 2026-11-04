# Security Design — VaultRAG Milestone 1

This document explains the security decisions behind the implementation. The
README is intentionally a quick start; this file is the interview/design
reference.

---

## 1. Trust model

The JWT proves that a token was issued to a user. It does **not** prove that the
user is still active, still belongs to that tenant, or still holds the roles
written into the token.

On every protected request, `app/dependencies/authentication.py`:

1. Extracts the bearer token.
2. Verifies the signature, expiry and required claims.
3. Loads the user by `sub` from PostgreSQL.
4. Rejects the token if its `tenant_id` claim differs from the user row.
5. Checks user and organization `is_active`.
6. Reloads current roles from `user_roles` / `roles`.
7. Returns a frozen `AuthenticatedContext`.

Consequences:

- Deactivating a user invalidates existing tokens immediately.
- Revoking a role takes effect on the next request.
- A stale `roles` claim in the JWT is ignored for authorization.

---

## 2. Tenant isolation in three layers

### Layer 1 — Authenticated context

`AuthenticatedContext.tenant_id` is a property of the database user:

```python
@property
def tenant_id(self) -> UUID:
    return self.user.tenant_id
```

No route accepts a tenant identifier for an authenticated action.

### Layer 2 — Mandatory query filters

Every tenant-sensitive query includes the authenticated tenant:

```python
select(User).where(User.tenant_id == context.tenant_id)
```

Cross-tenant object IDs return `404`, not `403`, so the API does not confirm
that another tenant's object exists.

### Layer 3 — Database composite foreign keys

`user_roles` stores one `tenant_id` shared by two composite references:

```sql
FOREIGN KEY (tenant_id, user_id) REFERENCES users (tenant_id, user_id)
FOREIGN KEY (tenant_id, role_id) REFERENCES roles  (tenant_id, role_id)
```

`users` and `roles` each have `UNIQUE (tenant_id, <id>)` to support these
references. A row linking user A (tenant A) to role B (tenant B) fails because
`(tenant_A, role_B)` does not exist. PostgreSQL rejects it even if application
code is bypassed. This is tested directly in
`test_cross_tenant_user_role_row_is_rejected_by_the_database`.

---

## 3. Tenant-scoped schema

| Table | Tenant rule |
|---|---|
| `organizations` | tenant root; `tenant_id` is the primary key |
| `users` | `tenant_id NOT NULL`; email and username unique per tenant |
| `roles` | `tenant_id NOT NULL`; role name unique per tenant |
| `user_roles` | one `tenant_id`; composite FKs enforce consistency |

Every tenant-owned table has a `NOT NULL tenant_id`, which is the prerequisite
for adding RLS later without restructuring.

Email addresses are normalised to lowercase before storage and lookup. The same
email may exist in different organizations, but not twice in one organization.

---

## 4. Authentication and password handling

### Passwords

- Argon2id via `argon2-cffi` (memory-hard, random salt per hash).
- 12–128 characters, at least one letter and one digit.
- Small common-password deny-list.
- Re-hash on login when Argon2 parameters change.
- Login accepts any non-empty password up to 128 characters so older accounts
  can still sign in after a policy change.

### Login

- Requires tenant ID, email and password.
- Unknown email, wrong password and inactive account return the same `401`
  message.
- A dummy Argon2 verification runs for unknown accounts to equalise timing.
- Account/tenant state is checked after password verification.

### JWT

- Secret comes from `JWT_SECRET_KEY` (minimum 32 characters).
- Production startup rejects placeholder secrets.
- Explicit algorithm allow-list; `alg: none` and algorithm confusion are
  impossible.
- Required claims: `exp`, `iat`, `sub`, `tenant_id`, `type`.
- `type` must be `access`; `sub` and `tenant_id` must be UUIDs.
- Lifetime: `ACCESS_TOKEN_EXPIRE_MINUTES` (1–1440).
- No refresh tokens in Milestone 1.

---

## 5. Privilege and onboarding model

### Public signup

`POST /auth/register` creates a user that is:

- in the requested active tenant,
- `is_active = false`,
- assigned no roles.

It cannot log in until an administrator activates it and assigns a role. The
request schema uses `extra="forbid"`, so `role_names`, `role_id`, `roles` and
`is_active` are rejected with `422`.

### First administrator

The first ADMIN is created out-of-band:

```powershell
python -m app.scripts.bootstrap_admin --tenant-id ... --username ... --email ...
```

The script:

- requires an existing, active tenant;
- refuses to silently promote an existing email;
- assigns only that tenant's `ADMIN` role;
- prompts for the password without echo.

No public HTTP endpoint can create or promote an administrator.

### Admin operations

All `/users` routes require a **current** `ADMIN` role loaded from the database.
Role names are resolved inside the authenticated tenant; role IDs and tenant IDs
are never accepted from the client. Admins cannot deactivate themselves or
remove their own last `ADMIN` role.

---

## 6. Mass assignment and input validation

All request models inherit `StrictRequestModel` with:

```python
model_config = ConfigDict(extra="forbid")
```

This prevents clients from setting fields the endpoint did not intend to expose.
Additional validation:

- usernames: 3–100 safe characters;
- emails: validated and normalised;
- role names: uppercase identifier pattern;
- passwords: bounded and policy-checked.

---

## 7. Application filtering vs PostgreSQL RLS

Milestone 1 has **application-level filtering + database integrity constraints**.
It does **not** enable Row-Level Security.

- **Application filtering:** `WHERE tenant_id = :authenticated_tenant`. A
  forgotten clause could return too much data.
- **RLS:** PostgreSQL rewrites every query against a policy such as
  `USING (tenant_id = current_setting('app.tenant_id')::uuid)`. It protects even
  forgotten filters and ad-hoc SQL, provided the application connects as a
  non-owner role and sets the session variable per transaction.

Planned Milestone 2 steps:

1. Create a non-owner application database role.
2. `ENABLE` and `FORCE ROW LEVEL SECURITY` on every tenant table.
3. Create tenant-isolation policies using `current_setting('app.tenant_id')`.
4. Run `SET LOCAL app.tenant_id = ...` at the start of each transaction.
5. Keep application filters as defence in depth.

`database/init.sql` documents this plan without enabling it.

---

## 8. Error handling and information leakage

| Status | Meaning |
|---|---|
| 400 | Invalid request or tenant-integrity violation |
| 401 | Missing, expired, tampered or inactive credentials |
| 403 | Authenticated but lacks ADMIN |
| 404 | Not found **in the caller's tenant** |
| 409 | Duplicate email/username/role |
| 422 | Validation error or unknown field |

- Authentication failures are generic.
- Cross-tenant lookups are indistinguishable from missing objects.
- Unexpected exceptions are handled by FastAPI; internal details are not
  returned to clients.
- Logs contain user/tenant IDs but never passwords or tokens.

---

## 9. Security test coverage

`pytest -q` runs 31 tests against PostgreSQL. They cover:

- distinct tenant IDs and default roles;
- tenant-correct user creation;
- same email across tenants allowed, within one tenant rejected;
- successful login and JWT claims;
- wrong password, wrong tenant, unknown account;
- expired, modified and wrong-key tokens;
- cross-tenant user listing and direct lookup (IDOR);
- inactive users and organizations;
- public signup mass-assignment attempts;
- non-admin privilege escalation attempts;
- cross-tenant role links rejected by the database;
- immediate role revocation on an existing token;
- admin self-lockout guardrails;
- missing/malformed bearer tokens.

Test docstrings state the vulnerability each test checks.

---

## 10. Known limitations

- No RLS yet.
- No email verification, invitations, password reset or MFA.
- No login rate limiting or account lockout.
- No audit log.
- No refresh tokens or token revocation list.
- Public signup can create pending accounts in any known tenant (admin
  approval prevents access, but spam is possible).
- Not independently security-audited.
