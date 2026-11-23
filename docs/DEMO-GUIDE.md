# VaultRAG Demo Guide (10–15 minutes)

Full API commands are in [demo/DEMO_SCRIPT.md](demo/DEMO_SCRIPT.md). Use only
synthetic users and documents.

## Before the demo

1. Start the stack and confirm `/health/ready`.
2. Seed two tenants with `python backend/scripts/seed_demo.py --api <url>` or
   prepare equivalent data. The seed script was authored for this purpose but was
   not executed in the validation environment; verify it before presenting.
3. Have one administrator and one employee account ready for each tenant.
4. Keep tokens, tenant IDs and real documents out of screenshots.

## Timed flow

| Time | Step | Point |
|---|---|---|
| 0:00 | Show the sign-in and employee assistant | Role-aware entry, no admin navigation |
| 1:00 | Ask a policy question | Grounded answer, grouped citations, evidence strength |
| 2:30 | Expand **Why this answer** | Mode, contributions, version, citation verification — no prompts |
| 3:30 | Submit feedback; open `/admin/feedback` as admin | Owner flow and reviewer-only citations |
| 5:00 | Open **Knowledge gaps** | Permission denials excluded, redacted questions, minimum group size |
| 6:30 | Re-evaluate a gap | Real pipeline result, not a simulated answer |
| 8:00 | Open **Analytics** | Usage, retrieval quality, aggregate-only documents |
| 9:30 | Open **Settings**, save and roll back | Validation, version history, security cannot be weakened |
| 11:00 | Revoke a document permission and ask again | Immediate revocation; no unauthorized evidence |
| 12:30 | Show `/health/live`, `/health/ready`, backup script | Operations readiness |
| 14:00 | Close with evaluation page and honest limitations | Measured metrics only; E2E/restore status stated |

## Strongest security moment

Before and after revoking permission, ask the identical question. The authorized
answer cites the document; after revocation the source disappears and the answer
reports insufficient evidence. This behavior is covered by backend regression
tests and does not rely on model obedience.

## Do not claim

- That Playwright, backup restore, real Ollama or live provider integrations were
  verified unless they were run in that environment.
- Any accuracy or latency number not shown by an actual evaluation run.
- WCAG certification or an independent security audit.
