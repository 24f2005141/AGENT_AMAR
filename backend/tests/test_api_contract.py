"""Phase 10.5 — frontend API contract: end-to-end smoke + new-field coverage.

Exercises the exact flow a Flutter client performs, against the real FastAPI
app with Gmail / OAuth / LLM mocked. No network, deterministic.
"""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.agents.action_agent import ActionAgent
from app.agents.amar_orchestrator import AMAROrchestrator
from app.agents.deadline_agent import DeadlineAgent
from app.agents.priority_agent import PriorityAgent
from app.agents.triage_agent import TriageAgent
from app.api.deps import get_amar_orchestrator, get_gmail_service
from app.core.config import Settings
from app.main import app
from app.services.gmail_service import GmailService
from tests.fakes import FakeGmailResource

client = TestClient(app)

_BODY = (
    "Please submit the application form https://forms.gle/abc and upload your "
    "resume by 5 September 2026, 6:00 PM. This opportunity is important."
)
_RAW = {
    "id": "contract1",
    "threadId": "contract1",
    "labelIds": ["INBOX", "UNREAD"],
    "snippet": "Please submit the application form and upload your resume by 5 September 2026",
    "internalDate": "1725000000000",
    "payload": {
        "mimeType": "text/plain",
        "headers": [
            {"name": "From", "value": "Placement Cell <placement@college.edu>"},
            {"name": "To", "value": "me@college.edu"},
            {"name": "Subject", "value": "Summer Internship 2026 - applications open"},
            {"name": "Date", "value": "Wed, 30 Jul 2025 09:14:22 +0530"},
        ],
        "body": {
            "size": len(_BODY),
            "data": base64.urlsafe_b64encode(_BODY.encode()).decode().rstrip("="),
        },
    },
}


def _orch() -> AMAROrchestrator:
    s = Settings()
    return AMAROrchestrator(
        TriageAgent(settings=s), ActionAgent(settings=s),
        DeadlineAgent(settings=s), PriorityAgent(settings=s), settings=s,
    )


@pytest.fixture
def gmail():
    fake = FakeGmailResource(unread_ids=["contract1"], messages={"contract1": _RAW})
    app.dependency_overrides[get_gmail_service] = lambda: GmailService(service=fake)
    app.dependency_overrides[get_amar_orchestrator] = _orch
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def processed(gmail):
    r = gmail.get("/api/v1/gmail/unread/process")
    assert r.status_code == 200
    return r.json()["emails"][0]["email_id"]


# --- STEP 8 end-to-end frontend flow --------------------------------------

