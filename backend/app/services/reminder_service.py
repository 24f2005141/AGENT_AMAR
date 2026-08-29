"""ReminderService — user-scheduled reminders (Phase 10, STEP 8/18).

A *scheduled reminder* ("remind me about this at 09:00 tomorrow") is NOT a
snooze:

* snooze  → ``EmailRecord.snoozed_until``: *suppress* automatic escalation until
  a time. One per email.
* reminder → a ``ReminderRecord``: *explicitly alert me* at a time. Many per
  email; optionally tied to one action.

Deadline escalation keeps running independently of both — a custom reminder
never disables critical-deadline protection (STEP 11).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import ReminderRecord
from app.repositories import EmailRepository, ReminderRepository


class ReminderValidationError(ValueError):
    """Raised for a nonsensical reminder request (→ HTTP 400)."""


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _tz_label(dt: datetime) -> str:
    tz = dt.tzinfo
    key = getattr(tz, "key", None)
    if key:
        return key
    offset = dt.strftime("%z")
    if not offset or offset == "+0000":
        return "UTC"
    return f"UTC{offset[:3]}:{offset[3:]}"


class ReminderService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.emails = EmailRepository(session)
        self.reminders = ReminderRepository(session)

    def create(
        self,
        email_id: str,
        reminder_at: datetime,
        *,
        action_ref: str | None = None,
        note: str | None = None,
        now: datetime | None = None,
    ) -> ReminderRecord | None:
        """Returns the new reminder, or ``None`` if the email does not exist.
        Raises :class:`ReminderValidationError` for a bad time / action_ref."""
        email = self.emails.get_by_email_id(email_id, with_children=True)
        if email is None:
            return None

        at = _aware(reminder_at)
        now = _aware(now) if now else datetime.now(timezone.utc)
        if at <= now:
            raise ReminderValidationError("reminder_at must be in the future")
        horizon = now + timedelta(days=get_settings().reminder_max_horizon_days)
        if at > horizon:
            raise ReminderValidationError(
                f"reminder_at is more than {get_settings().reminder_max_horizon_days} days away"
            )
        if action_ref and not any(a.action_ref == action_ref for a in email.actions):
            raise ReminderValidationError(f"action {action_ref!r} not found on this email")

        reminder = ReminderRecord(
            email_pk=email.id,
            action_ref=action_ref,
            reminder_at=at,
            reminder_type="USER_SCHEDULED",
            status="PENDING",
            timezone=_tz_label(at),
            note=note,
        )
        self.reminders.add(reminder)
        self.session.commit()
        self.session.refresh(reminder)
        return reminder

    def list_for_email(self, email_id: str) -> list[ReminderRecord] | None:
        email = self.emails.get_by_email_id(email_id)
        if email is None:
            return None
        return self.reminders.list_by_email(email.id)

    def cancel(self, email_id: str, reminder_id: int) -> ReminderRecord | None:
        email = self.emails.get_by_email_id(email_id)
        if email is None:
            return None
        reminder = self.reminders.get(reminder_id)
        if reminder is None or reminder.email_pk != email.id:
            return None
        if reminder.status == "PENDING":
            reminder.status = "CANCELLED"
            reminder.cancelled_at = datetime.now(timezone.utc)
            self.session.commit()
            self.session.refresh(reminder)
        return reminder
