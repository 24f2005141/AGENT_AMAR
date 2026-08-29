"""Phase 11B.1 — background scheduler.

No real waiting: cycle functions are invoked directly, and the two lifecycle
tests use a short interval inside ``asyncio.run`` with millisecond sleeps.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import Settings
from app.db import session as db_session
from app.db.models import ReminderRecord
from app.repositories import NotificationRepository, ReminderRepository
from app.services.scheduler import MonitorScheduler, get_scheduler
from tests.monitor_helpers import make_monitored, persist_internship


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _sched(**over) -> MonitorScheduler:
    cfg = dict(scheduler_enabled=True,
               deadline_check_interval_seconds=1,
               reminder_check_interval_seconds=1,
               gmail_sync_enabled=False)  # deadline/reminder loops only (Phase 12 has its own tests)
    cfg.update(over)
    return MonitorScheduler(Settings(**cfg))


def _escalations(db, email_pk):
    db.expire_all()  # scheduler cycles commit on their own session
    return [n for n in NotificationRepository(db).list_by_email(email_pk)
            if n.notification_type == "deadline_escalation"]


def _reload(db):
    """A scheduler cycle commits on its own DB session — drop this session's
    identity-map cache so the next read reflects it."""
    db.expire_all()


def _seed_due_reminder(db, email_pk, *, when_offset=timedelta(minutes=-1), action_ref=None):
    r = ReminderRecord(
        email_pk=email_pk, action_ref=action_ref,
        reminder_at=_now() + when_offset, reminder_type="USER_SCHEDULED",
        status="PENDING", timezone="UTC",
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    return r


# --- SCHEDULER lifecycle ------------------------------------------------

def test_scheduler_disabled_is_noop():
    sch = MonitorScheduler(Settings(scheduler_enabled=False))
    sch.start()
    assert sch.running is False
    assert sch.status()["scheduler"] == "stopped"


def test_scheduler_starts_and_stops_cleanly():
    async def scenario():
        sch = _sched()
        sch.start()
        assert sch.running is True
        assert len(sch._tasks) == 2
        await asyncio.sleep(0.05)
        await sch.stop()
        assert sch.running is False
        assert sch._tasks == []
        # idempotent
        await sch.stop()
    asyncio.run(scenario())


def test_scheduler_start_is_idempotent():
    async def scenario():
        sch = _sched()
        sch.start()
        first = list(sch._tasks)
        sch.start()  # no duplicate tasks
        assert sch._tasks == first
        await sch.stop()
    asyncio.run(scenario())


def test_loop_runs_cycles_on_interval(db):
    make_monitored(db, now=_now(), remaining=timedelta(hours=2))

    async def scenario():
        sch = _sched(deadline_check_interval_seconds=1, reminder_check_interval_seconds=1)
        sch.start()
        await asyncio.sleep(1.7)          # warmup 0.5s + at least one tick each
        await sch.stop()
        return sch
    sch = asyncio.run(scenario())
    assert sch.cycles["deadline"] >= 1
    assert sch.cycles["reminder"] >= 1
    assert sch.last_deadline_check is not None


# --- ERROR HANDLING --------------------------------------------------

def test_job_failure_is_swallowed_and_counted():
    sch = _sched()

    def boom():
        raise RuntimeError("kaboom")

    sch._run_safely("deadline", boom)          # must not raise
    sch._run_safely("deadline", boom)
    assert sch.failures["deadline"] == 2
    assert "kaboom" in sch.last_error
    assert sch.running is False  # unaffected


def test_loop_survives_a_failing_cycle():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("first call fails")

    async def scenario():
        sch = _sched()
        sch._deadline_cycle = flaky  # type: ignore[method-assign]
        sch.start()
        await asyncio.sleep(2.3)     # warmup 0.5s + ≥2 ticks
        await sch.stop()
        return sch
    sch = asyncio.run(scenario())
    assert calls["n"] >= 2                 # kept going after the failure
    assert sch.failures["deadline"] >= 1


# --- DEADLINE job ---------------------------------------------------

def test_deadline_cycle_calls_existing_logic_and_creates_notification(db):
    rec = make_monitored(db, now=_now(), remaining=timedelta(minutes=3), priority="CRITICAL")

    _sched()._deadline_cycle()

    escal = _escalations(db, rec.id)
    assert len(escal) == 1
    assert escal[0].reminder_level == "ALARM"
    assert escal[0].requires_alarm is True


def test_deadline_cycle_does_not_duplicate_on_repeat(db):
    rec = make_monitored(db, now=_now(), remaining=timedelta(minutes=3), priority="CRITICAL")
    sch = _sched()
    sch._deadline_cycle()
    sch._deadline_cycle()
    sch._deadline_cycle()
    assert len(_escalations(db, rec.id)) == 1


def test_deadline_cycle_adds_notification_only_when_level_changes(db):
    # 4h out → URGENT ladder gives REMINDER now; not yet URGENT
    rec = make_monitored(db, now=_now(), remaining=timedelta(hours=4))
    sch = _sched()
    sch._deadline_cycle()
    levels_after_1 = sorted(n.reminder_level for n in _escalations(db, rec.id))

    # move the deadline closer → URGENT rung
    dl = rec.deadlines[0]
    dl.deadline_datetime = _now() + timedelta(hours=2)
    db.commit()
    sch._deadline_cycle()
    levels_after_2 = sorted(n.reminder_level for n in _escalations(db, rec.id))

    assert levels_after_1 == ["REMINDER"]
    assert levels_after_2 == ["REMINDER", "URGENT"]


def test_deadline_cycle_handles_passed_deadline(db):
    rec = make_monitored(db, now=_now(), remaining=timedelta(minutes=-5))
    _sched()._deadline_cycle()
    notes = NotificationRepository(db).list_by_email(rec.id)
    assert any(n.notification_type == "deadline_passed" for n in notes)


def test_deadline_cycle_ignores_reminders(db):
    """The scheduler's deadline job runs with include_reminders=False."""
    rec = persist_internship(db, now=_now())
    _seed_due_reminder(db, rec.id)
    _sched()._deadline_cycle()
    _reload(db)
    notes = NotificationRepository(db).list_by_email(rec.id)
    assert not any(n.notification_type == "user_reminder" for n in notes)


