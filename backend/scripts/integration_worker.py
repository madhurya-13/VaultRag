"""Integration synchronization worker for one tenant.

Usage
-----
    python scripts/integration_worker.py --tenant-id <uuid>
    python scripts/integration_worker.py --tenant-id <uuid> --once

The worker is per tenant and sets the RLS tenant context on every transaction.
It never uses a BYPASSRLS role. Each iteration first schedules any integration
whose ``next_sync_at`` is due, then runs queued jobs one at a time. The partial
unique index, not the worker, guarantees one active job per integration, so
running a second worker cannot duplicate a synchronization.

Run one worker per tenant under a supervisor. ``--once`` drains the queue and
exits, which is what local backfills and tests use.
"""

from __future__ import annotations

import argparse
import logging
import signal
import time
from uuid import UUID

from app.core.config import get_settings
from app.core.database import SessionLocal, set_tenant_context
from app.services.integrations.sync import (
    claim_due_jobs,
    run_sync_job,
    schedule_due_integrations,
)

logger = logging.getLogger("integration_worker")

_STOP = False


def _handle_signal(signum, frame) -> None:  # pragma: no cover - signal handling
    global _STOP
    _STOP = True
    logger.info("Shutdown requested (signal %s)", signum)


def run_once(tenant_id: UUID) -> dict | None:
    """Run at most one queued job, returning a safe summary (or None)."""
    with SessionLocal() as session:
        with session.begin():
            set_tenant_context(session, tenant_id)
            schedule_due_integrations(SessionLocal, tenant_id=tenant_id)
    with SessionLocal() as session:
        with session.begin():
            set_tenant_context(session, tenant_id)
            pending = claim_due_jobs(SessionLocal, tenant_id=tenant_id, limit=1)
    if not pending:
        return None
    summary = run_sync_job(tenant_id=tenant_id, sync_job_id=pending[0])
    return {
        "sync_job_id": str(summary.sync_job_id),
        "status": summary.sync_status,
        "processed": summary.files_processed,
        "failed": summary.files_failed,
        "deleted": summary.documents_deleted,
    }


def drain(tenant_id: UUID) -> int:
    processed = 0
    while not _STOP:
        result = run_once(tenant_id)
        if result is None:
            break
        processed += 1
        logger.info(
            "Integration sync finished job_id=%s status=%s processed=%d failed=%d",
            result["sync_job_id"],
            result["status"],
            result["processed"],
            result["failed"],
        )
    return processed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", required=True, type=UUID)
    parser.add_argument("--once", action="store_true", help="Drain and exit")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    settings = get_settings()
    if not settings.integrations_enabled:
        logger.error("INTEGRATIONS_ENABLED is false; nothing to do")
        return 1

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    tenant_id: UUID = args.tenant_id
    logger.info("Integration worker started tenant_id=%s", tenant_id)

    if args.once:
        logger.info("Drained %d job(s)", drain(tenant_id))
        return 0

    poll = settings.integration_sync_worker_poll_seconds
    while not _STOP:
        try:
            run_once(tenant_id)
        except Exception:
            logger.exception("Integration worker iteration failed")
            time.sleep(min(poll * 5, 30))
            continue
        time.sleep(poll)

    logger.info("Integration worker stopped tenant_id=%s", tenant_id)
    return 0


if __name__ == "__main__":  # pragma: no cover - entry point
    raise SystemExit(main())
