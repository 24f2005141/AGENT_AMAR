"""Helpers for Phase 10 (deadline monitoring + reminder escalation) tests.

Everything is deterministic: a fixed ``NOW`` well outside quiet hours, and the
DB deadline datetime is forced to an exact offset from ``now`` so escalation
rungs are predictable regardless of what the Deadline Agent extracted.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.db.models import DeadlineRecord, EmailRecord
from app.services.persistence_service import PersistenceService
from tests.persistence_helpers import decision_for, internship_email

# 2026-06-01 12:00 UTC == 17:30 Asia/Kolkata — NOT within 23:00–07:00 quiet hours.
NOW = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
# 2026-06-01 20:00 UTC == 01:30 Asia/Kolkata — inside quiet hours.
NOW_QUIET = datetime(2026, 6, 1, 20, 0, tzinfo=timezone.utc)


def persist_internship(db, *, email_id: str = "gmail_m1", now: datetime = NOW) -> EmailRecord:
    email = internship_email(email_id)
    return PersistenceService(db).persist_decision(email, decision_for(email, now=now))


def make_monitored(
    db,
    *,
    email_id: str = "gmail_m1",
    remaining: timedelta = timedelta(hours=5),
    now: datetime = NOW,
    priority: str | None = None,
    viewed: bool = False,
    completed_actions: bool = False,
    monitoring: bool | None = True,
    ambiguous: bool = False,
    snoozed_until: datetime | None = None,
) -> EmailRecord:
    """``monitoring``: True = start it now, False = explicitly stopped,
    None = leave off so the monitor's auto-start can pick it up."""
    """Persist an internship email and shape its deadline/state for a scenario."""
    svc = PersistenceService(db)
    rec = persist_internship(db, email_id=email_id, now=now)
    dl: DeadlineRecord = rec.deadlines[0]

    if ambiguous:
        dl.deadline_datetime = None
        dl.is_ambiguous = True
        dl.is_past = False
    else:
        dl.deadline_datetime = now + remaining
        dl.is_past = (now + remaining) <= now

    if priority:
        rec.priority_level = priority
    if viewed:
        rec.is_viewed = True
        rec.viewed_at = now
    if snoozed_until:
        rec.snoozed_until = snoozed_until

    if monitoring is True:
        dl.is_monitoring = True
        dl.monitoring_started_at = now
    elif monitoring is False:
        dl.is_monitoring = False
        dl.monitoring_stopped_at = now  # explicitly stopped ⇒ no auto-restart
    # monitoring is None → leave is_monitoring=False, stopped_at=None (auto-start eligible)

    db.commit()

    if completed_actions:
        for a in [x for x in rec.actions if x.blocking] or rec.actions:
            svc.set_action_status(rec.email_id, a.action_ref, "COMPLETED")
        db.refresh(rec)
    return rec


def deadline_of(rec: EmailRecord) -> DeadlineRecord:
    return rec.deadlines[0]
