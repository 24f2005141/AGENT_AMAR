"""Sorted background worker — Gmail monitoring, deadline/reminder cycles, push.

Why a separate process
----------------------
In development the scheduler runs inside the FastAPI process (convenient: one
command). That is wrong for production:

* Gmail sync and LLM inference are slow; sharing a process with the API means a
  long inference can starve request handling.
* Every API replica would run its own copy of the scheduler, so N replicas =
  N duplicate Gmail syncs and N duplicate notification attempts.
* Restarting the API for a deploy would interrupt in-flight processing.

So production runs exactly **one** worker container (`python -m worker`) with
`SCHEDULER_ENABLED=false` on the API containers. The existing per-user sync
locks and the idempotent, checkpointed sync design mean a brief overlap during
a deploy is safe, but steady-state should be a single worker.

The queue
---------
The existing scheduler already IS the job pump: it walks connected users on an
interval, and `GmailSyncService` is idempotent and checkpointed (history id per
user, per-user lock, `persist_decision` keyed on `email_id`). No Redis/Celery
is introduced here — that would add two moving parts and a new failure mode for
a workload that is a handful of users on a periodic tick. If throughput ever
needs real fan-out, this is the single place to swap the pump.
"""

from __future__ import annotations

import asyncio
import signal
import sys

from app.core import crypto
from app.core.config import enforce_production_config, get_settings
from app.core.logging_setup import install as install_log_redaction
from app.core.logging_setup import secure_logger
from app.db.session import init_db
from app.services import llm_concurrency
from app.services.scheduler import get_scheduler

logger = secure_logger("agent_amar.worker")


async def _run() -> int:
    install_log_redaction()
    settings = get_settings()
    enforce_production_config(settings)
    crypto.configure(settings)
    llm_concurrency.configure(
        settings.llm_max_concurrent_requests, settings.llm_queue_timeout_seconds
    )

    if not settings.scheduler_enabled:
        logger.error(
            "SCHEDULER_ENABLED=false — the worker would do nothing. Set it to "
            "true for the worker process (and false for the API processes)."
        )
        return 2

    # Verifies the schema is migrated in production; creates tables in dev.
    init_db()

    # The scheduler's loops are asyncio tasks, so it must be started from
    # inside a running event loop — this process owns that loop.
    scheduler = get_scheduler()
    scheduler.start()
    logger.info(
        "worker started (gmail_sync=%s interval=%ss deadline=%ss reminder=%ss)",
        settings.gmail_sync_enabled,
        settings.gmail_sync_interval_seconds,
        settings.deadline_check_interval_seconds,
        settings.reminder_check_interval_seconds,
    )

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, ValueError, OSError):
            # Windows has no add_signal_handler for SIGTERM; fall back to the
            # plain handler. Containers (Linux) take the path above.
            try:
                signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))
            except (ValueError, OSError):
                pass

    try:
        await stop.wait()
    finally:
        logger.info("worker stopping scheduler…")
        await scheduler.stop()
        logger.info("worker stopped")
    return 0


def main() -> int:
    return asyncio.run(_run())


if __name__ == "__main__":
    sys.exit(main())
