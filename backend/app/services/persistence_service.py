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

# routing.notify already encodes User Preferences §3 (notify at HIGH+); we only
# add the "don't duplicate" guard here.
_NOTIFY_LEVELS = {"HIGH", "URGENT", "CRITICAL"}


class PersistenceService:
    def __init__(self, session: Session) -> None:
        self.session = session
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
            record = EmailRecord(email_id=fd.email_id)
            self.emails.add(record)

        self._apply_identity_and_metadata(record, normalized, fd)
        self._apply_system_analysis(record, fd, now)
        self._upsert_actions(record, fd)
        self._upsert_deadlines(record, fd)
        self._recompute_completion(record, now)
        self._append_processing_run(record, fd, decision_envelope, now)
        self._maybe_create_notification(record, fd)

        self.session.commit()
        self.session.refresh(record)
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
        """Derive ``is_completed`` from the actions' user-set statuses."""
        if not record.action_required:
            done = False
        elif not record.actions:
            done = False
        else:
            blocking = [a for a in record.actions if a.blocking]
            relevant = blocking or record.actions
            done = all(a.status in ("COMPLETED", "DISMISSED") for a in relevant)
        if done and not record.is_completed:
            record.is_completed = True
            record.completed_at = now
        elif not done and record.is_completed:
            record.is_completed = False
            record.completed_at = None

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
            summary=envelope.reasoning_summary or None,
            agent_trace=[t.model_dump() for t in fd.agent_trace],
            conflicts_resolved=[c.model_dump() for c in fd.conflicts_resolved],
            review_reasons=list(fd.review_reasons),
            errors=[e.model_dump() for e in envelope.errors],
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
        record = self.emails.get_by_email_id(email_id, with_children=True)
        if record is None:
            return None
        if not record.is_viewed:
            record.is_viewed = True
            record.viewed_at = datetime.now(timezone.utc)
        self.session.commit()
        self.session.refresh(record)
        return record

    def snooze(self, email_id: str, until: datetime) -> EmailRecord | None:
        record = self.emails.get_by_email_id(email_id, with_children=True)
        if record is None:
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
        record = self.emails.get_by_email_id(email_id, with_children=True)
        if record is None:
            return None
        record.snoozed_until = None
        self.session.commit()
        self.session.refresh(record)
        return record

    def set_action_status(
        self, email_id: str, action_ref: str, status: str
    ) -> tuple[EmailRecord, ActionRecord] | None:
        record = self.emails.get_by_email_id(email_id, with_children=True)
        if record is None:
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
