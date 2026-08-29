"""In-process background scheduler (Phase 11B.1).

One lightweight ``asyncio`` loop per job — **no** Celery / Redis / RabbitMQ /
APScheduler. Each loop wakes on a configurable interval, runs an *existing*
service inside its own short-lived DB session, logs a one-line structured
summary, and sleeps again.

Design
------
* Starts in the FastAPI ``lifespan`` startup, stops (with graceful task
  cancellation) on shutdown.
* Contains **no business logic** — it only calls
  :class:`~app.services.deadline_monitor_service.DeadlineMonitorService`, the
  same service the manual ``POST /api/v1/monitor/deadlines/check`` endpoint
  uses.
* The sync service runs in a worker thread (``asyncio.to_thread``) so a cycle
  never blocks the event loop / API requests.
* A job exception is caught, counted and logged — it never propagates, never
  cancels the loop, never crashes the app. The next tick runs normally.
* All state that matters (notifications, reminder status, monitoring flags)
  lives in the database, so a restart resumes cleanly and never double-fires.

Known limitation
----------------
**Single instance only.** Each backend process runs its own scheduler. Two
processes would each tick; the DB-state deduplication makes that *mostly*
harmless but is not a distributed lock. Do not run multiple replicas with the
scheduler enabled — run one, or move to an external scheduler, before scaling
out.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timezone

from app.core.config import Settings, get_settings
from app.db.session import db_session
from app.services.deadline_monitor_service import DeadlineMonitorService

logger = logging.getLogger("agent_amar.scheduler")

# small delay before the first tick so app startup finishes first
_WARMUP_SECONDS = 0.5


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


class MonitorScheduler:
    """Owns the deadline + reminder background loops."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._tasks: list[asyncio.Task] = []
        self._running = False
        self.started_at: datetime | None = None
        # observability counters
        self.last_deadline_check: datetime | None = None
        self.last_reminder_check: datetime | None = None
        self.last_gmail_sync: datetime | None = None
        self.cycles: dict[str, int] = {"deadline": 0, "reminder": 0, "gmail": 0}
        self.failures: dict[str, int] = {"deadline": 0, "reminder": 0, "gmail": 0}
        self.last_error: str | None = None

    # -- lifecycle -----------------------------------------------------

    @property
    def running(self) -> bool:
        return self._running

    def start(self) -> None:
        """Idempotent. No-op when disabled or already running. Non-blocking."""
        if self._running:
            return
        if not self.settings.scheduler_enabled:
            logger.info("scheduler disabled (scheduler_enabled=false) — not starting")
            return
        self._running = True
        self.started_at = _utcnow()
        self._tasks = [
            asyncio.ensure_future(
                self._loop(
                    "deadline",
                    self.settings.deadline_check_interval_seconds,
                    self._deadline_cycle,
                )
            ),
            asyncio.ensure_future(
                self._loop(
                    "reminder",
                    self.settings.reminder_check_interval_seconds,
                    self._reminder_cycle,
                )
            ),
        ]
        if self.settings.gmail_sync_enabled:
            self._tasks.append(
                asyncio.ensure_future(
                    self._loop(
                        "gmail",
                        self.settings.gmail_sync_interval_seconds,
                        self._gmail_cycle,
                    )
                )
            )
        logger.info(
            "scheduler started (deadline_interval=%ss reminder_interval=%ss gmail_sync=%s)",
            self.settings.deadline_check_interval_seconds,
            self.settings.reminder_check_interval_seconds,
            (
                f"{self.settings.gmail_sync_interval_seconds}s"
                if self.settings.gmail_sync_enabled
                else "off"
            ),
        )

    async def stop(self) -> None:
        """Cancel the loops and await their exit. Idempotent."""
        if not self._running:
            return
        self._running = False
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001 — shutdown must not raise
                logger.exception("scheduler task raised on shutdown")
        self._tasks.clear()
        logger.info("scheduler stopped")

    # -- loop --------------------------------------------------------

    async def _loop(self, name: str, interval: int, cycle: Callable[[], None]) -> None:
        interval = max(1, int(interval))
        try:
            await asyncio.sleep(min(_WARMUP_SECONDS, interval))
        except asyncio.CancelledError:
            return
        while self._running:
            await asyncio.to_thread(self._run_safely, name, cycle)
            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                return

    def _run_safely(self, name: str, cycle: Callable[[], None]) -> None:
        """Run one cycle; swallow + record any error so the loop survives."""
        try:
            cycle()
        except Exception as exc:  # noqa: BLE001 — a bad cycle must not kill the scheduler
            self.failures[name] = self.failures.get(name, 0) + 1
            self.last_error = f"{name}: {type(exc).__name__}: {exc}"
            logger.exception("scheduler job failed: %s (will retry next tick)", name)

    # -- jobs (thin wrappers over the existing service) -------------

    def _deadline_cycle(self) -> None:
        logger.info("deadline monitoring cycle started")
        with db_session() as session:
            result = DeadlineMonitorService(session).run_deadline_check(
                include_reminders=False
            )
        self.cycles["deadline"] += 1
        self.last_deadline_check = _utcnow()
        logger.info(
            "deadline monitoring cycle completed: deadlines_evaluated=%d notifications_created=%d",
            result.deadlines_evaluated,
            result.notifications_created,
        )

    def _reminder_cycle(self) -> None:
        logger.info("reminder check started")
        with db_session() as session:
            result = DeadlineMonitorService(session).run_reminder_check()
        self.cycles["reminder"] += 1
        self.last_reminder_check = _utcnow()
        logger.info(
            "reminder check completed: reminders_due=%d notifications_created=%d",
            result.reminders_evaluated,
            result.notifications_created,
        )

    def _gmail_cycle(self) -> None:
        """Incremental Gmail sync — process only newly-added messages.

        Skips cleanly (no error) when Gmail is not connected. Builds a
        short-lived GmailService from the stored credentials; the sync itself
        is the same :class:`GmailSyncService` the manual endpoint uses.
        """
        from app.services.gmail_auth_service import GmailAuthService
        from app.services.gmail_service import GmailService
        from app.services.gmail_sync_service import GmailSyncService
        from app.services.token_store import FileTokenStore

        auth = GmailAuthService(
            self.settings, FileTokenStore(str(self.settings.token_storage_dir))
        )
        try:
            creds = auth.get_credentials()
        except Exception:  # noqa: BLE001 — expired/unreadable token = "not connected"
            creds = None
        if creds is None:
            logger.info("gmail sync cycle skipped — Gmail not connected")
            return

        logger.info("gmail sync cycle started")
        with db_session() as session:
            result = GmailSyncService(session, settings=self.settings).sync_new_messages(
                GmailService(credentials=creds)
            )
        self.cycles["gmail"] += 1
        self.last_gmail_sync = _utcnow()
        logger.info(
            "gmail sync cycle completed: status=%s new=%d processed=%d",
            result.get("status"),
            len(result.get("new_message_ids", [])),
            result.get("processed", 0),
        )

    # -- observability --------------------------------------------

    def status(self) -> dict:
        return {
            "scheduler": "running" if self._running else "stopped",
            "enabled": bool(self.settings.scheduler_enabled),
            "started_at": _iso(self.started_at),
            "deadline_check_interval_seconds": self.settings.deadline_check_interval_seconds,
            "reminder_check_interval_seconds": self.settings.reminder_check_interval_seconds,
            "gmail_sync_enabled": bool(self.settings.gmail_sync_enabled),
            "gmail_sync_interval_seconds": self.settings.gmail_sync_interval_seconds,
            "last_deadline_check": _iso(self.last_deadline_check),
            "last_reminder_check": _iso(self.last_reminder_check),
            "last_gmail_sync": _iso(self.last_gmail_sync),
            "deadline_cycles": self.cycles["deadline"],
            "reminder_cycles": self.cycles["reminder"],
            "gmail_cycles": self.cycles["gmail"],
            "deadline_failures": self.failures["deadline"],
            "reminder_failures": self.failures["reminder"],
            "gmail_failures": self.failures["gmail"],
            "last_error": self.last_error,
        }


# -- module singleton (used by the FastAPI lifespan + the status route) ---

_scheduler: MonitorScheduler | None = None


def get_scheduler() -> MonitorScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = MonitorScheduler()
    return _scheduler
