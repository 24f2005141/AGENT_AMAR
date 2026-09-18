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

import threading
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.agents.amar_orchestrator import AMAROrchestrator, build_default_orchestrator
from app.agents.intake_agent import MailIntakeAgent
from app.core.config import Settings, get_settings
from app.core.logging_setup import secure_logger
from app.core.errors import GmailHistoryExpiredError, GmailIntegrationError, MessageNotFoundError
from app.db.base import utcnow
from app.db.models import GmailSyncState
from app.models.email import NormalizedEmail
from app.repositories import EmailRepository, GmailSyncRepository
from app.services.audit_service import audit_record
from app.services.gmail_service import GmailService, is_spam_message
from app.services.persistence_service import PersistenceService

logger = secure_logger("agent_amar.gmail_sync")

# One process, one scheduler + FastAPI threadpool. Phase 15: a lock *per user* so
# two users sync concurrently but a scheduled + a manual sync for the SAME user
# never run the same window twice.
_LOCKS_GUARD = threading.Lock()
_SYNC_LOCKS: dict[int, threading.Lock] = {}


def _lock_for(user_pk: int) -> threading.Lock:
    with _LOCKS_GUARD:
        return _SYNC_LOCKS.setdefault(user_pk, threading.Lock())


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


class GmailSyncService:
    def __init__(
        self, session: Session, *, user_pk: int, settings: Settings | None = None
    ) -> None:
        self.session = session
        self.user_pk = user_pk
        self.settings = settings or get_settings()
        self.states = GmailSyncRepository(session)

    # -- read ---------------------------------------------------------

    def get_state(self) -> GmailSyncState | None:
        return self.states.get(self.user_pk)

    # -- baseline ---------------------------------------------------

    def ensure_baseline(
        self,
        gmail: GmailService,
        *,
        now: datetime | None = None,
        account_email: str | None = None,
        force: bool = False,
    ) -> GmailSyncState:
        """Record the monitoring baseline (current historyId). **No email is
        processed here.**

        Idempotent by default — an existing baseline is returned unchanged.
        ``force=True`` (used when the user (re)connects Gmail) always re-anchors
        the cursor to *now*, so reconnecting never replays the mail that arrived
        while the account was disconnected / the old cursor was stale.
        """
        now = _aware(now) or utcnow()
        state = self.states.get(self.user_pk)
        if state is not None and state.last_history_id and not force:
            return state

        history_id = gmail.get_history_id()
        email = account_email or gmail.get_profile_email()

        if state is None:
            state = GmailSyncState(user_pk=self.user_pk)
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
        # audit: no email address here — only the opaque account key + cursor
        audit_record("GMAIL_CONNECTED", "gmail_account", resource_id=str(self.user_pk),
                     user_pk=self.user_pk, detail={"baseline_history_id": history_id})
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
    ) -> dict[str, Any]:
        """Process messages added since ``last_history_id``; then advance it.

        Returns a summary dict. ``status`` is one of:
        ``baselined`` · ``synced`` · ``history_expired_rebaselined`` ·
        ``skipped_locked``.
        """
        lock = _lock_for(self.user_pk)
        if not lock.acquire(blocking=False):
            logger.info("gmail sync skipped — another sync is already running for this user")
            return {"status": "skipped_locked", "processed": 0,
                    "new_message_ids": [], "errors": []}
        try:
            return self._sync_locked(
                gmail, intake=intake, orchestrator=orchestrator,
                persistence=persistence, now=now,
            )
        finally:
            lock.release()

    def _sync_locked(
        self,
        gmail: GmailService,
        *,
        intake: MailIntakeAgent | None,
        orchestrator: AMAROrchestrator | None,
        persistence: PersistenceService | None,
        now: datetime | None,
    ) -> dict[str, Any]:
        now = _aware(now) or utcnow()
        state = self.states.get(self.user_pk)

        # never connected / never baselined → baseline now, process nothing
        if state is None or not state.last_history_id:
            state = self.ensure_baseline(gmail, now=now)
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
        persistence = persistence or PersistenceService(self.session, user_pk=self.user_pk)

        processed: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []

        for message_id in message_ids:
            try:
                raw = gmail.get_message(message_id)
            except (GmailIntegrationError, MessageNotFoundError) as exc:
                errors.append({"message_id": message_id, "error": exc.public_message})
                continue
            if is_spam_message(raw):
                # Defense in depth — list_added_message_ids_since already
                # excludes spam from `message_ids` using history-event label
                # metadata; this re-check covers a label changing in the
                # instant between that scan and the fetch. Never processed,
                # never persisted, never notified on.
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

        # Spam label transitions — an already-ingested email that later gets
        # Gmail's SPAM label (or one that loses it) does not raise a
        # `messageAdded` history event, so it needs its own scan. Never fails
        # the sync — a problem here must not block ingesting new mail.
        spam_marked = spam_restored = 0
        try:
            spam_now, spam_cleared = gmail.list_spam_label_changes_since(start_history_id)
            spam_marked, spam_restored = self._apply_spam_transitions(
                gmail, spam_now, spam_cleared,
                intake=intake, orchestrator=orchestrator, persistence=persistence, now=now,
            )
        except GmailHistoryExpiredError:
            pass  # the primary scan above already succeeded against this window
        except Exception:  # noqa: BLE001 — advisory signal, never blocks sync
            logger.warning("gmail sync: spam label-change scan failed", exc_info=True)

        # advance the resume point ONLY after the batch has been processed
        state.last_history_id = str(latest_history_id or start_history_id)
        state.last_sync_at = now
        self.session.commit()
        self.session.refresh(state)

        logger.info(
            "gmail sync completed: new=%d processed=%d errors=%d spam_marked=%d "
            "spam_restored=%d (history %s -> %s)",
            len(message_ids), len(processed), len(errors), spam_marked, spam_restored,
            start_history_id, state.last_history_id,
        )
        audit_record("GMAIL_SYNCED", "gmail_account", resource_id=str(self.user_pk),
                     user_pk=self.user_pk,
                     detail={"new": len(message_ids), "processed": len(processed),
                             "errors": len(errors), "spam_marked": spam_marked,
                             "spam_restored": spam_restored})
        return {
            "status": "synced",
            "from_history_id": start_history_id,
            "last_history_id": state.last_history_id,
            "spam_marked": spam_marked,
            "spam_restored": spam_restored,
            "last_sync_at": _iso(state.last_sync_at),
            "new_message_ids": message_ids,
            "processed": len(processed),
            "results": processed,
            "errors": errors,
        }

    def _apply_spam_transitions(
        self,
        gmail: GmailService,
        spam_now: set[str],
        spam_cleared: set[str],
        *,
        intake: MailIntakeAgent,
        orchestrator: AMAROrchestrator,
        persistence: PersistenceService,
        now: datetime,
    ) -> tuple[int, int]:
        """React to Gmail SPAM label changes on already-known messages.

        * ``spam_now`` — mark the matching :class:`EmailRecord` (if one exists)
          ``is_spam=True``. The Gmail message is never touched or deleted; the
          row just stops appearing anywhere in the app and gets no further
          notifications. If we never ingested it, there is nothing to do.
        * ``spam_cleared`` — the message is eligible for normal processing
          again. Re-run it through the existing pipeline exactly like a fresh
          message; `persist_decision` is idempotent on `email_id` so this
          never duplicates a row, whether or not one already existed.

        Returns ``(marked_count, restored_count)``. Never raises — a failure
        on one message must not stop the rest or the surrounding sync.
        """
        prefix = self.settings.gmail_id_prefix
        marked = 0
        if spam_now:
            emails = EmailRepository(self.session)
            changed = False
            for mid in spam_now:
                record = emails.get_by_email_id(f"{prefix}{mid}", user_pk=self.user_pk)
                if record is None or record.is_spam:
                    continue
                record.is_spam = True
                marked += 1
                changed = True
            if changed:
                self.session.commit()

        restored = 0
        for mid in spam_cleared:
            try:
                raw = gmail.get_message(mid)
            except (GmailIntegrationError, MessageNotFoundError):
                continue
            if is_spam_message(raw):
                continue  # label flapped again since the history scan
            try:
                intake_out = intake.run(raw)
                normalized = NormalizedEmail.model_validate(intake_out.data)
                decision_env = orchestrator.process(normalized, intake_out, now=now)
                record = persistence.persist_decision(normalized, decision_env)
                if record.is_spam:
                    record.is_spam = False
                    self.session.commit()
                restored += 1
            except Exception:  # noqa: BLE001 — one message must not stall the rest
                logger.warning(
                    "gmail sync: reprocessing unspammed message %s failed", mid, exc_info=True
                )
        return marked, restored