# --- REMINDER job -------------------------------------------------

def test_reminder_cycle_triggers_due_reminder(db):
    rec = persist_internship(db, now=_now())
    r = _seed_due_reminder(db, rec.id)

    _sched()._reminder_cycle()
    _reload(db)

    r = ReminderRepository(db).get(r.id)
    assert r.status == "TRIGGERED"
    assert r.triggered_at is not None
    notes = NotificationRepository(db).list_by_email(rec.id)
    assert any(n.notification_type == "user_reminder" for n in notes)


def test_reminder_cycle_ignores_future_reminder(db):
    rec = persist_internship(db, now=_now())
    r = _seed_due_reminder(db, rec.id, when_offset=timedelta(hours=6))
    _sched()._reminder_cycle()
    _reload(db)
    assert ReminderRepository(db).get(r.id).status == "PENDING"


def test_reminder_cycle_ignores_cancelled_and_triggered(db):
    rec = persist_internship(db, now=_now())
    cancelled = _seed_due_reminder(db, rec.id)
    cancelled.status = "CANCELLED"
    already = _seed_due_reminder(db, rec.id)
    already.status = "TRIGGERED"
    db.commit()

    _sched()._reminder_cycle()
    _reload(db)

    assert ReminderRepository(db).get(cancelled.id).status == "CANCELLED"
    assert ReminderRepository(db).get(already.id).status == "TRIGGERED"
    assert [n for n in NotificationRepository(db).list_by_email(rec.id)
            if n.notification_type == "user_reminder"] == []


def test_reminder_cycle_does_not_duplicate_on_repeat(db):
    rec = persist_internship(db, now=_now())
    _seed_due_reminder(db, rec.id)
    sch = _sched()
    sch._reminder_cycle()
    sch._reminder_cycle()
    sch._reminder_cycle()
    _reload(db)
    got = [n for n in NotificationRepository(db).list_by_email(rec.id)
           if n.notification_type == "user_reminder"]
    assert len(got) == 1


# --- PERSISTENCE across "restart" -------------------------------

def test_restart_does_not_reset_reminder_state_or_duplicate(db):
    rec = persist_internship(db, now=_now())
    r = _seed_due_reminder(db, rec.id)

    _sched()._reminder_cycle()            # "process A"
    _reload(db)
    assert ReminderRepository(db).get(r.id).status == "TRIGGERED"

    # brand-new scheduler instance == a process restart
    MonitorScheduler(Settings(scheduler_enabled=True))._reminder_cycle()
    _reload(db)

    assert ReminderRepository(db).get(r.id).status == "TRIGGERED"
    got = [n for n in NotificationRepository(db).list_by_email(rec.id)
           if n.notification_type == "user_reminder"]
    assert len(got) == 1


def test_restart_does_not_duplicate_deadline_notifications(db):
    rec = make_monitored(db, now=_now(), remaining=timedelta(minutes=3), priority="CRITICAL")
    _sched()._deadline_cycle()
    MonitorScheduler(Settings(scheduler_enabled=True))._deadline_cycle()
    assert len(_escalations(db, rec.id)) == 1


# --- status endpoint / singleton -------------------------------

def test_status_shape():
    st = _sched().status()
    for k in ("scheduler", "enabled", "deadline_check_interval_seconds",
              "reminder_check_interval_seconds", "last_deadline_check",
              "last_reminder_check", "deadline_cycles", "reminder_cycles",
              "deadline_failures", "reminder_failures", "last_error"):
        assert k in st
    assert st["scheduler"] == "stopped"


def test_get_scheduler_is_singleton():
    assert get_scheduler() is get_scheduler()


def test_monitor_status_endpoint():
    from fastapi.testclient import TestClient
    from app.main import app

    r = TestClient(app).get("/api/v1/monitor/status")
    assert r.status_code == 200
    body = r.json()
    assert body["scheduler"] in {"running", "stopped"}
    assert body["enabled"] is False           # disabled in the test env
    assert body["deadline_check_interval_seconds"] == 60
