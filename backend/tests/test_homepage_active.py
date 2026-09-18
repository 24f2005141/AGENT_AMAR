"""Attention-dashboard homepage feed — ``GET /api/v1/emails?active=true``.

The homepage shows only emails that still need the user's attention:

* REPLY_REQUIRED / ACTION_REQUIRED  → until resolved (opening is not enough)
* IMPORTANT / LOW_PRIORITY          → until opened / acknowledged

Nothing is ever deleted — an inactive email still comes back on the plain list
and on the detail endpoint.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.db import session as db_session
from app.db.models import EmailRecord
from app.main import app
from app.repositories.email_repository import active_attention_filter
from app.services.persistence_service import PersistenceService
from tests.persistence_helpers import (
    decision_for,
    default_user_pk,
    internship_email,
    promo_email,
)

client = TestClient(app)


def _set_state(email_id: str, **fields) -> None:
    """Force an email's stored lifecycle state (bypassing the agents)."""
    with db_session.db_session() as db:
        row = db.query(EmailRecord).filter(EmailRecord.email_id == email_id).one()
        for key, value in fields.items():
            setattr(row, key, value)
        db.commit()


@pytest.fixture
def seeded():
    """internship (ACTION_REQUIRED) + promo (LOW_PRIORITY), freshly ingested."""
    out = {}
    with db_session.db_session() as db:
        svc = PersistenceService(db, user_pk=default_user_pk(db))
        intern = internship_email()
        promo = promo_email()
        out["intern"] = svc.persist_decision(intern, decision_for(intern)).email_id
        out["promo"] = svc.persist_decision(promo, decision_for(promo)).email_id
    # sanity: the fixtures land in the buckets the rest of the test assumes
    with db_session.db_session() as db:
        rows = {r.email_id: r for r in db.query(EmailRecord).all()}
        assert rows[out["intern"]].primary_category == "ACTION_REQUIRED"
        assert rows[out["promo"]].primary_category == "LOW_PRIORITY"
    return out


def _active_ids() -> list[str]:
    r = client.get("/api/v1/emails", params={"active": "true"})
    assert r.status_code == 200
    return [e["email_id"] for e in r.json()]


# --- new / unseen emails appear -----------------------------------------

def test_new_emails_are_all_active(seeded):
    assert set(_active_ids()) == {seeded["intern"], seeded["promo"]}
    # and each row carries the derived flag
    for e in client.get("/api/v1/emails").json():
        assert e["is_active"] is True


# --- low priority: opened / acknowledged → drops off ------------------

def test_low_priority_drops_off_once_viewed(seeded):
    client.patch(f"/api/v1/emails/{seeded['promo']}/viewed")

    assert seeded["promo"] not in _active_ids()
    # still fully retrievable — not deleted
    assert client.get("/api/v1/emails").status_code == 200
    assert any(e["email_id"] == seeded["promo"] for e in client.get("/api/v1/emails").json())
    assert client.get(f"/api/v1/emails/{seeded['promo']}").status_code == 200
    assert client.get(f"/api/v1/emails/{seeded['promo']}").json()["is_active"] is False


def test_acknowledged_low_priority_does_not_reappear_after_refresh(seeded):
    client.patch(f"/api/v1/emails/{seeded['promo']}/viewed")
    # simulate several homepage refreshes
    for _ in range(3):
        assert seeded["promo"] not in _active_ids()


# --- important informational: opened → drops off ---------------------

def test_important_informational_drops_off_once_viewed(seeded):
    _set_state(seeded["promo"], primary_category="IMPORTANT", priority_level="HIGH")
    assert seeded["promo"] in _active_ids()

    client.patch(f"/api/v1/emails/{seeded['promo']}/viewed")
    assert seeded["promo"] not in _active_ids()


def test_important_with_unresolved_action_stays_visible_after_viewing(seeded):
    # an IMPORTANT-priority email that still carries an action is bucketed
    # ACTION_REQUIRED and must survive being opened
    _set_state(seeded["intern"], priority_level="HIGH")  # still ACTION_REQUIRED
    client.patch(f"/api/v1/emails/{seeded['intern']}/viewed")
    assert seeded["intern"] in _active_ids()


# --- action required: opened stays, completed drops ------------------

def test_action_required_opened_stays_visible(seeded):
    client.patch(f"/api/v1/emails/{seeded['intern']}/viewed")
    assert seeded["intern"] in _active_ids()


def test_action_required_removed_immediately_on_completion(seeded):
    detail = client.get(f"/api/v1/emails/{seeded['intern']}").json()
    for action in detail["actions"]:
        r = client.patch(
            f"/api/v1/emails/{seeded['intern']}/actions/{action['action_ref']}/complete"
        )
        assert r.status_code == 200
    assert client.get(f"/api/v1/emails/{seeded['intern']}").json()["is_completed"] is True

    assert seeded["intern"] not in _active_ids()
    # preserved in storage / history
    assert any(e["email_id"] == seeded["intern"] for e in client.get("/api/v1/emails").json())
    assert client.get(f"/api/v1/emails/{seeded['intern']}").json()["is_completed"] is True


def test_completed_items_do_not_reappear_after_refresh(seeded):
    _set_state(seeded["intern"], is_completed=True)
    for _ in range(3):
        assert seeded["intern"] not in _active_ids()


# --- reply required -------------------------------------------------

def test_reply_required_opened_stays_until_resolved(seeded):
    _set_state(seeded["promo"], primary_category="REPLY_REQUIRED")
    client.patch(f"/api/v1/emails/{seeded['promo']}/viewed")
    assert seeded["promo"] in _active_ids()

    # sending a reply / resolving marks the email completed → drops off
    _set_state(seeded["promo"], is_completed=True)
    assert seeded["promo"] not in _active_ids()


# --- snooze --------------------------------------------------------

def test_snoozed_email_is_not_active_until_snooze_expires(seeded):
    future = datetime.now(timezone.utc) + timedelta(hours=6)
    _set_state(seeded["intern"], snoozed_until=future)
    assert seeded["intern"] not in _active_ids()

    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    _set_state(seeded["intern"], snoozed_until=past)
    assert seeded["intern"] in _active_ids()


# --- active=false is the complement (history / archive) ------------

def test_active_false_returns_only_resolved_or_acknowledged(seeded):
    client.patch(f"/api/v1/emails/{seeded['promo']}/viewed")  # acknowledged low-priority
    r = client.get("/api/v1/emails", params={"active": "false"})
    assert r.status_code == 200
    assert [e["email_id"] for e in r.json()] == [seeded["promo"]]


def test_active_true_and_false_partition_the_list(seeded):
    _set_state(seeded["promo"], is_viewed=True)
    all_ids = {e["email_id"] for e in client.get("/api/v1/emails").json()}
    active = set(_active_ids())
    inactive = {e["email_id"] for e in client.get("/api/v1/emails", params={"active": "false"}).json()}
    assert active | inactive == all_ids
    assert active & inactive == set()


# --- the SQL predicate matches the ORM property -------------------

def test_sql_filter_agrees_with_is_active_property(seeded):
    client.patch(f"/api/v1/emails/{seeded['promo']}/viewed")
    with db_session.db_session() as db:
        via_sql = {
            r.email_id
            for r in db.query(EmailRecord).filter(active_attention_filter()).all()
        }
        via_prop = {r.email_id for r in db.query(EmailRecord).all() if r.is_active}
    assert via_sql == via_prop
