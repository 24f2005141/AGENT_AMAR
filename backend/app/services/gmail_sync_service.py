"""Incremental Gmail synchronisation (Phase 12).

Prevents AGENT AMAR from ingesting the user's entire historical unread inbox.

    First connect        →  ensure_baseline()   record monitoring_started_at +
                                                the current mailbox historyId;
                                                process **nothing**.
    Every cycle after    →  sync_new_messages()  Gmail History API since
                                                last_history_id → process only
                                                newly-added messages → then
                                                persist the new historyId.

Reuses the existing pipeline end to end:
    GmailService → MailIntakeAgent → AMAROrchestrator → PersistenceService.
``PersistenceService`` is idempotent on ``email_id``, so a crash / repeated
history event / overlapping run never creates duplicate rows.

The scheduler job and the manual ``POST /api/v1/gmail/sync`` endpoint both call
:meth:`sync_new_messages`. An in-process lock keeps a scheduled run and a manual
run from overlapping.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.agents.amar_orchestrator import AMAROrchestrator, build_default_orchestrator
from app.agents.intake_agent import MailIntakeAgent
from app.core.config import Settings, get_settings
from app.core.errors import GmailHistoryExpiredError, GmailIntegrationError, MessageNotFoundError
from app.db.base import utcnow
from app.db.models import SYNC_DEFAULT_USER, GmailSyncState
from app.models.email import NormalizedEmail
from app.repositories import GmailSyncRepository
from app.services.gmail_service import GmailService
from app.services.persistence_service import PersistenceService

logger = logging.getLogger("agent_amar.gmail_sync")

# One process, one scheduler + FastAPI threadpool — a plain lock is enough to
# keep a scheduled sync and a manual /sync from running the same window twice.
_SYNC_LOCK = threading.Lock()


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


class GmailSyncService:
    def __init__(self, session: Session, *, settings: Settings | None = None) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.states = GmailSyncRepository(session)

    # -- read ---------------------------------------------------------

    def get_state(self, user_id: str = SYNC_DEFAULT_USER) -> GmailSyncState | None:
        return self.states.get(user_id)

    # -- baseline ---------------------------------------------------

    def ensure_baseline(
        self,
        gmail: GmailService,
        *,
        now: datetime | None = None,
        account_email: str | None = None,
        user_id: str = SYNC_DEFAULT_USER,
    ) -> GmailSyncState:
        """Record the monitoring baseline (current historyId). Idempotent —
        if a baseline already exists it is returned unchanged. **No email is
        processed here.**"""
        now = _aware(now) or utcnow()
        state = self.states.get(user_id)
        if state is not None and state.last_history_id:
            return state

        history_id = gmail.get_history_id()
        email = account_email or gmail.get_profile_email()

        if state is None:
            state = GmailSyncState(user_id=user_id)
            self.session.add(state)
        if state.monitoring_started_at is None:
            state.monitoring_started_at = now
        state.last_history_id = history_id
        state.last_sync_at = now
        if email:
            state.account_email = email
        self.session.commit()
        self.session.refresh(state)
        logger.info(
            "gmail sync baseline established (history_id=%s, from now on only new mail is processed)",
            history_id,
        )
        return state

    # -- incremental sync ----------------------------------------

    def sync_new_messages(
        self,
        gmail: GmailService,
        *,
        intake: MailIntakeAgent | None = None,
        orchestrator: AMAROrchestrator | None = None,
        persistence: PersistenceService | None = None,
        now: datetime | None = None,
        user_id: str = SYNC_DEFAULT_USER,
    ) -> dict[str, Any]:
        """Process messages added since ``last_history_id``; then advance it.

        Returns a summary dict. ``status`` is one of:
        ``baselined`` · ``synced`` · ``history_expired_rebaselined`` ·
        ``skipped_locked``.
        """
        if not _SYNC_LOCK.acquire(blocking=False):
            logger.info("gmail sync skipped — another sync is already running")
            return {"status": "skipped_locked", "processed": 0,
                    "new_message_ids": [], "errors": []}
        try:
            return self._sync_locked(
                gmail, intake=intake, orchestrator=orchestrator,
                persistence=persistence, now=now, user_id=user_id,
            )
        finally:
            _SYNC_LOCK.release()

    def _sync_locked(
        self,
        gmail: GmailService,
        *,
        intake: MailIntakeAgent | None,
        orchestrator: AMAROrchestrator | None,
        persistence: PersistenceService | None,
        now: datetime | None,
        user_id: str,
    ) -> dict[str, Any]:
        now = _aware(now) or utcnow()
        state = self.states.get(user_id)

        # never connected / never baselined → baseline now, process nothing
        if state is None or not state.last_history_id:
            state = self.ensure_baseline(gmail, now=now, user_id=user_id)
            return {
                "status": "baselined",
                "monitoring_started_at": _iso(state.monitoring_started_at),
                "last_history_id": state.last_history_id,
                "processed": 0, "new_message_ids": [], "errors": [],
            }

        start_history_id = state.last_history_id
        try:
            message_ids, latest_history_id = gmail.list_added_message_ids_since(
                start_history_id, max_messages=self.settings.gmail_sync_max_messages
            )
        except GmailHistoryExpiredError:
            new_history_id = gmail.get_history_id()
            state.last_history_id = new_history_id
            state.last_sync_at = now
            self.session.commit()
            self.session.refresh(state)
            logger.warning(
                "gmail history expired (was %s) — re-baselined to %s",
                start_history_id, new_history_id,
            )
            return {
                "status": "history_expired_rebaselined",
                "from_history_id": start_history_id,
                "last_history_id": new_history_id,
                "processed": 0, "new_message_ids": [], "errors": [],
            }

        intake = intake or MailIntakeAgent(self.settings)
        orchestrator = orchestrator or build_default_orchestrator(self.settings)
        persistence = persistence or PersistenceService(self.session)

        processed: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []

        for message_id in message_ids:
            try:
                raw = gmail.get_message(message_id)
            except (GmailIntegrationError, MessageNotFoundError) as exc:
                errors.append({"message_id": message_id, "error": exc.public_message})
                continue
            try:
                intake_out = intake.run(raw)
                normalized = NormalizedEmail.model_validate(intake_out.data)
                decision_env = orchestrator.process(normalized, intake_out, now=now)
                record = persistence.persist_decision(normalized, decision_env)
                processed.append(
                    {
                        "email_id": record.email_id,
                        "created": len(record.processing_runs) == 1,
                        "priority_level": record.priority_level,
                        "final_category": record.final_category,
                    }
                )
            except Exception as exc:  # noqa: BLE001 — one bad message must not stall sync
                logger.warning(
                    "gmail sync: message %s failed (%s)", message_id, type(exc).__name__
                )
                errors.append({"message_id": message_id, "error": type(exc).__name__})

        # advance the resume point ONLY after the batch has been processed
        state.last_history_id = str(latest_history_id or start_history_id)
        state.last_sync_at = now
        self.session.commit()
        self.session.refresh(state)

        logger.info(
            "gmail sync completed: new=%d processed=%d errors=%d (history %s -> %s)",
            len(message_ids), len(processed), len(errors),
            start_history_id, state.last_history_id,
        )
        return {
            "status": "synced",
            "from_history_id": start_history_id,
            "last_history_id": state.last_history_id,
            "last_sync_at": _iso(state.last_sync_at),
            "new_message_ids": message_ids,
            "processed": len(processed),
            "results": processed,
            "errors": errors,
        }
