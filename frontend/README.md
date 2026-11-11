# VaultRAG frontend

Milestone 5 adds a responsive React, TypeScript and Vite application for the
employee workspace, document administration, user and role directory, and
tenant-scoped security monitoring. The frontend uses the existing FastAPI APIs;
it does not make authorization decisions or provide mock production data.

## Setup

From the repository root, install backend dependencies and start PostgreSQL,
then configure `backend/.env` and apply migrations as described in the root
[README](../README.md). Start the API and frontend in separate PowerShell
windows:

```powershell
cd C:\Users\Madhurya\OneDrive\Desktop\VaultRag\backend
..\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

```powershell
cd C:\Users\Madhurya\OneDrive\Desktop\VaultRag\frontend
npm ci
$env:VITE_API_BASE_URL = "http://localhost:8000"
npm run dev
```

Open `http://localhost:5173`. Login requires the organization UUID, email and
password used by the existing `/auth/login` endpoint. The client fetches the
identity from `/auth/me`; role and capability checks only shape navigation,
while the API enforces access on every request.

## Stack and structure

- React 19, TypeScript, Vite, Tailwind CSS and reusable Radix-based UI
- React Router, Axios, TanStack Query, React Hook Form, Zod, Recharts and
  Lucide React
- `src/components`: layout, auth, documents, users, common and UI components
- `src/pages`: login, overview, assistant, documents, users, security and error
  pages
- `src/lib/api.ts`: centralized bearer-token API client and safe error handling
- `src/contexts`: identity/session and theme state
- `src/hooks/queries.ts`: server state for documents, roles, users, RAG health,
  audit events and aggregate analytics
- `src/types/api.ts`: TypeScript representations of the current API responses
- `e2e`: Playwright browser scenarios against a configured live environment

`VITE_API_BASE_URL` is the only frontend environment variable. It must contain
an API origin/path, never a secret. The current backend returns a bearer JWT,
so the client keeps it in browser local storage for compatibility and sends it
in the `Authorization` header. The server remains authoritative and reloads
roles from PostgreSQL on each request. A future HttpOnly cookie flow would
require coordinated backend changes and CSRF protection.

## Features and API connections

- Login and session identity: `POST /auth/login`, `GET /auth/me`
- Employee dashboard: accessible documents, own attributes, RAG health and
  recent user-visible activity
- Assistant: `POST /rag/ask`, `GET /rag/history`, validated backend citations
- Documents: `GET /documents`, multipart `POST /documents/upload`, processing,
  status, authorized chunk inspection and permission grant/revoke endpoints
- User administration: `/users` and `/users/{id}/roles`
- Role directory and capability matrix: `GET /roles` (capabilities are
  code-defined; the current API does not edit role definitions)
- Security: `/security/audit-logs` and `/security/analytics`, with tenant scope,
  server-side filters and pagination

Conversation history is currently stateless in the backend. Audit date-range
and department filters are also not exposed by the API, so the UI does not
simulate them. Document actions are available only where the current backend
capabilities and exact document grants permit them.

## Commands

```powershell
npm test
npm run lint
npm run build
npm run e2e
```

Unit tests run in Vitest/jsdom and use isolated API mocks. Playwright tests use
the live API at `http://127.0.0.1:8010` by default, launch Vite at
`http://localhost:5173`, and require `E2E_TENANT_ID`, `E2E_ADMIN_EMAIL`,
`E2E_ADMIN_PASSWORD`, `E2E_EMPLOYEE_EMAIL` and `E2E_EMPLOYEE_PASSWORD` to be
configured for provisioned test accounts. Credentials are not stored in the
repository. They exercise real upload, processing, permission, user, analytics
and RAG routes; provisioning sample records and running database, Ollama and
embedding services is an environment prerequisite.

## Production build

```powershell
npm run build
npm run preview
```

Deploy `dist/` behind an HTTPS origin. Configure backend CORS for that origin,
use a secure API URL, and keep all credentials, signing keys and private
documents on the backend. The bearer-token storage choice follows the existing
API contract and has the usual XSS exposure of browser-accessible tokens.
