"""PersistenceService — turns a Final Decision Object into persistent state.

    AMAR Orchestrator → Final Decision Object → PersistenceService → DB

Rules (Phase 9 brief):
  * **Idempotent** on ``email_id`` — reprocessing UPDATEs, never duplicates.
  * **User-generated state is preserved** across reprocessing: ``is_viewed`` /
    ``viewed_at``, ``is_completed`` / ``completed_at``, ``snoozed_until``, an
    action's ``status``, a deadline's monitoring fields.
  * **System analysis is refreshed** every run (category, priority, actions,
    deadlines, routing).
  * **Processing history is append-only** — one ``ProcessingRun`` per pass.
  * A notification record is created only when routing says notify *and* one
    does not already exist for this email/type.

No intelligence here — it only maps fields.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import (
    ActionRecord,
    DeadlineRecord,
    EmailRecord,
    NotificationRecord,
    ProcessingRun,
)
from app.core.sanitization import mask_sensitive
from app.models.agent_output import AgentOutput
from app.models.decision import FinalDecision
from app.models.email import NormalizedEmail
from app.repositories import (
    ActionRepository,
    DeadlineRepository,
    EmailRepository,
    NotificationRepository,
    ProcessingRepository,
)
from app.services.audit_service import audit_record, audit_record_many
from app.services.push_service import dispatch_unpushed

# routing.notify already encodes User Preferences §3 (notify at HIGH+); we only
# add the "don't duplicate" guard here.
_NOTIFY_LEVELS = {"HIGH", "URGENT", "CRITICAL"}


class PersistenceService:
    def __init__(self, session: Session, *, user_pk: int | None = None) -> None:
        self.session = session
        self.user_pk = user_pk
        self.emails = EmailRepository(session)
        self.actions = ActionRepository(session)
        self.deadlines = DeadlineRepository(session)
        self.processing = ProcessingRepository(session)
        self.notifications = NotificationRepository(session)

    # -- public entry point --------------------------------------------

    def persist_decision(
        self, normalized: NormalizedEmail, decision_envelope: AgentOutput
    ) -> EmailRecord:
        """Persist one pipeline pass. Commits the transaction."""
        fd = FinalDecision.model_validate(decision_envelope.data)
        now = datetime.now(timezone.utc)

        record = self.emails.get_by_email_id(fd.email_id, with_children=True)
        created = record is None
        if created:
            record = EmailRecord(email_id=fd.email_id, user_pk=self.user_pk)
            self.emails.add(record)
        elif record.user_pk is None and self.user_pk is not None:
            # adopt a legacy unowned row on first reprocess under a real user
            record.user_pk = self.user_pk

        before_actions = {a.action_ref for a in record.actions}
        before_deadlines = {d.deadline_ref for d in record.deadlines}
        had_notification = bool(record.notifications)

        self._apply_identity_and_metadata(record, normalized, fd)
        self._apply_system_analysis(record, fd, now)
        self._upsert_actions(record, fd)
        self._upsert_deadlines(record, fd)
        self._recompute_completion(record, now)
        self._append_processing_run(record, fd, decision_envelope, now)
        self._maybe_create_notification(record, fd)

        self.session.commit()
        self.session.refresh(record)

        # Phase 14 — tamper-evident audit trail (non-sensitive metadata only,
        # one atomic append for the whole pass).
        eid = record.email_id
        uid = record.user_pk
        events: list[dict] = []
        if created:
            events.append({"event_type": "EMAIL_INGESTED", "resource_type": "email",
                           "resource_id": eid, "user_pk": uid,
                           "detail": {"source": record.source}})
        events.append({"event_type": "EMAIL_PROCESSED", "resource_type": "email",
                       "resource_id": eid, "user_pk": uid,
                       "detail": {"category": record.final_category,
                                  "priority": record.priority_level,
                                  "run": len(record.processing_runs)}})
        for a in record.actions:
            if a.action_ref not in before_actions:
                events.append({"event_type": "ACTION_CREATED", "resource_type": "action",
                               "resource_id": f"{eid}/{a.action_ref}", "user_pk": uid,
                               "detail": {"type": a.action_type, "blocking": a.blocking}})
        for d in record.deadlines:
            if d.deadline_ref not in before_deadlines:
                events.append({"event_type": "DEADLINE_CREATED", "resource_type": "deadline",
                               "resource_id": f"{eid}/{d.deadline_ref}", "user_pk": uid,
                               "detail": {"ambiguous": d.is_ambiguous, "is_past": d.is_past}})
        new_notification = bool(record.notifications) and not had_notification
        if new_notification:
            events.append({"event_type": "NOTIFICATION_SENT", "resource_type": "notification",
                           "resource_id": eid, "user_pk": uid,
                           "detail": {"type": record.notifications[-1].notification_type}})
        audit_record_many(events)

        # Phase 16 — backend push (own session, non-fatal). Fire only when this
        # pass created a notification so a re-process never re-pushes.
        if new_notification and uid is not None:
            try:
                dispatch_unpushed(uid)
            except Exception:  # noqa: BLE001 — push must never break persistence
                pass
        return record

    # -- field mapping ------------------------------------------------

    @staticmethod
    def _apply_identity_and_metadata(
        record: EmailRecord, normalized: NormalizedEmail, fd: FinalDecision
    ) -> None:
        record.thread_id = normalized.thread_id or fd.thread_id
        record.source = normalized.source or fd.source
        record.sender_name = normalized.sender.name
        record.sender_email = normalized.sender.email
        record.subject = normalized.subject or ""
        record.snippet = _preview(normalized.snippet or normalized.body)
        record.received_at = normalized.received_at
        # Gmail-derived state — refreshed every fetch (it is a Gmail fact,
        # not something the user set inside AMAR).
        record.is_unread = bool(normalized.is_unread)

    @staticmethod
    def _apply_system_analysis(record: EmailRecord, fd: FinalDecision, now: datetime) -> None:
        record.final_category = fd.final_category
        # Phase 18: the automated derivation always refreshes ``auto_primary_category``;
        # the canonical ``primary_category`` only follows it while the user has not
        # manually corrected this email (same "user state is preserved" rule as
        # is_viewed / is_completed).
        auto_primary = str(getattr(fd.primary_category, "value", fd.primary_category))
        record.auto_primary_category = auto_primary
        if getattr(record, "primary_category_source", "auto") != "user":
            record.primary_category = auto_primary
        record.category_confidence = fd.category_confidence
        record.priority_level = str(fd.priority_level)
        record.priority_score = int(fd.priority_score)
        record.proximity_bucket = str(fd.proximity_bucket)
        record.deadline_is_past = bool(fd.deadline_is_past)
        record.action_required = bool(fd.action_required)
        record.needs_human_review = bool(fd.needs_human_review)
        record.folder_label = fd.routing.folder_label
        record.should_notify = bool(fd.routing.notify)
        record.should_monitor = bool(fd.routing.monitor)
        record.processed_at = now

    def _upsert_actions(self, record: EmailRecord, fd: FinalDecision) -> None:
        seen: set[str] = set()
        by_ref = {a.action_ref: a for a in record.actions}
        for da in fd.actions:
            seen.add(da.action_id)
            row = by_ref.get(da.action_id)
            if row is None:
                row = ActionRecord(action_ref=da.action_id, status="PENDING")
                record.actions.append(row)
            # refresh system fields; PRESERVE row.status (user-generated)
            row.action_type = da.action_type
            row.description = da.action_description
            row.blocking = bool(da.blocking)
            row.target_link = da.target_link
            row.raw_deadline_hint = da.raw_deadline_hint
            row.confidence = float(da.confidence)
        # drop actions no longer detected — but only if the user never touched them
        for row in list(record.actions):
            if row.action_ref not in seen and row.status == "PENDING":
                record.actions.remove(row)

    def _upsert_deadlines(self, record: EmailRecord, fd: FinalDecision) -> None:
        seen: set[str] = set()
        by_ref = {d.deadline_ref: d for d in record.deadlines}
        for dd in fd.deadlines:
            seen.add(dd.deadline_id)
            row = by_ref.get(dd.deadline_id)
            if row is None:
                row = DeadlineRecord(deadline_ref=dd.deadline_id)
                record.deadlines.append(row)
            # refresh extraction fields; PRESERVE monitoring_* (system/Phase 10)
            row.deadline_datetime = _parse_iso(dd.normalized_deadline)
            row.source_text = dd.raw_deadline_text
            row.timezone = dd.timezone or "UTC"
            row.date_only = bool(dd.date_only)
            row.confidence = float(dd.confidence)
            row.is_ambiguous = bool(dd.ambiguity_flag)
            row.ambiguity_reason = dd.ambiguity_reason
            row.is_past = bool(dd.is_past)
            row.action_context = dd.action_context
            row.related_action_ref = dd.related_action_id
        for row in list(record.deadlines):
            if row.deadline_ref not in seen and not row.is_monitoring:
                record.deadlines.remove(row)

    @staticmethod
    def _recompute_completion(record: EmailRecord, now: datetime) -> None:
        """Derive ``is_completed`` from the actions' user-set statuses.

        **Promote-only.** Auto-completion (all blocking actions done) may set
        ``is_completed`` true, but this never sets it back to false — an email the
        user has resolved (``completion_source == "user"``) or that auto-completed
        on a previous pass must not be un-completed by a later Gmail sync that
        re-runs the agents and produces a slightly different action set. Undoing a
        completion is only ever an explicit user action (see ``reopen``).
        """
        if getattr(record, "completion_source", "auto") == "user" or record.is_completed:
            return
        if not record.action_required or not record.actions:
            return
        blocking = [a for a in record.actions if a.blocking]
        relevant = blocking or record.actions
        if all(a.status in ("COMPLETED", "DISMISSED") for a in relevant):
            record.is_completed = True
            record.completed_at = now

    def _append_processing_run(
        self,
        record: EmailRecord,
        fd: FinalDecision,
        envelope: AgentOutput,
        now: datetime,
    ) -> None:
        run = ProcessingRun(
            run_id=envelope.run_id,
            processed_at=now,
            status=str(envelope.status),
            pipeline_version=envelope.agent_version,
            final_category=fd.final_category,
            priority_level=str(fd.priority_level),
            priority_score=int(fd.priority_score),
            needs_human_review=bool(fd.needs_human_review),
            # sanitise the free-text analysis metadata before it is persisted —
            # an OTP / token quoted by an agent must not land in the DB / logs.
            summary=mask_sensitive(envelope.reasoning_summary) or None,
            agent_trace=[t.model_dump() for t in fd.agent_trace],
            conflicts_resolved=[
                {**c.model_dump(), "detail": mask_sensitive(c.model_dump().get("detail"))}
                for c in fd.conflicts_resolved
            ],
            review_reasons=[mask_sensitive(r) for r in fd.review_reasons],
            errors=[
                {**e.model_dump(), "message": mask_sensitive(e.model_dump().get("message"))}
                for e in envelope.errors
            ],
        )
        record.processing_runs.append(run)
        self.session.flush()

    def _maybe_create_notification(self, record: EmailRecord, fd: FinalDecision) -> None:
        if not fd.routing.notify or str(fd.priority_level) not in _NOTIFY_LEVELS:
            return
        if record.id is None:
            self.session.flush()
        if self.notifications.exists_for(record.id, "new_priority_email"):
            return
        self.notifications.create_pending(
            record.id,
            "new_priority_email",
            reminder_level="NORMAL",  # the base rung of the Phase 10 escalation ladder
            detail=f"{fd.final_category} / {fd.priority_level} (score {fd.priority_score})",
        )

    # -- user-state mutations (called by the state endpoints) --------

    def mark_viewed(self, email_id: str) -> EmailRecord | None:
        record = self.emails.get_by_email_id(
            email_id, user_pk=self.user_pk, with_children=True
        )
        if record is None or record.is_spam:
            return None
        newly_viewed = not record.is_viewed
        if newly_viewed:
            record.is_viewed = True
            record.viewed_at = datetime.now(timezone.utc)
        self.session.commit()
        self.session.refresh(record)
        if newly_viewed:
            audit_record("EMAIL_VIEWED", "email", resource_id=record.email_id,
                         user_pk=record.user_pk)
        return record

    def snooze(self, email_id: str, until: datetime) -> EmailRecord | None:
        record = self.emails.get_by_email_id(
            email_id, user_pk=self.user_pk, with_children=True
        )
        if record is None or record.is_spam:
            return None
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
        record.snoozed_until = until
        self.session.commit()
        self.session.refresh(record)
        return record

    def clear_snooze(self, email_id: str) -> EmailRecord | None:
        """Remove an active snooze (the email becomes eligible for escalation
        again immediately). No-op if it was not snoozed."""
        record = self.emails.get_by_email_id(
            email_id, user_pk=self.user_pk, with_children=True
        )
        if record is None or record.is_spam:
            return None
        record.snoozed_until = None
        self.session.commit()
        self.session.refresh(record)
        return record

    def complete_reply(self, email_id: str) -> EmailRecord | None:
        """Mark an email done because the user has sent a reply.

        Completes every pending ``REPLY`` action, then — sending the reply
        fulfils the email's reply obligation — marks the email itself
        ``is_completed`` unless it still has non-reply actions the user must
        handle. Idempotent. Returns the refreshed record (``None`` if unknown /
        not owned by this user)."""
        record = self.emails.get_by_email_id(
            email_id, user_pk=self.user_pk, with_children=True
        )
        if record is None or record.is_spam:
            return None
        now = datetime.now(timezone.utc)
        for action in record.actions:
            if (action.action_type or "").upper() == "REPLY" and action.status == "PENDING":
                self.actions.set_status(action, "COMPLETED")
        self._recompute_completion(record, now)
        if not record.is_completed:
            other_pending = any(
                a.status == "PENDING" and (a.action_type or "").upper() != "REPLY"
                for a in record.actions
            )
            if not other_pending:
                record.is_completed = True
                record.completed_at = now
                record.completion_source = "user"
        self.session.commit()
        self.session.refresh(record)
        return record

    def mark_complete(self, email_id: str) -> EmailRecord | None:
        """Explicitly resolve the whole email (the "mark done" / tick action).

        Completes **every** pending action (not just ``act_001``), sets
        ``is_completed`` + ``completion_source = "user"`` so a later Gmail sync /
        reprocess can never revert it, and stamps ``completed_at``. Idempotent —
        a second call is a no-op. Returns ``None`` if the email is unknown / not
        owned by this user."""
        record = self.emails.get_by_email_id(
            email_id, user_pk=self.user_pk, with_children=True
        )
        if record is None or record.is_spam:
            return None
        now = datetime.now(timezone.utc)
        for action in record.actions:
            if action.status == "PENDING":
                self.actions.set_status(action, "COMPLETED")
        newly = not record.is_completed
        record.is_completed = True
        if record.completed_at is None:
            record.completed_at = now
        record.completion_source = "user"
        self.session.commit()
        self.session.refresh(record)
        if newly:
            audit_record("EMAIL_RESOLVED", "email", resource_id=record.email_id,
                         user_pk=record.user_pk, detail={"via": "user"})
        return record

    def reopen(self, email_id: str) -> EmailRecord | None:
        """Undo a completion (explicit user action / "undo"). Reverts
        ``is_completed`` and hands the email back to auto-derivation."""
        record = self.emails.get_by_email_id(
            email_id, user_pk=self.user_pk, with_children=True
        )
        if record is None or record.is_spam:
            return None
        record.is_completed = False
        record.completed_at = None
        record.completion_source = "auto"
        self.session.commit()
        self.session.refresh(record)
        return record

    def clear_acknowledged(self) -> int:
        """Bulk-acknowledge every *active, non-actionable* email for this user.

        Marks ``is_viewed`` on `IMPORTANT` / `LOW_PRIORITY` emails that are not
        completed and not snoozed — the "Clear Resolved / tidy my dashboard"
        action. **Never** touches `REPLY_REQUIRED` / `ACTION_REQUIRED` (an
        unresolved task is never silently completed) and never deletes anything.
        Idempotent — returns the number of rows actually changed."""
        now = datetime.now(timezone.utc)
        rows = self.emails.list(
            user_pk=self.user_pk,
            primary_category=None,
            active=True,
            limit=500,
            now=now,
        )
        changed = 0
        for record in rows:
            if record.primary_category not in ("IMPORTANT", "LOW_PRIORITY"):
                continue
            if not record.is_viewed:
                record.is_viewed = True
                record.viewed_at = now
                changed += 1
        if changed:
            self.session.commit()
        return changed

    def set_action_status(
        self, email_id: str, action_ref: str, status: str
    ) -> tuple[EmailRecord, ActionRecord] | None:
        record = self.emails.get_by_email_id(
            email_id, user_pk=self.user_pk, with_children=True
        )
        if record is None or record.is_spam:
            return None
        action = next((a for a in record.actions if a.action_ref == action_ref), None)
        if action is None:
            return None
        self.actions.set_status(action, status)
        self._recompute_completion(record, datetime.now(timezone.utc))
        self.session.commit()
        self.session.refresh(record)
        return record, action


def _preview(text: str | None, *, limit: int = 240) -> str | None:
    """A short single-line preview for the frontend inbox. Never the full body."""
    if not text:
        return None
    collapsed = " ".join(text.split())
    return collapsed[:limit] or None


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