def test_full_frontend_flow(gmail, processed):
    email_id = processed

    # 1. Smart Inbox
    inbox = gmail.get("/api/v1/emails").json()
    assert len(inbox) == 1
    row = inbox[0]
    for key in (
        "email_id", "sender_name", "sender_email", "subject", "snippet",
        "final_category", "priority_level", "priority_score", "action_required",
        "primary_action_type", "next_deadline_at", "is_completed", "is_viewed",
        "is_unread", "snoozed_until", "proximity_bucket",
    ):
        assert key in row, key
    assert row["snippet"]
    assert row["final_category"] == "INTERNSHIP"
    assert row["primary_action_type"] is not None
    assert row["next_deadline_at"] is not None

    # 2. Needs Attention
    pending = gmail.get("/api/v1/actions/pending").json()
    assert pending and pending[0]["email_id"] == email_id
    assert pending[0]["status"] == "PENDING"

    # 3. Deadlines
    gmail.post("/api/v1/monitor/deadlines/check")  # auto-starts monitoring
    deadlines = gmail.get("/api/v1/deadlines/upcoming").json()
    assert deadlines and deadlines[0]["email_id"] == email_id
    assert deadlines[0]["deadline_datetime"] is not None

    # 4. Email detail + agent trace
    detail = gmail.get(f"/api/v1/emails/{email_id}").json()
    assert detail["reasoning_summary"]
    assert detail["category_confidence"] is not None
    assert len(detail["actions"]) >= 1
    assert detail["actions"][0]["target_link"] == "https://forms.gle/abc"
    trace = detail["latest_processing"]["agent_trace"]
    assert [t["agent"] for t in trace][:2] == ["Mail Intake Agent", "Triage Agent"]
    assert all("status" in t and "duration_ms" in t for t in trace)

    # 5. Agent Activity (full history)
    history = gmail.get(f"/api/v1/emails/{email_id}/processing").json()
    assert history and history[0]["run_id"].startswith("run_")
    assert "summary" in history[0]

    # 6. Create reminder
    at = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    made = gmail.post(f"/api/v1/emails/{email_id}/reminders", json={"reminder_at": at})
    assert made.status_code == 201
    rid = made.json()["id"]
    assert made.json()["email_id"] == email_id
    assert made.json()["status"] == "PENDING"

    # 7. List reminders (per-email + global)
    assert [r["id"] for r in gmail.get(f"/api/v1/emails/{email_id}/reminders").json()] == [rid]
    glob = gmail.get("/api/v1/reminders").json()
    assert [r["id"] for r in glob] == [rid]
    assert glob[0]["email_id"] == email_id

    # 8. Cancel reminder
    cancelled = gmail.delete(f"/api/v1/emails/{email_id}/reminders/{rid}")
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "CANCELLED"

    # 9. Snooze + un-snooze
    until = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    snz = gmail.patch(f"/api/v1/emails/{email_id}/snooze", json={"snoozed_until": until})
    assert snz.json()["snoozed_until"] is not None
    assert gmail.delete(f"/api/v1/emails/{email_id}/snooze").json()["snoozed_until"] is None

    # 10. Mark an action complete
    ref = detail["actions"][0]["action_ref"]
    done = gmail.patch(f"/api/v1/emails/{email_id}/actions/{ref}/complete")
    assert done.status_code == 200
    assert next(a for a in done.json()["actions"] if a["action_ref"] == ref)["status"] == "COMPLETED"

    # 11. Mark viewed + notifications
    gmail.patch(f"/api/v1/emails/{email_id}/viewed")
    notes = gmail.get("/api/v1/notifications").json()
    assert notes and notes[0]["email_id"] == email_id
    for key in ("id", "notification_type", "severity", "status", "requires_alarm",
                "deadline_id", "reminder_id", "created_at"):
        assert key in notes[0], key

    # 12. Resulting state reflects the interactions
    final = gmail.get(f"/api/v1/emails/{email_id}").json()
    assert final["is_viewed"] is True
    assert final["snoozed_until"] is None
    assert final["notifications"][0]["id"] is not None


# --- targeted checks for the Phase 10.5 additions -----------------------

def test_email_list_row_has_no_full_body(gmail, processed):
    row = gmail.get("/api/v1/emails").json()[0]
    assert "body" not in row  # full body is never persisted / exposed
    assert len(row["snippet"]) <= 240


def test_snooze_delete_is_idempotent(gmail, processed):
    r1 = gmail.delete(f"/api/v1/emails/{processed}/snooze")
    r2 = gmail.delete(f"/api/v1/emails/{processed}/snooze")
    assert r1.status_code == r2.status_code == 200


def test_global_reminders_filter_by_status(gmail, processed):
    at = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    rid = gmail.post(f"/api/v1/emails/{processed}/reminders", json={"reminder_at": at}).json()["id"]
    gmail.delete(f"/api/v1/emails/{processed}/reminders/{rid}")
    assert gmail.get("/api/v1/reminders", params={"status": "PENDING"}).json() == []
    assert [r["id"] for r in gmail.get("/api/v1/reminders", params={"status": "CANCELLED"}).json()] == [rid]


def test_reminder_unknown_email_404():
    at = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    assert client.post("/api/v1/emails/gmail_missing/reminders", json={"reminder_at": at}).status_code == 404


def test_detail_reasoning_summary_matches_latest_run(gmail, processed):
    detail = gmail.get(f"/api/v1/emails/{processed}").json()
    assert detail["reasoning_summary"] == detail["latest_processing"]["summary"]
