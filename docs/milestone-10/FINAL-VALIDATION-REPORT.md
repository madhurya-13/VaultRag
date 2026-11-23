# Milestone 10 — Final Validation Report

Date: 2026-11-23
Environment: Windows 11, local PostgreSQL/pgvector container, Python virtual
environment at `../.venv`, Node.js/npm frontend toolchain.
Version under test: `1.0.0` (Milestone 10).

This report lists only commands actually executed. Checks that could not be run
are identified as blocked or not run, with the reason.

## Summary

| Check | Command | Result |
|---|---|---|
| Backend regression | `python -m pytest -q` (from `backend`) | **368 passed, 2 skipped** in 376.83 s |
| New API tests | focused reruns: feedback (2), explainability (1), health/logging (3) | **6 passed**; the full suite covers all new API tests |
| Migration from empty DB | executed by the test session | All migrations through `f2c3d4e5a6b7` applied |
| Migration drift | `python -m alembic check` | **No new upgrade operations detected** |
| Backend lint (changed files) | `python -m ruff check <changed backend files + seed script>` | **All checks passed** |
| Frontend unit tests | `npm test` | **27 passed** in 6 files |
| Frontend lint | `npm run lint` | **0 errors, 18 warnings** |
| Frontend typecheck + build | `npm run build` (`tsc -b && vite build`) | **Success**, chunk-size warning only |
| Compose configuration | `docker compose config --quiet` with required env placeholders | **Exit 0** |
| Backup script | `powershell -ExecutionPolicy Bypass -File scripts/backup.ps1` | **Success**, dump + TOC + document archive |
| Playwright discovery | `npx playwright test --list` | **12 tests in 2 files load** |
| npm audit | `npm audit --omit=dev` | **0 vulnerabilities** |
| Python dependency audit | `python -m pip_audit` | **No known vulnerabilities**; `torch 2.15.0+cpu` not audit-able on PyPI |
| Bandit | `python -m bandit -r app -x app/tests` | 7 **LOW** findings, 0 medium/high (see below) |
| Tracked-secret scan | `git ls-files` + `git grep` for keys/tokens | Only `.env.example` files; no key material matched |
| Playwright execution | — | **Not run** (see blockers) |
| Demo seed | `python scripts/seed_demo.py` | **Not run** (requires live full stack) |
| Backup restore | — | **Not run** (no isolated restore database) |

The two backend skips are intentional opt-in integrations:
`VAULTRAG_RUN_MODEL_INTEGRATION=1` (real embedding model) and
`OLLAMA_INTEGRATION=1` (real Ollama generation). Neither was enabled.

Bandit's seven low-severity findings are reviewed false positives: OAuth token
URL constants and the `bearer` scheme are classified as hardcoded passwords, and
three internal invariants use `assert`. No medium or high finding was reported.

## Backend coverage relevant to this milestone

Executed in the full suite or in focused runs:

- feedback create/update/retract, duplicate handling, ownership and admin review;
- knowledge-gap classification, permission-denial exclusion, redaction and access
  control;
- tenant settings defaults, validation, history and rollback;
- analytics sections and non-admin denial;
- explainability contract, citation verification and history persistence;
- liveness, readiness and content-free request logging;
- capability catalogue now including the four new ADMIN capabilities;
- existing RLS, tenant-isolation, ABAC, ingestion, RAG, graph, integration,
  evaluation, security and version suites.

The full suite starts from an empty test database each session, so the new
migration and its RLS/grants are exercised on every run.

## Operations checks

- **Compose:** all services, worker profile, environment interpolation and volume
  references parse successfully. No containers were started for this check.
- **Backup:** produced `backups/vaultrag-<timestamp>.dump`, verified it with
  `pg_restore --list` (4,000+ line TOC written alongside), and archived the
  document volume. The first run exposed a broken-pipe false failure in the
  verification pipeline; both PowerShell and Bash scripts were corrected to
  redirect the listing to a file before checking the exit code, and the
  PowerShell script then passed end to end.
- **Restore:** deliberately not executed against the working database because it
  is destructive without an isolated target. The deployment guide requires
  restore testing before production use.

## End-to-end status

`frontend/e2e/milestone10.spec.ts` adds three scenarios: administrator access to
Feedback, Knowledge gaps, Analytics and Settings; evaluation reachability; and
employee denial of admin routes. `npx playwright test --list` confirms the spec
compiles and is discovered (12 tests total).

Execution is **blocked** because the Playwright configuration requires a running
API at the configured base URL, a web server, and externally seeded administrator
and employee credentials (`E2E_*`). Those were not provisioned in this session,
so no browser scenario is reported as passing.

`backend/scripts/seed_demo.py` was authored for this gap. It seeds two synthetic
tenants through the HTTP APIs plus the supported out-of-band `bootstrap_admin`
operator command, imports the synthetic corpus, grants employee READ access,
creates conversations/feedback, records repeated unanswered queries and runs one
retrieval-only evaluation. It was syntax-checked and linted but not executed
against a live API, so its end-to-end behavior remains unverified.

## Not verified / limitations

- Playwright UI execution and mobile navigation with the new pages.
- The demo seed script against a live API.
- Backup restore into an isolated environment.
- Real embedding and Ollama integration paths (explicit opt-ins).
- Responsive layout and screen-reader behavior were not audited; no WCAG
  certification is claimed. Charts include adjacent summaries/tables, and
  lint/typecheck/build pass.
- No new load or latency benchmark was run for this milestone. No performance
  improvement is claimed.
- No independent security audit or penetration test.
- No deployment or publish operation was performed.

## Acceptance gaps reported honestly

These acceptance criteria are **not** claimed as met:

- Phase 5: a dedicated integration test proving version metadata after a
  document rollback. Existing rollback and explainability tests pass separately.
- Phase 6: endpoint-by-endpoint tenant-isolation tests for all eight analytics
  sections. Queries are tenant-filtered and RLS is exercised, but the dedicated
  matrix is absent.
- Phase 8: manual keyboard-only walkthrough, dialog focus-return checks and
  layout checks at 375/768/1280 px. Build, lint, unit tests and static structure
  pass; no browser accessibility audit was run.
- Phase 9: stale job/ingestion recovery test, and Bash backup execution (the
  PowerShell backup passed; both scripts were corrected).
- Phase 10: live two-tenant Playwright execution and seed-script execution.
- Phase 12: backup restore and an independent security review.

All other phase items are implemented and verified by the checks above, or are
documented limitations (deterministic gap clustering, aggregate-only document
analytics, no tag/deploy).

## Verdict

All automated backend and frontend checks that can run in this environment pass,
the migration is drift-free, Compose parses, and the backup procedure works.
Browser E2E, seed execution and restore testing remain open and must be completed
against a provisioned stack before claiming those paths are verified.
