"""Phase 10 — user-scheduled reminders (STEP 20 items 21-29, 44)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.repositories import NotificationRepository, ReminderRepository
from app.services.deadline_monitor_service import DeadlineMonitorService
from app.services.persistence_service import PersistenceService
from app.services.reminder_service import ReminderService, ReminderValidationError
from tests.monitor_helpers import NOW, make_monitored, persist_internship


@pytest.fixture
def reminders(db):
    return ReminderService(db)


@pytest.fixture
def monitor(db):
    return DeadlineMonitorService(db)


def _user_reminders(db, email_pk):
    return [
        n for n in NotificationRepository(db).list_by_email(email_pk)
        if n.notification_type == "user_reminder"
    ]


# --- create ---------------------------------------------------------

def test_create_user_scheduled_reminder(db, reminders):
    rec = persist_internship(db)
    r = reminders.create(rec.email_id, NOW + timedelta(days=1), now=NOW)
    assert r.id is not None
    assert r.status == "PENDING"
    assert r.reminder_type == "USER_SCHEDULED"


def test_multiple_reminders_for_one_email(db, reminders):
    rec = persist_internship(db)
    reminders.create(rec.email_id, NOW + timedelta(hours=3), now=NOW)
    reminders.create(rec.email_id, NOW + timedelta(hours=9), now=NOW)
    assert len(ReminderRepository(db).list_by_email(rec.id)) == 2


def test_reminder_tied_to_action(db, reminders):
    rec = persist_internship(db)
    ref = rec.actions[0].action_ref
    r = reminders.create(rec.email_id, NOW + timedelta(days=1), action_ref=ref, now=NOW)
    assert r.action_ref == ref


def test_reminder_unknown_email_returns_none(db, reminders):
    assert reminders.create("gmail_nope", NOW + timedelta(days=1), now=NOW) is None


def test_reminder_in_the_past_is_rejected(db, reminders):
    rec = persist_internship(db)
    with pytest.raises(ReminderValidationError):
        reminders.create(rec.email_id, NOW - timedelta(hours=1), now=NOW)


def test_reminder_unknown_action_is_rejected(db, reminders):
    rec = persist_internship(db)
    with pytest.raises(ReminderValidationError):
        reminders.create(rec.email_id, NOW + timedelta(days=1), action_ref="act_999", now=NOW)


# --- trigger --------------------------------------------------------

def test_reminder_triggers_when_due(db, reminders, monitor):
    rec = persist_internship(db)
    reminders.create(rec.email_id, NOW + timedelta(hours=2), now=NOW)
    result = monitor.run_deadline_check(NOW + timedelta(hours=3))
    assert result.reminders_evaluated == 1
    assert _user_reminders(db, rec.id)
    assert ReminderRepository(db).list_by_email(rec.id)[0].status == "TRIGGERED"


def test_reminder_does_not_trigger_before_due(db, reminders, monitor):
    rec = persist_internship(db)
    reminders.create(rec.email_id, NOW + timedelta(hours=5), now=NOW)
    result = monitor.run_deadline_check(NOW + timedelta(hours=1))
    assert result.reminders_evaluated == 0
    assert _user_reminders(db, rec.id) == []


def test_reminder_does_not_trigger_twice(db, reminders, monitor):
    rec = persist_internship(db)
    reminders.create(rec.email_id, NOW + timedelta(hours=2), now=NOW)
    monitor.run_deadline_check(NOW + timedelta(hours=3))
    monitor.run_deadline_check(NOW + timedelta(hours=4))
    assert len(_user_reminders(db, rec.id)) == 1


def test_completed_action_skips_the_reminder(db, reminders, monitor):
    rec = persist_internship(db)
    ref = rec.actions[0].action_ref
    reminders.create(rec.email_id, NOW + timedelta(hours=2), action_ref=ref, now=NOW)
    PersistenceService(db).set_action_status(rec.email_id, ref, "COMPLETED")
    monitor.run_deadline_check(NOW + timedelta(hours=3))
    assert _user_reminders(db, rec.id) == []
    assert ReminderRepository(db).list_by_email(rec.id)[0].status == "SKIPPED"


def test_cancelled_reminder_does_not_trigger(db, reminders, monitor):
    rec = persist_internship(db)
    r = reminders.create(rec.email_id, NOW + timedelta(hours=2), now=NOW)
    reminders.cancel(rec.email_id, r.id)
    monitor.run_deadline_check(NOW + timedelta(hours=3))
    assert _user_reminders(db, rec.id) == []
    assert ReminderRepository(db).get(r.id).status == "CANCELLED"


# --- independence from deadline escalation (STEP 11) ---------------

def test_custom_reminder_independent_from_deadline_escalation(db, reminders, monitor):
    rec = make_monitored(db, remaining=timedelta(hours=6))
    reminders.create(rec.email_id, NOW + timedelta(hours=1), now=NOW)
    result = monitor.run_deadline_check(NOW + timedelta(hours=2))
    kinds = {d.decision for d in result.results if d.email_id == rec.email_id}
    # both a deadline escalation AND the user reminder fired in the same pass
    assert "REMINDER_TRIGGERED" in kinds
    assert kinds & {"REMINDER", "URGENT"}


def test_custom_reminder_near_deadline_does_not_block_alarm(db, reminders, monitor):
    rec = make_monitored(db, remaining=timedelta(minutes=30), priority="CRITICAL")
    dl_dt = rec.deadlines[0].deadline_datetime
    reminders.create(rec.email_id, dl_dt - timedelta(minutes=10), now=NOW)
    result = monitor.run_deadline_check(dl_dt - timedelta(minutes=4))
    decisions = {d.decision for d in result.results if d.email_id == rec.email_id}
    assert "ALARM" in decisions
    assert "REMINDER_TRIGGERED" in decisions
