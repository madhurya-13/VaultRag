"""Knowledge graph extraction worker for one tenant.

Usage
-----
    python scripts/graph_worker.py --tenant-id <uuid>
    python scripts/graph_worker.py --tenant-id <uuid> --once

The worker is deliberately per-tenant: it sets the RLS tenant context on every
transaction instead of relying on a privileged connection, so a bug in the
worker cannot read another tenant's documents or graph.

Run under a process supervisor. ``--once`` drains the queue and exits, which is
what the tests and one-off reprocessing use.
"""

from __future__ import annotations

import argparse
import logging
import signal
import time
from uuid import UUID

from app.core.config import get_settings
from app.core.database import SessionLocal, set_tenant_context
from app.services.graph.jobs import run_next_job

logger = logging.getLogger("graph_worker")

_STOP = False


def _handle_signal(signum, frame) -> None:  # pragma: no cover - signal handling
    global _STOP
    _STOP = True
    logger.info("Shutdown requested (signal %s)", signum)


def run_once(tenant_id: UUID) -> dict | None:
    """Process at most one job, returning its summary (or None when idle)."""
    with SessionLocal() as session:
        with session.begin():
            set_tenant_context(session, tenant_id)
            result = run_next_job(session, tenant_id=tenant_id)
    return result


def drain(tenant_id: UUID) -> int:
    processed = 0
    while not _STOP:
        result = run_once(tenant_id)
        if result is None:
            break
        processed += 1
        logger.info(
            "KG job finished job_id=%s document_id=%s status=%s",
            result.get("job_id"),
            result.get("document_id"),
            result.get("status"),
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
    if not settings.knowledge_graph_enabled:
        logger.error("KNOWLEDGE_GRAPH_ENABLED is false; nothing to do")
        return 1

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    tenant_id: UUID = args.tenant_id
    logger.info("Knowledge graph worker started tenant_id=%s", tenant_id)

    if args.once:
        processed = drain(tenant_id)
        logger.info("Drained %d job(s)", processed)
        return 0

    poll = settings.knowledge_graph_worker_poll_seconds
    while not _STOP:
        try:
            result = run_once(tenant_id)
        except Exception:
            # A transient database error must not kill the worker; back off.
            logger.exception("Graph worker iteration failed")
            time.sleep(min(poll * 5, 30))
            continue
        if result is None:
            time.sleep(poll)
        else:
            logger.info(
                "KG job finished job_id=%s document_id=%s status=%s",
                result.get("job_id"),
                result.get("document_id"),
                result.get("status"),
            )

    logger.info("Knowledge graph worker stopped tenant_id=%s", tenant_id)
    return 0


if __name__ == "__main__":  # pragma: no cover - entry point
    raise SystemExit(main())
