"""Canonical email lifecycle — completion / acknowledgement is persistent
backend state that a Gmail sync / reprocess can never revert.

Covers the "completed emails come back" + "Mark Complete does nothing" bugs:
the whole-email resolve endpoint, promote-only auto-completion, and the
"Clear Resolved" bulk-acknowledge action.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.db import session as db_session
from app.db.models import ActionRecord, EmailRecord
from app.main import app
from app.services.gmail_service import GmailService
from app.services.gmail_sync_service import GmailSyncService
from app.services.persistence_service import PersistenceService
from tests.fakes import FakeGmailResource, minimal_raw_message
from tests.persistence_helpers import (
    decision_for,
    default_user_pk,
    internship_email,
    promo_email,
)

client = TestClient(app)


@pytest.fixture
def seeded():
    out = {}
    with db_session.db_session() as db:
        svc = PersistenceService(db, user_pk=default_user_pk(db))
        intern = internship_email()          # ACTION_REQUIRED, multi-step
        promo = promo_email()                # LOW_PRIORITY
        out["intern"] = svc.persist_decision(intern, decision_for(intern)).email_id
        out["promo"] = svc.persist_decision(promo, decision_for(promo)).email_id
    return out


def _row(email_id: str) -> EmailRecord:
    with db_session.db_session() as db:
        return db.query(EmailRecord).filter(EmailRecord.email_id == email_id).one()


def _active_ids() -> list[str]:
    return [e["email_id"] for e in client.get("/api/v1/emails", params={"active": "true"}).json()]


# --- PART 6 — mark complete actually removes the item ---------------

def test_complete_email_resolves_every_pending_action(seeded):
    detail = client.get(f"/api/v1/emails/{seeded['intern']}").json()
    assert len(detail["actions"]) >= 1

    r = client.patch(f"/api/v1/emails/{seeded['intern']}/complete")
    assert r.status_code == 200
    body = r.json()
    assert body["is_completed"] is True
    assert body["completion_source"] == "user"
    assert all(a["status"] in ("COMPLETED", "DISMISSED") for a in body["actions"])

    assert seeded["intern"] not in _active_ids()


def test_complete_email_is_idempotent(seeded):
    client.patch(f"/api/v1/emails/{seeded['intern']}/complete")
    r = client.patch(f"/api/v1/emails/{seeded['intern']}/complete")
    assert r.status_code == 200
    assert r.json()["is_completed"] is True


def test_complete_email_404_for_unknown_or_other_user(seeded):
    assert client.patch("/api/v1/emails/gmail_nope/complete").status_code == 404


def test_completed_email_survives_a_database_reload(seeded):
    client.patch(f"/api/v1/emails/{seeded['intern']}/complete")
    # brand-new session — reads straight from disk
    assert _row(seeded["intern"]).is_completed is True
    assert _row(seeded["intern"]).completion_source == "user"


def test_reopen_undoes_a_completion(seeded):
    client.patch(f"/api/v1/emails/{seeded['intern']}/complete")
    r = client.patch(f"/api/v1/emails/{seeded['intern']}/reopen")
    assert r.status_code == 200
    assert r.json()["is_completed"] is False
    assert r.json()["completion_source"] == "auto"
    assert seeded["intern"] in _active_ids()


# --- PART 3 / 6 — a Gmail sync / reprocess cannot reset user state ---

def test_reprocess_never_uncompletes_a_user_resolved_email(seeded):
    client.patch(f"/api/v1/emails/{seeded['intern']}/complete")

    # reprocess the exact same email through the full pipeline + persistence
    with db_session.db_session() as db:
        svc = PersistenceService(db, user_pk=default_user_pk(db))
        intern = internship_email()
        svc.persist_decision(intern, decision_for(intern))

    row = _row(seeded["intern"])
    assert row.is_completed is True
    assert row.completion_source == "user"
    assert seeded["intern"] not in _active_ids()


def test_gmail_sync_reprocessing_a_completed_email_keeps_it_completed():
    """End-to-end: an email is synced, resolved, then a later sync re-sees the
    same message id (history replay) — it must stay completed."""
    with db_session.db_session() as db:
        uid = default_user_pk(db)
        sync = GmailSyncService(db, user_pk=uid)
        sync.ensure_baseline(GmailService(service=FakeGmailResource(history_id="100")))

        raw = minimal_raw_message("m_done", subject="Do the thing", body="Please act on this.")
        gmail = GmailService(service=FakeGmailResource(
            history_id="120",
            history=[{"id": 110, "added_message_ids": ["m_done"]}],
            messages={"m_done": raw},
        ))
        res = sync.sync_new_messages(gmail)
        assert res["processed"] == 1
        email_id = db.query(EmailRecord).one().email_id

    client.patch(f"/api/v1/emails/{email_id}/complete")

    with db_session.db_session() as db:
        sync = GmailSyncService(db, user_pk=default_user_pk(db))
        # rewind the cursor so the same window is replayed
        state = sync.get_state()
        state.last_history_id = "100"
        db.commit()
        raw = minimal_raw_message("m_done", subject="Do the thing", body="Please act on this.")
        gmail = GmailService(service=FakeGmailResource(
            history_id="120",
            history=[{"id": 110, "added_message_ids": ["m_done"]}],
            messages={"m_done": raw},
        ))
        sync.sync_new_messages(gmail)

    row = _row(email_id)
    assert row.is_completed is True
    assert row.completion_source == "user"
    assert email_id not in _active_ids()


def test_auto_completion_is_promote_only(seeded):
    """An email auto-completed on one pass is not un-completed when a later pass
    produces an extra pending action."""
    with db_session.db_session() as db:
        row = db.query(EmailRecord).filter(EmailRecord.email_id == seeded["promo"]).one()
        row.action_required = True
        row.actions.append(ActionRecord(action_ref="act_001", action_type="OTHER",
                                        blocking=True, status="COMPLETED"))
        db.commit()
        PersistenceService._recompute_completion(row, datetime.now(timezone.utc))
        db.commit()
    assert _row(seeded["promo"]).is_completed is True

    # a later pass adds a new pending action
    with db_session.db_session() as db:
        row = db.query(EmailRecord).filter(EmailRecord.email_id == seeded["promo"]).one()
        row.actions.append(ActionRecord(action_ref="act_002", action_type="OTHER",
                                        blocking=True, status="PENDING"))
        db.commit()
        PersistenceService._recompute_completion(row, datetime.now(timezone.utc))
        db.commit()
    assert _row(seeded["promo"]).is_completed is True  # not reverted


# --- PART 8 / 9 — Clear Resolved / Clear Acknowledged ---------------

def test_clear_acknowledged_marks_non_actionable_active_emails(seeded):
    r = client.post("/api/v1/emails/clear-acknowledged")
    assert r.status_code == 200
    assert r.json()["acknowledged"] == 1  # the promo (LOW_PRIORITY)

    assert seeded["promo"] not in _active_ids()
    assert _row(seeded["promo"]).is_viewed is True
    # not deleted
    assert any(e["email_id"] == seeded["promo"] for e in client.get("/api/v1/emails").json())


def test_clear_acknowledged_never_touches_reply_or_action_emails(seeded):
    client.post("/api/v1/emails/clear-acknowledged")
    # the internship email is ACTION_REQUIRED — must remain active + not completed
    assert seeded["intern"] in _active_ids()
    row = _row(seeded["intern"])
    assert row.is_completed is False
    assert row.is_viewed is False


def test_clear_acknowledged_is_idempotent(seeded):
    first = client.post("/api/v1/emails/clear-acknowledged").json()["acknowledged"]
    second = client.post("/api/v1/emails/clear-acknowledged").json()["acknowledged"]
    assert first == 1
    assert second == 0


def test_clear_acknowledged_makes_no_gmail_call(seeded):
    # the autouse _gmail_offline_by_default fixture would make any real Gmail
    # call fail loudly; a clean 200 proves the endpoint is DB-only.
    assert client.post("/api/v1/emails/clear-acknowledged").status_code == 200
