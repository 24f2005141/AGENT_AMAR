"""Mutually-exclusive primary inbox category.

Every email maps to exactly ONE of REPLY_REQUIRED / ACTION_REQUIRED / IMPORTANT /
LOW_PRIORITY. The Flutter inbox sections filter on this, so nothing is ever
double-listed (in particular: a "reply required" email is NOT also "action
required").
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.agents.amar_orchestrator import AMAROrchestrator
from app.db import session as db_session
from app.db.backfill import backfill_primary_category
from app.db.models import EmailRecord
from app.main import app
from app.models.decision import DecisionAction, PrimaryCategory
from app.services.persistence_service import PersistenceService
from tests.persistence_helpers import (
    decision_for,
    default_user_pk,
    internship_email,
    promo_email,
)
from tests.triage_helpers import make_email

client = TestClient(app)

_PRIMARY = ("REPLY_REQUIRED", "ACTION_REQUIRED", "IMPORTANT", "LOW_PRIORITY")


# --- unit: the collapse function -------------------------------------

def _pc(**kw):
    kw.setdefault("category", "OTHER")
    kw.setdefault("action_required", False)
    kw.setdefault("primary_action_type", None)
    kw.setdefault("actions", [])
    kw.setdefault("priority_level", "LOW")
    return AMAROrchestrator._primary_category(**kw)


def test_reply_category_wins_over_action():
    # Triage says REPLY_REQUIRED AND there is a (reply) action → still REPLY_REQUIRED
    assert _pc(category="REPLY_REQUIRED", action_required=True,
               primary_action_type="REPLY") is PrimaryCategory.REPLY_REQUIRED


def test_reply_action_alone_is_reply_required():
    assert _pc(category="OTHER", action_required=True, primary_action_type="REPLY",
               actions=[DecisionAction(action_type="REPLY")]) is PrimaryCategory.REPLY_REQUIRED


def test_non_reply_action_is_action_required_not_important():
    assert _pc(category="INTERNSHIP", action_required=True,
               primary_action_type="FORM_SUBMISSION",
               priority_level="CRITICAL") is PrimaryCategory.ACTION_REQUIRED


def test_high_priority_no_action_is_important():
    assert _pc(category="FACULTY_ANNOUNCEMENT", action_required=False,
               priority_level="HIGH") is PrimaryCategory.IMPORTANT
    assert _pc(priority_level="URGENT") is PrimaryCategory.IMPORTANT
    assert _pc(priority_level="CRITICAL") is PrimaryCategory.IMPORTANT


def test_everything_else_is_low_priority():
    assert _pc(category="PROMOTIONAL", priority_level="LOW") is PrimaryCategory.LOW_PRIORITY
    assert _pc(category="NEWSLETTER", priority_level="MEDIUM") is PrimaryCategory.LOW_PRIORITY


# --- integration: persisted + queried ------------------------------

def _reply_email(eid="gmail_pc_reply"):
    return make_email(
        sender="prof@college.edu",
        subject="Meeting tomorrow",
        body="Hi, please reply to this email to confirm whether you can attend "
        "tomorrow's project meeting.",
    ).model_copy(update={"email_id": eid, "thread_id": f"{eid}_t"})


def _important_email(eid="gmail_pc_imp"):
    return make_email(
        sender="dean@college.edu",
        subject="URGENT: campus closure notice",
        body="The campus will be closed tomorrow due to weather. This notice is "
        "for your information only; no action is required.",
    ).model_copy(update={"email_id": eid, "thread_id": f"{eid}_t"})


@pytest.fixture
def seeded():
    out = {}
    with db_session.db_session() as db:
        upk = default_user_pk(db)
        svc = PersistenceService(db, user_pk=upk)
        for key, email in (
            ("reply", _reply_email()),
            ("action", internship_email("gmail_pc_action")),  # form + upload
            ("important", _important_email()),
            ("low", promo_email("gmail_pc_low")),
        ):
            out[key] = svc.persist_decision(email, decision_for(email)).email_id
    return out


def test_reply_email_is_reply_required_only(seeded):
    detail = client.get(f"/api/v1/emails/{seeded['reply']}").json()
    assert detail["primary_category"] == "REPLY_REQUIRED"
    # secondary metadata still there
    assert "action_required" in detail and "priority_level" in detail and "final_category" in detail

    reply_ids = {e["email_id"] for e in
                 client.get("/api/v1/emails", params={"primary_category": "REPLY_REQUIRED"}).json()}
    action_ids = {e["email_id"] for e in
                  client.get("/api/v1/emails", params={"primary_category": "ACTION_REQUIRED"}).json()}
    assert seeded["reply"] in reply_ids
    assert seeded["reply"] not in action_ids  # the whole point


def test_action_email_is_action_required(seeded):
    detail = client.get(f"/api/v1/emails/{seeded['action']}").json()
    assert detail["primary_category"] == "ACTION_REQUIRED"


def test_every_email_has_exactly_one_primary_category_and_buckets_are_disjoint(seeded):
    all_emails = client.get("/api/v1/emails").json()
    assert all_emails
    seen: dict[str, set[str]] = {p: set() for p in _PRIMARY}
    for e in all_emails:
        pc = e["primary_category"]
        assert pc in _PRIMARY
        seen[pc].add(e["email_id"])

    # each bucket query returns exactly the emails with that primary_category
    for p in _PRIMARY:
        got = {e["email_id"] for e in
               client.get("/api/v1/emails", params={"primary_category": p}).json()}
        assert got == seen[p], p

    # union covers everything, pairwise intersections empty
    union: set[str] = set()
    for a in _PRIMARY:
        for b in _PRIMARY:
            if a != b:
                assert not (seen[a] & seen[b])
        union |= seen[a]
    assert union == {e["email_id"] for e in all_emails}


# --- backfill: repair rows written before the column existed --------

def test_backfill_recomputes_stale_primary_category(seeded):
    """A legacy row stuck at the LOW_PRIORITY default is corrected from its
    already-stored final_category / action / priority signals."""
    with db_session.db_session() as db:
        # simulate the pre-feature state: every row back to the column default
        for row in db.query(EmailRecord).all():
            row.primary_category = "LOW_PRIORITY"
        db.commit()

        changed = backfill_primary_category(db)
        assert changed >= 1  # at least the reply + action emails move

        by_id = {r.email_id: r.primary_category for r in db.query(EmailRecord).all()}
    assert by_id[seeded["reply"]] == "REPLY_REQUIRED"
    assert by_id[seeded["action"]] == "ACTION_REQUIRED"


def test_backfill_is_idempotent(seeded):
    with db_session.db_session() as db:
        backfill_primary_category(db)
        assert backfill_primary_category(db) == 0
