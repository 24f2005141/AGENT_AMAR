"""Phase 10 — monitor / reminder / notification endpoints (STEP 20 items 17-19, 43-51)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.db import session as db_session
from app.main import app
from app.repositories import NotificationRepository
from tests.monitor_helpers import NOW, make_monitored, persist_internship

client = TestClient(app)


def _future_iso(**delta) -> str:
    """A real wall-clock future instant — the reminder API uses ``datetime.now``."""
    return (datetime.now(timezone.utc) + timedelta(**delta)).isoformat()


@pytest.fixture
def escalating():
    """An internship email whose deadline is 2h out (URGENT rung) + monitored."""
    with db_session.db_session() as db:
        rec = make_monitored(db, remaining=timedelta(hours=2))
        return {"email_id": rec.email_id, "pk": rec.id}


@pytest.fixture
def seeded():
    with db_session.db_session() as db:
        rec = persist_internship(db)
        return {"email_id": rec.email_id, "action_ref": rec.actions[0].action_ref}


# --- monitor check endpoint --------------------------------------------

def test_monitor_check_runs_and_summarises(escalating):
    r = client.post("/api/v1/monitor/deadlines/check", json={"now": NOW.isoformat()})
    assert r.status_code == 200
    body = r.json()
    assert body["deadlines_evaluated"] >= 1
    assert body["notifications_created"] >= 1
    assert any(d["decision"] == "URGENT" for d in body["results"])


def test_monitor_check_accepts_no_body():
    r = client.post("/api/v1/monitor/deadlines/check")
    assert r.status_code == 200
    assert "checked_at" in r.json()


def test_monitor_check_is_idempotent(escalating):
    client.post("/api/v1/monitor/deadlines/check", json={"now": NOW.isoformat()})
    r2 = client.post("/api/v1/monitor/deadlines/check", json={"now": NOW.isoformat()})
    assert all(d["decision"] != "URGENT" for d in r2.json()["results"])


# --- reminder endpoints -------------------------------------------

def test_create_and_list_reminder(seeded):
    r = client.post(
        f"/api/v1/emails/{seeded['email_id']}/reminders",
        json={"reminder_at": _future_iso(days=2)},
    )
    assert r.status_code == 201
    rid = r.json()["id"]

    listed = client.get(f"/api/v1/emails/{seeded['email_id']}/reminders")
    assert listed.status_code == 200
    assert [x["id"] for x in listed.json()] == [rid]


def test_create_reminder_bad_time_is_400(seeded):
    r = client.post(
        f"/api/v1/emails/{seeded['email_id']}/reminders",
        json={"reminder_at": _future_iso(days=-2)},
    )
    assert r.status_code == 400


def test_create_reminder_unknown_email_is_404():
    r = client.post(
        "/api/v1/emails/gmail_nope/reminders", json={"reminder_at": _future_iso(days=2)}
    )
    assert r.status_code == 404


def test_cancel_reminder(seeded):
    rid = client.post(
        f"/api/v1/emails/{seeded['email_id']}/reminders",
        json={"reminder_at": _future_iso(days=2)},
    ).json()["id"]
    r = client.delete(f"/api/v1/emails/{seeded['email_id']}/reminders/{rid}")
    assert r.status_code == 200
    assert r.json()["status"] == "CANCELLED"


def test_reminder_tied_to_action_via_api(seeded):
    r = client.post(
        f"/api/v1/emails/{seeded['email_id']}/reminders",
        json={"reminder_at": _future_iso(days=2), "action_ref": seeded["action_ref"]},
    )
    assert r.status_code == 201
    assert r.json()["action_ref"] == seeded["action_ref"]


# --- notification query endpoints -------------------------------

def test_notifications_list_and_filter(escalating):
    client.post("/api/v1/monitor/deadlines/check", json={"now": NOW.isoformat()})
    alln = client.get("/api/v1/notifications")
    assert alln.status_code == 200
    assert alln.json()

    by_type = client.get("/api/v1/notifications", params={"type": "deadline_escalation"})
    assert by_type.json()
    assert all(n["notification_type"] == "deadline_escalation" for n in by_type.json())

    by_email = client.get(
        "/api/v1/notifications", params={"email_id": escalating["email_id"]}
    )
    assert all(n["email_id"] == escalating["email_id"] for n in by_email.json())


def test_notification_detail_and_404(escalating):
    client.post("/api/v1/monitor/deadlines/check", json={"now": NOW.isoformat()})
    first = client.get("/api/v1/notifications").json()[0]
    one = client.get(f"/api/v1/notifications/{first['id']}")
    assert one.status_code == 200
    assert one.json()["id"] == first["id"]
    assert client.get("/api/v1/notifications/999999").status_code == 404


def test_alarm_flag_surfaced_in_notifications(seeded):
    with db_session.db_session() as db:
        make_monitored(db, email_id="gmail_alarm", remaining=timedelta(minutes=3),
                       priority="CRITICAL")
    client.post("/api/v1/monitor/deadlines/check", json={"now": NOW.isoformat()})
    alarms = client.get("/api/v1/notifications", params={"requires_alarm": True})
    assert alarms.status_code == 200
    assert alarms.json()
    assert all(n["requires_alarm"] for n in alarms.json())
    assert all(n["severity"] == "ALARM" for n in alarms.json())


# --- prior endpoints still work (STEP 22) -----------------------

def test_phase9_and_earlier_endpoints_unaffected(seeded):
    assert client.get("/health").status_code == 200
    assert client.get("/api/v1/emails").status_code == 200
    assert client.get(f"/api/v1/emails/{seeded['email_id']}").status_code == 200
    assert client.get("/api/v1/actions/pending").status_code == 200
    assert client.get("/api/v1/deadlines/upcoming").status_code == 200
    root = client.get("/").json()
    assert "phase" in root
    assert "/api/v1/monitor/deadlines/check" in root["endpoints"]
