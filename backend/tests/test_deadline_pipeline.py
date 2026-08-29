"""Deadline pipeline + endpoint tests (Phase 6, STEP 14)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.agents.action_agent import ActionAgent
from app.agents.deadline_agent import DeadlineAgent
from app.agents.intake_agent import MailIntakeAgent
from app.agents.triage_agent import TriageAgent
from app.api.deps import get_auth_service, get_deadline_agent, get_gmail_service
from app.core.config import Settings
from app.main import app
from app.models.agent_output import AgentOutput
from app.models.deadline import DeadlineData
from app.models.email import NormalizedEmail
from app.services.deadline_pipeline import fetch_unread_deadlines
from app.services.gmail_auth_service import GmailAuthService
from app.services.gmail_service import GmailService
from app.services.token_store import InMemoryTokenStore
from tests.fakes import FakeGmailResource, minimal_raw_message


def _agents():
    s = Settings()
    return (
        MailIntakeAgent(),
        TriageAgent(settings=s),
        ActionAgent(settings=s),
        DeadlineAgent(settings=s),
    )


def test_pipeline_full_chain(sample_gmail_message):
    fake = FakeGmailResource(
        unread_ids=["big", "plain"],
        messages={
            "big": sample_gmail_message,
            "plain": minimal_raw_message("plain", subject="Weekly notes", body="Notes attached for reference."),
        },
    )
    intake, triage, action, deadline = _agents()
    result = fetch_unread_deadlines(
        GmailService(service=fake), intake, triage, action, deadline, max_results=10
    )
    assert result["count"] == 2
    assert result["errors"] == []
    for item in result["emails"]:
        NormalizedEmail.model_validate(item["email"])
        AgentOutput.model_validate(item["deadline_envelope"])
        DeadlineData.model_validate(item["deadline_envelope"]["data"])
        assert "has_deadline" in item["deadline"]
        assert "reference_time_used" in item["deadline"]

    big = next(e for e in result["emails"] if e["email"]["email_id"] == "gmail_18f0a1b2c3d4e5f6")
    # the fixture body says "deadline is 5 September 2026, 6:00 PM IST"
    assert big["deadline"]["has_deadline"] is True
    assert big["deadline"]["primary"]["normalized_deadline"].startswith("2026-09-05")

    plain = next(e for e in result["emails"] if e["email"]["email_id"] == "gmail_plain")
    assert plain["deadline"]["has_deadline"] is False


def test_pipeline_no_unread():
    intake, triage, action, deadline = _agents()
    result = fetch_unread_deadlines(
        GmailService(service=FakeGmailResource(unread_ids=[])), intake, triage, action, deadline
    )
    assert result["count"] == 0


@pytest.fixture
def client_connected(sample_gmail_message):
    fake = FakeGmailResource(unread_ids=["big"], messages={"big": sample_gmail_message})
    app.dependency_overrides[get_gmail_service] = lambda: GmailService(service=fake)
    app.dependency_overrides[get_deadline_agent] = lambda: DeadlineAgent(settings=Settings())
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def client_disconnected():
    svc = GmailAuthService(Settings(), InMemoryTokenStore())
    app.dependency_overrides[get_auth_service] = lambda: svc
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_deadlines_endpoint(client_connected):
    resp = client_connected.get("/api/v1/gmail/unread/deadlines?max_results=5")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    item = body["emails"][0]
    assert item["deadline"]["has_deadline"] is True
    assert item["deadline_envelope"]["agent"] == "Deadline Agent"
    assert item["triage"]["category"] == "INTERNSHIP"


def test_deadlines_endpoint_requires_auth(client_disconnected):
    resp = client_disconnected.get("/api/v1/gmail/unread/deadlines")
    assert resp.status_code == 401


def test_all_prior_endpoints_still_work(client_connected):
    assert client_connected.get("/api/v1/gmail/unread").status_code == 200
    assert client_connected.get("/api/v1/gmail/unread/triage").status_code == 200
    assert client_connected.get("/api/v1/gmail/unread/actions").status_code == 200
    assert "/api/v1/gmail/unread/deadlines" in TestClient(app).get("/").json()["endpoints"]
