"""PersistenceService + repositories (Phase 9, STEP 18 items 1-32, 44-49)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.db.models import EmailRecord
from app.repositories import (
    ActionRepository,
    DeadlineRepository,
    EmailRepository,
    NotificationRepository,
    ProcessingRepository,
)
from app.services.persistence_service import PersistenceService
from tests.persistence_helpers import (
    decision_for,
    internship_email,
    orchestrator,
    promo_email,
)


@pytest.fixture
def svc(db):
    return PersistenceService(db)


# --- EMAIL PERSISTENCE ---------------------------------------------------

def test_persist_new_email(svc):
    email = internship_email()
    rec = svc.persist_decision(email, decision_for(email))
    assert rec.id is not None
    assert rec.email_id == email.email_id
    assert rec.final_category == "INTERNSHIP"
    assert rec.priority_level in {"URGENT", "CRITICAL", "HIGH"}


def test_retrieve_persisted_email(svc, db):
    email = internship_email()
    svc.persist_decision(email, decision_for(email))
    got = EmailRepository(db).get_by_email_id(email.email_id, with_children=True)
    assert got is not None
    assert got.subject.startswith("Summer Internship")
    assert len(got.actions) >= 1


def test_duplicate_processing_no_duplicate_email(svc, db):
    email = internship_email()
    r1 = svc.persist_decision(email, decision_for(email))
    r2 = svc.persist_decision(email, decision_for(email))
    assert r1.id == r2.id
    assert db.query(EmailRecord).count() == 1


def test_reprocessing_updates_analysis(svc):
    email = internship_email()
    svc.persist_decision(email, decision_for(email))
    # a second, different-looking email under the SAME id → analysis should change
    changed = email.model_copy(update={"subject": "Weekly shopping deals",
                                       "body": "Huge sale, buy now, 70% off!",
                                       "sender": email.sender.model_copy(update={"email": "deals@x.com"})})
    rec = svc.persist_decision(changed, decision_for(changed))
    assert rec.final_category == "PROMOTIONAL"
    assert rec.priority_level == "LOW"


def test_reprocessing_preserves_viewed(svc):
    email = internship_email()
    svc.persist_decision(email, decision_for(email))
    svc.mark_viewed(email.email_id)
    rec = svc.persist_decision(email, decision_for(email))
    assert rec.is_viewed is True
    assert rec.viewed_at is not None


def test_reprocessing_preserves_completed_actions(svc):
    email = internship_email()
    r = svc.persist_decision(email, decision_for(email))
    ref = r.actions[0].action_ref
    svc.set_action_status(email.email_id, ref, "COMPLETED")
    rec = svc.persist_decision(email, decision_for(email))
    same = next(a for a in rec.actions if a.action_ref == ref)
    assert same.status == "COMPLETED"


def test_reprocessing_preserves_snooze(svc):
    email = internship_email()
    svc.persist_decision(email, decision_for(email))
    until = datetime.now(timezone.utc) + timedelta(days=1)
    svc.snooze(email.email_id, until)
    rec = svc.persist_decision(email, decision_for(email))
    assert rec.snoozed_until is not None


# --- ACTIONS ---------------------------------------------------------

def test_persist_single_action(svc):
    email = internship_email()
    email = email.model_copy(update={"body": "Please fill the form https://forms.gle/x."})
    rec = svc.persist_decision(email, decision_for(email))
    assert len(rec.actions) == 1
    assert rec.actions[0].status == "PENDING"


def test_persist_multiple_actions(svc):
    email = internship_email()  # apply via form + upload resume
    rec = svc.persist_decision(email, decision_for(email))
    assert len(rec.actions) >= 2
    assert len({a.action_ref for a in rec.actions}) == len(rec.actions)


def test_mark_action_completed_sets_completed_at(svc):
    email = internship_email()
    r = svc.persist_decision(email, decision_for(email))
    result = svc.set_action_status(email.email_id, r.actions[0].action_ref, "COMPLETED")
    assert result is not None
    _rec, action = result
    assert action.status == "COMPLETED" and action.completed_at is not None


def test_dismiss_action(svc):
    email = internship_email()
    r = svc.persist_decision(email, decision_for(email))
    _rec, action = svc.set_action_status(email.email_id, r.actions[0].action_ref, "DISMISSED")
    assert action.status == "DISMISSED"


def test_all_blocking_actions_done_marks_email_completed(svc):
    email = internship_email()
    r = svc.persist_decision(email, decision_for(email))
    for a in [x for x in r.actions if x.blocking] or r.actions:
        svc.set_action_status(email.email_id, a.action_ref, "COMPLETED")
    rec = svc.persist_decision(email, decision_for(email))  # reprocess
    assert rec.is_completed is True
    assert rec.completed_at is not None


def test_pending_actions_repo(svc, db):
    email = internship_email()
    svc.persist_decision(email, decision_for(email))
    pending = ActionRepository(db).list_pending()
    assert pending
    assert all(a.status == "PENDING" for a, _e in pending)


def test_orphan_pending_action_removed_on_reprocess(svc):
    email = internship_email()
    r = svc.persist_decision(email, decision_for(email))
    n0 = len(r.actions)
    stripped = email.model_copy(update={"body": "Here are the placement statistics for reference."})
    rec = svc.persist_decision(stripped, decision_for(stripped))
    assert len(rec.actions) < n0  # untouched, no-longer-detected actions are dropped


# --- DEADLINES -----------------------------------------------------

def test_persist_single_deadline(svc):
    email = internship_email().model_copy(
        update={"body": "Submit the form https://forms.gle/x by 5 September 2026."}
    )
    rec = svc.persist_decision(email, decision_for(email))
    assert len(rec.deadlines) == 1
    assert rec.deadlines[0].deadline_datetime is not None


def test_persist_multiple_deadlines(svc):
    email = internship_email().model_copy(update={
        "body": "Register via the form by 1 September 2026 and upload your resume "
        "by 3 September 2026."
    })
    rec = svc.persist_decision(email, decision_for(email))
    assert len(rec.deadlines) == 2


def test_deadline_ambiguity_metadata_preserved(svc):
    email = internship_email().model_copy(update={
        "body": "Apply via the form https://forms.gle/x — submit by next Friday."
    })
    rec = svc.persist_decision(email, decision_for(email))
    assert any(d.is_ambiguous for d in rec.deadlines) or rec.deadlines[0].ambiguity_reason


def test_upcoming_deadlines_repo(svc, db):
    email = internship_email()
    svc.persist_decision(email, decision_for(email, now=datetime(2026, 9, 4, 12, tzinfo=timezone.utc)))
    up = DeadlineRepository(db).list_upcoming()
    assert up  # the 5 Sept deadline is in the future relative to real "now"


def test_start_stop_monitoring(svc, db):
    email = internship_email()
    rec = svc.persist_decision(email, decision_for(email))
    dl = rec.deadlines[0]
    repo = DeadlineRepository(db)
    repo.start_monitoring(dl)
    db.commit()
    assert dl.is_monitoring and dl.monitoring_started_at is not None
    repo.stop_monitoring(dl)
    db.commit()
    assert dl.is_monitoring is False and dl.monitoring_stopped_at is not None


def test_deadline_reprocess_no_duplicate(svc, db):
    email = internship_email()
    svc.persist_decision(email, decision_for(email))
    svc.persist_decision(email, decision_for(email))
    from app.db.models import DeadlineRecord
    email_pk = EmailRepository(db).get_by_email_id(email.email_id).id
    assert db.query(DeadlineRecord).filter_by(email_pk=email_pk).count() == len(
        DeadlineRepository(db).list_by_email(email_pk)
    )
    # refs are unique per email
    refs = [d.deadline_ref for d in DeadlineRepository(db).list_by_email(email_pk)]
    assert len(refs) == len(set(refs))


def test_monitoring_preserved_on_reprocess(svc, db):
    email = internship_email()
    rec = svc.persist_decision(email, decision_for(email))
    DeadlineRepository(db).start_monitoring(rec.deadlines[0])
    db.commit()
    rec2 = svc.persist_decision(email, decision_for(email))
    assert any(d.is_monitoring for d in rec2.deadlines)


# --- PROCESSING HISTORY ------------------------------------------

def test_processing_run_created(svc, db):
    email = internship_email()
    rec = svc.persist_decision(email, decision_for(email))
    runs = ProcessingRepository(db).list_by_email(rec.id)
    assert len(runs) == 1
    assert runs[0].run_id.startswith("run_")


def test_reprocessing_appends_history(svc, db):
    email = internship_email()
    r = svc.persist_decision(email, decision_for(email))
    svc.persist_decision(email, decision_for(email))
    svc.persist_decision(email, decision_for(email))
    assert ProcessingRepository(db).count_for(r.id) == 3


def test_agent_trace_and_errors_persist(svc, db):
    email = internship_email()
    rec = svc.persist_decision(email, decision_for(email))
    run = ProcessingRepository(db).latest_for(rec.id)
    assert isinstance(run.agent_trace, list) and run.agent_trace
    assert run.agent_trace[0]["agent"] == "Mail Intake Agent"
    assert isinstance(run.errors, list)


def test_processing_status_queryable(svc, db):
    email = internship_email()
    rec = svc.persist_decision(email, decision_for(email))
    run = ProcessingRepository(db).latest_for(rec.id)
    assert run.status in {"ok", "partial", "error"}


# --- STATE ------------------------------------------------------

def test_mark_viewed(svc):
    email = internship_email()
    svc.persist_decision(email, decision_for(email))
    rec = svc.mark_viewed(email.email_id)
    assert rec.is_viewed and rec.viewed_at


def test_snooze_persists(svc, db):
    email = internship_email()
    svc.persist_decision(email, decision_for(email))
    until = datetime.now(timezone.utc) + timedelta(hours=6)
    svc.snooze(email.email_id, until)
    got = EmailRepository(db).get_by_email_id(email.email_id)
    assert got.snoozed_until is not None


def test_independent_state_combinations(svc):
    email = internship_email()
    r = svc.persist_decision(email, decision_for(email))
    svc.mark_viewed(email.email_id)
    svc.snooze(email.email_id, datetime.now(timezone.utc) + timedelta(days=1))
    svc.set_action_status(email.email_id, r.actions[0].action_ref, "COMPLETED")
    rec = svc.persist_decision(email, decision_for(email))
    # viewed + snoozed + one action done + still action_required (2nd action pending)
    assert rec.is_viewed and rec.snoozed_until and rec.action_required
    assert rec.is_completed is False


# --- NOTIFICATIONS -------------------------------------------

def test_notification_created_when_notify(svc, db):
    email = internship_email()
    rec = svc.persist_decision(email, decision_for(email))
    notes = NotificationRepository(db).list_by_email(rec.id)
    assert len(notes) == 1
    assert notes[0].status == "PENDING"


def test_no_duplicate_notification_on_reprocess(svc, db):
    email = internship_email()
    r = svc.persist_decision(email, decision_for(email))
    svc.persist_decision(email, decision_for(email))
    assert len(NotificationRepository(db).list_by_email(r.id)) == 1


def test_no_notification_for_low_priority(svc, db):
    email = promo_email()
    rec = svc.persist_decision(email, decision_for(email))
    assert NotificationRepository(db).list_by_email(rec.id) == []


def test_notification_queryable(svc, db):
    email = internship_email()
    svc.persist_decision(email, decision_for(email))
    assert NotificationRepository(db).list_unsent()


# --- INTEGRATION -------------------------------------------

def test_reprocess_same_message_twice_one_email_many_runs(svc, db):
    email = internship_email()
    orch = orchestrator()
    svc.persist_decision(email, decision_for(email, orch=orch))
    svc.mark_viewed(email.email_id)
    svc.persist_decision(email, decision_for(email, orch=orch))
    assert db.query(EmailRecord).count() == 1
    pk = EmailRepository(db).get_by_email_id(email.email_id).id
    assert ProcessingRepository(db).count_for(pk) == 2
    assert EmailRepository(db).get_by_email_id(email.email_id).is_viewed is True
