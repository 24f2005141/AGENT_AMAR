"""Phase 10 — DeadlineMonitorService (STEP 20 items 4-20, 30-42)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.repositories import DeadlineRepository, NotificationRepository
from app.services.deadline_monitor_service import DeadlineMonitorService
from tests.monitor_helpers import NOW, NOW_QUIET, deadline_of, make_monitored


@pytest.fixture
def monitor(db):
    return DeadlineMonitorService(db)


def _escalations(db, email_pk):
    return [
        n for n in NotificationRepository(db).list_by_email(email_pk)
        if n.notification_type == "deadline_escalation"
    ]


def _decision(result, email_id):
    return next(d for d in result.results if d.email_id == email_id)


# --- detection / lifecycle --------------------------------------------

def test_upcoming_deadline_is_detected(db, monitor):
    rec = make_monitored(db, remaining=timedelta(hours=10))
    result = monitor.run_deadline_check(NOW)
    assert result.deadlines_evaluated == 1
    assert _decision(result, rec.email_id).decision == "REMINDER"


def test_inactive_monitoring_is_ignored(db, monitor):
    make_monitored(db, remaining=timedelta(hours=2), monitoring=False)
    result = monitor.run_deadline_check(NOW)
    assert result.deadlines_evaluated == 0
    assert result.notifications_created == 0


def test_auto_start_monitoring_for_routing_monitor(db, monitor):
    rec = make_monitored(db, remaining=timedelta(hours=10), monitoring=None)
    assert deadline_of(rec).is_monitoring is False
    result = monitor.run_deadline_check(NOW)
    assert result.deadlines_evaluated == 1
    db.refresh(deadline_of(rec))
    assert deadline_of(rec).is_monitoring is True


def test_completed_action_stops_reminders(db, monitor):
    rec = make_monitored(db, remaining=timedelta(minutes=10), completed_actions=True)
    result = monitor.run_deadline_check(NOW)
    assert _decision(result, rec.email_id).decision == "COMPLETED"
    assert _escalations(db, rec.id) == []
    db.refresh(deadline_of(rec))
    assert deadline_of(rec).is_monitoring is False


def test_viewed_vs_unviewed_handled_differently(db, monitor):
    seen = make_monitored(db, email_id="gmail_seen", remaining=timedelta(hours=10), viewed=True)
    unseen = make_monitored(db, email_id="gmail_unseen", remaining=timedelta(hours=10))
    result = monitor.run_deadline_check(NOW)
    assert _decision(result, seen.email_id).decision == "NO_CHANGE"   # REMINDER demoted → skipped
    assert _decision(result, unseen.email_id).decision == "REMINDER"


def test_past_deadline_stops_escalation(db, monitor):
    rec = make_monitored(db, remaining=timedelta(hours=-2))
    result = monitor.run_deadline_check(NOW)
    d = _decision(result, rec.email_id)
    assert d.decision == "DEADLINE_PASSED"
    notes = NotificationRepository(db).list_by_email(rec.id)
    assert any(n.notification_type == "deadline_passed" for n in notes)
    assert _escalations(db, rec.id) == []
    # a second run does not add another "deadline passed" notice
    monitor.run_deadline_check(NOW)
    passed = [n for n in NotificationRepository(db).list_by_email(rec.id)
              if n.notification_type == "deadline_passed"]
    assert len(passed) == 1


def test_past_deadline_stops_monitoring_after_grace(db, monitor):
    rec = make_monitored(db, remaining=timedelta(hours=-30))  # > 24h grace
    monitor.run_deadline_check(NOW)
    db.refresh(deadline_of(rec))
    assert deadline_of(rec).is_monitoring is False


# --- escalation levels ------------------------------------------------

def test_reminder_level(db, monitor):
    rec = make_monitored(db, remaining=timedelta(hours=10))  # URGENT ladder, 12h rung
    result = monitor.run_deadline_check(NOW)
    assert _decision(result, rec.email_id).decision == "REMINDER"


def test_urgent_level(db, monitor):
    rec = make_monitored(db, remaining=timedelta(hours=2))
    result = monitor.run_deadline_check(NOW)
    assert _decision(result, rec.email_id).decision == "URGENT"


def test_alarm_eligibility(db, monitor):
    rec = make_monitored(db, remaining=timedelta(minutes=10))  # URGENT priority, unviewed
    result = monitor.run_deadline_check(NOW)
    d = _decision(result, rec.email_id)
    assert d.decision == "ALARM"
    assert d.requires_alarm is True
    assert _escalations(db, rec.id)[-1].requires_alarm is True


def test_low_priority_deadline_never_alarms(db, monitor):
    rec = make_monitored(db, remaining=timedelta(minutes=3), priority="LOW")
    result = monitor.run_deadline_check(NOW)
    assert _decision(result, rec.email_id).decision == "NO_CHANGE"
    assert _escalations(db, rec.id) == []


def test_high_critical_unviewed_triggers_alarm(db, monitor):
    rec = make_monitored(db, remaining=timedelta(minutes=3), priority="CRITICAL")
    result = monitor.run_deadline_check(NOW)
    assert _decision(result, rec.email_id).decision == "ALARM"


def test_viewed_pending_action_gets_reminder_not_alarm(db, monitor):
    rec = make_monitored(db, remaining=timedelta(minutes=10), viewed=True)
    result = monitor.run_deadline_check(NOW)
    d = _decision(result, rec.email_id)
    assert d.decision == "URGENT"      # ALARM demoted by "viewed"
    assert d.requires_alarm is False


def test_alarm_created_only_once_and_idempotent(db, monitor):
    rec = make_monitored(db, remaining=timedelta(minutes=10))
    monitor.run_deadline_check(NOW)
    r2 = monitor.run_deadline_check(NOW)
    assert _decision(r2, rec.email_id).decision == "NO_CHANGE"
    alarms = [n for n in _escalations(db, rec.id) if n.reminder_level == "ALARM"]
    assert len(alarms) == 1


def test_repeated_runs_are_idempotent(db, monitor):
    rec = make_monitored(db, remaining=timedelta(hours=2))
    for _ in range(4):
        monitor.run_deadline_check(NOW)
    assert len(_escalations(db, rec.id)) == 1


def test_highest_escalation_can_be_determined(db, monitor):
    rec = make_monitored(db, remaining=timedelta(minutes=10))
    monitor.run_deadline_check(NOW)
    assert NotificationRepository(db).highest_escalation_for(deadline_of(rec).id) == "ALARM"


def test_escalation_progresses_across_ticks(db, monitor):
    # deadline 4h out (16:00 UTC); intermediate ticks stay outside quiet hours
    rec = make_monitored(db, remaining=timedelta(hours=4))
    assert _decision(monitor.run_deadline_check(NOW), rec.email_id).decision == "REMINDER"
    dl_dt = deadline_of(rec).deadline_datetime
    assert _decision(
        monitor.run_deadline_check(dl_dt - timedelta(hours=2)), rec.email_id
    ).decision == "URGENT"
    assert _decision(
        monitor.run_deadline_check(dl_dt - timedelta(minutes=10)), rec.email_id
    ).decision == "ALARM"
    levels = sorted({n.reminder_level for n in _escalations(db, rec.id)})
    assert levels == ["ALARM", "REMINDER", "URGENT"]


# --- snooze ---------------------------------------------------------

def test_snoozed_email_suppresses_automatic_reminders(db, monitor):
    rec = make_monitored(
        db, remaining=timedelta(hours=2), snoozed_until=NOW + timedelta(hours=6)
    )
    result = monitor.run_deadline_check(NOW)
    assert _decision(result, rec.email_id).decision == "SNOOZED"
    assert _escalations(db, rec.id) == []
    db.refresh(deadline_of(rec))
    assert deadline_of(rec).is_monitoring is True  # still monitored


def test_monitoring_resumes_after_snooze_expires(db, monitor):
    rec = make_monitored(
        db, remaining=timedelta(hours=2), snoozed_until=NOW - timedelta(minutes=1)
    )
    result = monitor.run_deadline_check(NOW)
    assert _decision(result, rec.email_id).decision == "URGENT"


def test_critical_after_snooze_evaluates_current_urgency_once(db, monitor):
    rec = make_monitored(
        db, remaining=timedelta(minutes=3), priority="CRITICAL",
        snoozed_until=NOW - timedelta(minutes=1),
    )
    monitor.run_deadline_check(NOW)
    # only the currently-appropriate rung is issued, not every missed one
    assert [n.reminder_level for n in _escalations(db, rec.id)] == ["ALARM"]


# --- quiet hours --------------------------------------------------

def test_reminder_respects_quiet_hours(db, monitor):
    rec = make_monitored(db, remaining=timedelta(hours=2), now=NOW_QUIET)
    result = monitor.run_deadline_check(NOW_QUIET)
    assert _decision(result, rec.email_id).decision == "QUIET_HOURS_DEFERRED"
    skipped = [n for n in _escalations(db, rec.id) if n.status == "SKIPPED"]
    assert len(skipped) == 1


def test_alarm_breaks_quiet_hours_only_for_critical(db, monitor):
    urgent = make_monitored(db, email_id="gmail_u", remaining=timedelta(minutes=10), now=NOW_QUIET)
    crit = make_monitored(
        db, email_id="gmail_c", remaining=timedelta(minutes=3),
        priority="CRITICAL", now=NOW_QUIET,
    )
    result = monitor.run_deadline_check(NOW_QUIET)
    assert _decision(result, urgent.email_id).decision == "QUIET_HOURS_DEFERRED"
    assert _decision(result, crit.email_id).decision == "ALARM"


def test_quiet_hours_skip_is_not_duplicated(db, monitor):
    rec = make_monitored(db, remaining=timedelta(hours=2), now=NOW_QUIET)
    monitor.run_deadline_check(NOW_QUIET)
    monitor.run_deadline_check(NOW_QUIET)
    skipped = [n for n in _escalations(db, rec.id) if n.status == "SKIPPED"]
    assert len(skipped) == 1


# --- ambiguous deadline -----------------------------------------

def test_ambiguous_deadline_gets_one_heads_up(db, monitor):
    rec = make_monitored(db, ambiguous=True)
    monitor.run_deadline_check(NOW)
    monitor.run_deadline_check(NOW)
    notes = [n for n in NotificationRepository(db).list_by_email(rec.id)
             if n.notification_type == "ambiguous_deadline"]
    assert len(notes) == 1


# --- integration-ish -------------------------------------------

def test_full_critical_deadline_flow(db, monitor):
    """4:30pm email, 5:00pm deadline, unviewed → NORMAL then URGENT then ALARM."""
    rec = make_monitored(db, remaining=timedelta(minutes=30), priority="CRITICAL")
    # NORMAL alert already created at persistence time
    assert any(n.notification_type == "new_priority_email" for n in rec.notifications)

    dl_dt = deadline_of(rec).deadline_datetime
    assert _decision(monitor.run_deadline_check(dl_dt - timedelta(minutes=25)),
                     rec.email_id).decision == "REMINDER"
    assert _decision(monitor.run_deadline_check(dl_dt - timedelta(minutes=12)),
                     rec.email_id).decision == "URGENT"
    d = _decision(monitor.run_deadline_check(dl_dt - timedelta(minutes=4)), rec.email_id)
    assert d.decision == "ALARM" and d.requires_alarm is True

    monitor.run_deadline_check(dl_dt - timedelta(minutes=4))  # idempotent
    alarms = [n for n in _escalations(db, rec.id) if n.reminder_level == "ALARM"]
    assert len(alarms) == 1


def test_user_completes_action_mid_escalation(db, monitor):
    from app.services.persistence_service import PersistenceService

    rec = make_monitored(db, remaining=timedelta(hours=2))
    assert _decision(monitor.run_deadline_check(NOW), rec.email_id).decision == "URGENT"
    for a in rec.actions:
        PersistenceService(db).set_action_status(rec.email_id, a.action_ref, "COMPLETED")
    result = monitor.run_deadline_check(NOW)
    assert _decision(result, rec.email_id).decision == "COMPLETED"
    db.refresh(deadline_of(rec))
    assert deadline_of(rec).is_monitoring is False
