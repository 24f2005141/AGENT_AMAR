"""State API endpoints + /process persistence integration (STEP 18 items 33-50)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.agents.action_agent import ActionAgent
from app.agents.amar_orchestrator import AMAROrchestrator
from app.agents.deadline_agent import DeadlineAgent
from app.agents.intake_agent import MailIntakeAgent
from app.agents.priority_agent import PriorityAgent
from app.agents.triage_agent import TriageAgent
from app.api.deps import get_amar_orchestrator, get_auth_service, get_gmail_service
from app.core.config import Settings
from app.db import session as db_session
from app.main import app
from app.services.gmail_auth_service import GmailAuthService
from app.services.gmail_service import GmailService
from app.services.persistence_service import PersistenceService
from app.services.token_store import InMemoryTokenStore
from tests.fakes import FakeGmailResource
from tests.persistence_helpers import decision_for, internship_email, promo_email

client = TestClient(app)


def _orch() -> AMAROrchestrator:
    s = Settings()
    return AMAROrchestrator(
        TriageAgent(settings=s), ActionAgent(settings=s),
        DeadlineAgent(settings=s), PriorityAgent(settings=s), settings=s,
    )


@pytest.fixture
def seeded():
    """Persist an internship email + a promo email straight into the DB."""
    out = {}
    with db_session.db_session() as db:
        svc = PersistenceService(db)
        intern = internship_email()
        promo = promo_email()
        out["intern"] = svc.persist_decision(intern, decision_for(intern)).email_id
        out["promo"] = svc.persist_decision(promo, decision_for(promo)).email_id
    return out


# --- list / filter ---------------------------------------------------------

def test_list_emails(seeded):
    r = client.get("/api/v1/emails")
    assert r.status_code == 200
    assert len(r.json()) == 2


def test_filter_by_priority(seeded):
    r = client.get("/api/v1/emails", params={"priority": "LOW"})
    assert [e["email_id"] for e in r.json()] == [seeded["promo"]]


def test_filter_by_category(seeded):
    r = client.get("/api/v1/emails", params={"category": "INTERNSHIP"})
    assert [e["email_id"] for e in r.json()] == [seeded["intern"]]


def test_filter_actionable(seeded):
    r = client.get("/api/v1/emails", params={"action_required": True})
    assert [e["email_id"] for e in r.json()] == [seeded["intern"]]


def test_filter_human_review(seeded):
    r = client.get("/api/v1/emails/human-review")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


# --- detail --------------------------------------------------------------

def test_get_email_detail(seeded):
    r = client.get(f"/api/v1/emails/{seeded['intern']}")
    assert r.status_code == 200
    body = r.json()
    assert body["email_id"] == seeded["intern"]
    assert len(body["actions"]) >= 1
    assert len(body["deadlines"]) >= 1
    assert body["latest_processing"]["run_id"].startswith("run_")
    assert body["processing_run_count"] == 1


def test_get_email_404():
    assert client.get("/api/v1/emails/gmail_nope").status_code == 404


# --- mutations ---------------------------------------------------------

def test_mark_viewed_endpoint(seeded):
    r = client.patch(f"/api/v1/emails/{seeded['intern']}/viewed")
    assert r.status_code == 200
    assert r.json()["is_viewed"] is True
    # still viewed after re-fetch
    assert client.get(f"/api/v1/emails/{seeded['intern']}").json()["is_viewed"] is True


def test_complete_action_endpoint(seeded):
    detail = client.get(f"/api/v1/emails/{seeded['intern']}").json()
    ref = detail["actions"][0]["action_ref"]
    r = client.patch(f"/api/v1/emails/{seeded['intern']}/actions/{ref}/complete")
    assert r.status_code == 200
    action = next(a for a in r.json()["actions"] if a["action_ref"] == ref)
    assert action["status"] == "COMPLETED"


def test_complete_action_404(seeded):
    assert client.patch(
        f"/api/v1/emails/{seeded['intern']}/actions/act_999/complete"
    ).status_code == 404


def test_snooze_endpoint(seeded):
    until = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    r = client.patch(f"/api/v1/emails/{seeded['intern']}/snooze", json={"snoozed_until": until})
    assert r.status_code == 200
    assert r.json()["snoozed_until"] is not None


# --- cross-cutting lists ---------------------------------------------

def test_pending_actions_endpoint(seeded):
    r = client.get("/api/v1/actions/pending")
    assert r.status_code == 200
    body = r.json()
    assert body and all(a["status"] == "PENDING" for a in body)
    assert "email_id" in body[0] and "subject" in body[0]


def test_upcoming_deadlines_endpoint(seeded):
    r = client.get("/api/v1/deadlines/upcoming", params={"within_hours": 24 * 60})
    assert r.status_code == 200
    for d in r.json():
        assert d["deadline_datetime"] is not None
        assert d["is_past"] is False


# --- /process persistence integration -----------------------------

@pytest.fixture
def gmail_client(sample_gmail_message):
    fake = FakeGmailResource(unread_ids=["big"], messages={"big": sample_gmail_message})
    app.dependency_overrides[get_gmail_service] = lambda: GmailService(service=fake)
    app.dependency_overrides[get_amar_orchestrator] = _orch
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def gmail_disconnected():
    svc = GmailAuthService(Settings(), InMemoryTokenStore())
    app.dependency_overrides[get_auth_service] = lambda: svc
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_process_persists_and_is_idempotent(gmail_client):
    r1 = gmail_client.get("/api/v1/gmail/unread/process?max_results=5")
    assert r1.status_code == 200
    item = r1.json()["emails"][0]
    assert item["persisted"]["email_id"] == "gmail_18f0a1b2c3d4e5f6"
    assert item["persisted"]["processing_run_count"] == 1

    r2 = gmail_client.get("/api/v1/gmail/unread/process?max_results=5")
    item2 = r2.json()["emails"][0]
    assert item2["persisted"]["processing_run_count"] == 2

    listed = gmail_client.get("/api/v1/emails").json()
    assert len(listed) == 1  # exactly one email record after two passes


def test_process_persist_false_skips_db(gmail_client):
    gmail_client.get("/api/v1/gmail/unread/process?persist=false")
    assert client.get("/api/v1/emails").json() == []


def test_process_user_state_survives_reprocess(gmail_client):
    gmail_client.get("/api/v1/gmail/unread/process")
    email_id = client.get("/api/v1/emails").json()[0]["email_id"]
    client.patch(f"/api/v1/emails/{email_id}/viewed")
    gmail_client.get("/api/v1/gmail/unread/process")  # reprocess
    detail = client.get(f"/api/v1/emails/{email_id}").json()
    assert detail["is_viewed"] is True
    assert detail["processing_run_count"] == 2


def test_process_requires_auth(gmail_disconnected):
    assert gmail_disconnected.get("/api/v1/gmail/unread/process").status_code == 401


def test_all_prior_endpoints_still_work(gmail_client):
    for p in ("/api/v1/gmail/unread", "/api/v1/gmail/unread/triage",
              "/api/v1/gmail/unread/actions", "/api/v1/gmail/unread/deadlines",
              "/api/v1/gmail/unread/priorities"):
        assert gmail_client.get(p).status_code == 200
    assert client.get("/health").status_code == 200
    assert "/api/v1/emails" in client.get("/").json()["endpoints"]
