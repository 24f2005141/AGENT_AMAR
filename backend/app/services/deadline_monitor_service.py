"""DeadlineMonitorService — AGENT AMAR's autonomous attention system (Phase 10).

    SQLite  →  DeadlineMonitorService.run_deadline_check(now)
                    ├─ auto-start monitoring for eligible deadlines
                    ├─ evaluate every monitored deadline  → escalation events
                    └─ fire every due user-scheduled reminder

It **consumes** the persistent state the Phase 9 pipeline produced. It holds
no classification / priority intelligence — priority level, category, viewed /
completed / snoozed state and the extracted deadline are all read straight from
the DB. The only judgement here is deterministic time-math + the centralised
:mod:`app.services.escalation_policy`.

Nothing is delivered. Every decision becomes a ``notifications`` row for the
future Flutter layer to consume.

The current time is injectable (``now=``) so tests never touch the wall clock.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.models import DeadlineRecord, EmailRecord
from app.repositories import (
    DeadlineRepository,
    EmailRepository,
    NotificationRepository,
    ReminderRepository,
)
from app.services.escalation_policy import (
    ALARM_ELIGIBLE_PRIORITIES,
    EscalationLevel,
    QuietHours,
    demote,
    ladder_level,
    quiet_hours_suppress,
    rank,
)

_DONE_STATUSES = {"COMPLETED", "DISMISSED"}


@dataclass
class MonitorDecision:
    email_id: str
    decision: str          # escalation level or COMPLETED / SNOOZED / DEADLINE_PASSED / ...
    reason: str
    deadline_ref: str | None = None
    notification_id: int | None = None
    requires_alarm: bool = False


@dataclass
class MonitorRunResult:
    checked_at: datetime
    deadlines_evaluated: int = 0
    reminders_evaluated: int = 0
    notifications_created: int = 0
    results: list[MonitorDecision] = field(default_factory=list)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _human(delta: timedelta) -> str:
    secs = int(delta.total_seconds())
    if secs < 0:
        return "overdue"
    if secs < 3600:
        return f"{secs // 60}m"
    if secs < 86400:
        return f"{secs // 3600}h"
    return f"{secs // 86400}d"


class DeadlineMonitorService:
    def __init__(self, session: Session, *, settings: Settings | None = None) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.emails = EmailRepository(session)
        self.deadlines = DeadlineRepository(session)
        self.notifications = NotificationRepository(session)
        self.reminders = ReminderRepository(session)

    # -- public entry point -------------------------------------------

    def run_deadline_check(
        self, now: datetime | None = None, *, include_reminders: bool = True
    ) -> MonitorRunResult:
        """Evaluate every monitored deadline (and, by default, every due
        reminder). Commits.

        ``include_reminders=False`` runs the deadline half only — used by the
        background scheduler, which fires reminders on its own interval via
        :meth:`run_reminder_check`. The manual ``POST /monitor/deadlines/check``
        endpoint keeps the default (both), so its behaviour is unchanged.
        """
        now = _aware(now) or datetime.now(timezone.utc)
        result = MonitorRunResult(checked_at=now)

        self._auto_start_monitoring()

        for dl, email in self.deadlines.list_monitored_with_email():
            result.deadlines_evaluated += 1
            decision = self._evaluate_deadline(dl, email, now)
            result.results.append(decision)
            if decision.notification_id is not None:
                result.notifications_created += 1

        if include_reminders:
            self._process_due_reminders(now, result)

        self.session.commit()
        return result

    def run_reminder_check(self, now: datetime | None = None) -> MonitorRunResult:
        """Fire every user-scheduled reminder whose time has come. Commits.

        Same logic path as the reminder half of :meth:`run_deadline_check` —
        no duplicated business rules.
        """
        now = _aware(now) or datetime.now(timezone.utc)
        result = MonitorRunResult(checked_at=now)
        self._process_due_reminders(now, result)
        self.session.commit()
        return result

    def _process_due_reminders(self, now: datetime, result: MonitorRunResult) -> None:
        due = self.reminders.list_due(now)
        result.reminders_evaluated = len(due)
        for reminder in due:
            decision = self._fire_reminder(reminder, now)
            result.results.append(decision)
            if decision.notification_id is not None:
                result.notifications_created += 1

    # -- monitoring lifecycle ---------------------------------------

    def _auto_start_monitoring(self) -> None:
        """Begin monitoring for deadlines the orchestrator flagged
        ``routing.monitor`` (Deadline Monitoring.md "When monitoring starts")."""
        for dl, _email in self.deadlines.list_auto_monitor_candidates():
            self.deadlines.start_monitoring(dl)

    # -- per-deadline evaluation -----------------------------------

    def _evaluate_deadline(
        self, dl: DeadlineRecord, email: EmailRecord, now: datetime
    ) -> MonitorDecision:
        ref = dl.deadline_ref

        # (C) action completed / email done → stop, no more reminders
        if email.is_completed or self._related_action_done(dl, email):
            self.deadlines.stop_monitoring(dl)
            return MonitorDecision(email.email_id, "COMPLETED",
                                   "action complete — monitoring stopped", ref)

        # ambiguous deadline with no concrete datetime → one heads-up, keep watching
        if dl.deadline_datetime is None:
            if dl.is_ambiguous and not self.notifications.exists_for_deadline(
                dl.id, "ambiguous_deadline", statuses=("PENDING", "SENT", "SKIPPED")
            ):
                n = self.notifications.create(
                    email_pk=email.id, deadline_pk=dl.id,
                    notification_type="ambiguous_deadline",
                    reminder_level="REMINDER", severity="REMINDER",
                    detail="deadline looks time-sensitive but is unclear — please check",
                )
                return MonitorDecision(email.email_id, "AMBIGUOUS",
                                       "unclear deadline — one-time heads-up", ref, n.id)
            return MonitorDecision(email.email_id, "NO_CHANGE",
                                   "no concrete deadline to time", ref)

        deadline_dt = _aware(dl.deadline_datetime)
        remaining = deadline_dt - now

        # (STEP 15) deadline passed → one final notice, then stop after grace
        if remaining.total_seconds() <= 0:
            return self._handle_passed(dl, email, now, deadline_dt, ref)

        # (D) snoozed → suppress automatic escalation, keep monitoring
        snoozed_until = _aware(email.snoozed_until)
        if snoozed_until and now < snoozed_until:
            return MonitorDecision(email.email_id, "SNOOZED",
                                   f"snoozed until {snoozed_until.isoformat()}", ref)

        target = ladder_level(email.priority_level, remaining)

        # (STEP 6B / Reminder Escalation) user has seen it → drop one rung
        if email.is_viewed and rank(target) > rank(EscalationLevel.NORMAL):
            target = demote(target)

        if rank(target) <= rank(EscalationLevel.NORMAL):
            return MonitorDecision(email.email_id, "NO_CHANGE",
                                   "no new escalation threshold crossed", ref)

        # (STEP 7) alarm eligibility
        if target == EscalationLevel.ALARM and not self._alarm_allowed(dl, email):
            target = EscalationLevel.URGENT

        # (STEP 13/14) dedup + monotonic — never re-issue or step backwards
        already = self.notifications.highest_escalation_for(dl.id)
        if rank(target) <= rank(already):
            return MonitorDecision(email.email_id, "NO_CHANGE",
                                   f"{target.value} already issued (highest: {already})", ref)

        # (STEP 16) quiet hours
        in_quiet = self._quiet_hours().contains(now)
        if quiet_hours_suppress(
            target, email.priority_level, in_quiet,
            alarm_breaks_quiet_hours_for_critical=self.settings.alarm_breaks_quiet_hours_for_critical,
        ):
            if not self.notifications.exists_for_deadline(
                dl.id, "deadline_escalation", reminder_level=target.value,
                statuses=("PENDING", "SENT", "SKIPPED"),
            ):
                self.notifications.create(
                    email_pk=email.id, deadline_pk=dl.id,
                    notification_type="deadline_escalation",
                    reminder_level=target.value, severity=target.value,
                    status="SKIPPED", detail="held back by quiet hours",
                )
            return MonitorDecision(email.email_id, "QUIET_HOURS_DEFERRED",
                                   f"{target.value} suppressed by quiet hours", ref)

        requires_alarm = target == EscalationLevel.ALARM
        n = self.notifications.create(
            email_pk=email.id, deadline_pk=dl.id,
            notification_type="deadline_escalation",
            reminder_level=target.value, severity=target.value,
            requires_alarm=requires_alarm,
            detail=(
                f"{email.priority_level} deadline in {_human(remaining)}; "
                f"{'unviewed' if not email.is_viewed else 'viewed, action pending'}"
            ),
        )
        return MonitorDecision(
            email.email_id, target.value,
            f"deadline in {_human(remaining)}, priority {email.priority_level}",
            ref, n.id, requires_alarm,
        )

    def _handle_passed(
        self, dl: DeadlineRecord, email: EmailRecord, now: datetime,
        deadline_dt: datetime, ref: str | None,
    ) -> MonitorDecision:
        if not dl.is_past:
            dl.is_past = True
            email.deadline_is_past = True
        notification_id = None
        if not self.notifications.exists_for_deadline(
            dl.id, "deadline_passed", statuses=("PENDING", "SENT", "SKIPPED")
        ):
            n = self.notifications.create(
                email_pk=email.id, deadline_pk=dl.id,
                notification_type="deadline_passed",
                reminder_level="URGENT", severity="URGENT",
                detail="the deadline for this email has passed",
            )
            notification_id = n.id
        grace_ends = deadline_dt + timedelta(hours=self.settings.deadline_passed_grace_hours)
        if now >= grace_ends:
            self.deadlines.stop_monitoring(dl)
        return MonitorDecision(email.email_id, "DEADLINE_PASSED",
                               "deadline passed — final notice", ref, notification_id)

    # -- reminders --------------------------------------------------

    def _fire_reminder(self, reminder, now: datetime) -> MonitorDecision:
        email = reminder.email
        if email.is_completed or self._reminder_action_done(reminder, email):
            reminder.status = "SKIPPED"
            self.session.flush()
            return MonitorDecision(email.email_id, "REMINDER_SKIPPED",
                                   "email/action already handled before the reminder time")
        n = self.notifications.create(
            email_pk=email.id, reminder_pk=reminder.id,
            notification_type="user_reminder",
            reminder_level="NORMAL", severity="NORMAL",
            detail=reminder.note or "you asked to be reminded about this email",
        )
        reminder.status = "TRIGGERED"
        reminder.triggered_at = now
        self.session.flush()
        return MonitorDecision(email.email_id, "REMINDER_TRIGGERED",
                               "user-scheduled reminder fired", None, n.id)

    # -- helpers ---------------------------------------------------

    @staticmethod
    def _related_action_done(dl: DeadlineRecord, email: EmailRecord) -> bool:
        if not dl.related_action_ref:
            return False
        action = next(
            (a for a in email.actions if a.action_ref == dl.related_action_ref), None
        )
        return action is not None and action.status in _DONE_STATUSES

    @staticmethod
    def _reminder_action_done(reminder, email: EmailRecord) -> bool:
        if not reminder.action_ref:
            return False
        action = next(
            (a for a in email.actions if a.action_ref == reminder.action_ref), None
        )
        return action is not None and action.status in _DONE_STATUSES

    def _alarm_allowed(self, dl: DeadlineRecord, email: EmailRecord) -> bool:
        """STEP 7 — an alarm needs a *combination*, not just a close deadline."""
        if (email.priority_level or "").upper() not in ALARM_ELIGIBLE_PRIORITIES:
            return False
        if email.is_completed or not dl.is_monitoring:
            return False
        action_pending = email.action_required and not email.is_completed
        # unviewed, or the user saw it but the required action is still open
        return (not email.is_viewed) or action_pending

    def _quiet_hours(self) -> QuietHours:
        return QuietHours(
            start_hour=self.settings.quiet_hours_start,
            end_hour=self.settings.quiet_hours_end,
            tz=self.settings.quiet_hours_tz_resolved,
        )
