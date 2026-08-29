"""Priority pipeline + endpoint tests (Phase 7, STEP 13-14)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.agents.action_agent import ActionAgent
from app.agents.deadline_agent import DeadlineAgent
from app.agents.intake_agent import MailIntakeAgent
from app.agents.priority_agent import PriorityAgent
from app.agents.triage_agent import TriageAgent
from app.api.deps import get_auth_service, get_gmail_service, get_priority_agent
from app.core.config import Settings
from app.main import app
from app.models.agent_output import AgentOutput
from app.models.priority import PriorityData
from app.services.gmail_auth_service import GmailAuthService
from app.services.gmail_service import GmailService
from app.services.priority_pipeline import fetch_unread_priorities
from app.services.token_store import InMemoryTokenStore
from tests.fakes import FakeGmailResource, minimal_raw_message


def _agents():
    s = Settings()
    return (
        MailIntakeAgent(),
        TriageAgent(settings=s),
        ActionAgent(settings=s),
        DeadlineAgent(settings=s),
        PriorityAgent(settings=s),
    )


def test_full_pipeline_five_agents(sample_gmail_message):
    fake = FakeGmailResource(
        unread_ids=["big", "promo"],
        messages={
            "big": sample_gmail_message,
            "promo": minimal_raw_message("promo", subject="50% OFF", body="Buy now, limited offer!"),
        },
    )
    intake, triage, action, deadline, priority = _agents()
    result = fetch_unread_priorities(
        GmailService(service=fake), intake, triage, action, deadline, priority, max_results=10
    )
    assert result["count"] == 2
    for item in result["emails"]:
        AgentOutput.model_validate(item["priority_envelope"])
        PriorityData.model_validate(item["priority_envelope"]["data"])
        p = item["priority"]
        assert p["priority_level"] in {"CRITICAL", "URGENT", "HIGH", "MEDIUM", "LOW"}
        assert 0 <= p["priority_score"] <= 100
        assert p["proximity_bucket"] in {"OVERDUE", "WITHIN_1H", "WITHIN_24H", "WITHIN_72H", "LATER", "NONE"}
        assert p["reasoning_summary"]

    by_id = {e["email"]["email_id"]: e for e in result["emails"]}
    # the fixture is an internship application from the placement cell with a deadline
    assert by_id["gmail_18f0a1b2c3d4e5f6"]["priority"]["priority_level"] in {"URGENT", "CRITICAL", "HIGH"}
    assert by_id["gmail_promo"]["priority"]["priority_level"] == "LOW"
    assert by_id["gmail_promo"]["priority"]["notify"] is False


def test_pipeline_no_unread():
    intake, triage, action, deadline, priority = _agents()
    result = fetch_unread_priorities(
        GmailService(service=FakeGmailResource(unread_ids=[])),
        intake, triage, action, deadline, priority,
    )
    assert result["count"] == 0


@pytest.fixture
def client_connected(sample_gmail_message):
    fake = FakeGmailResource(unread_ids=["big"], messages={"big": sample_gmail_message})
    app.dependency_overrides[get_gmail_service] = lambda: GmailService(service=fake)
    app.dependency_overrides[get_priority_agent] = lambda: PriorityAgent(settings=Settings())
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def client_disconnected():
    svc = GmailAuthService(Settings(), InMemoryTokenStore())
    app.dependency_overrides[get_auth_service] = lambda: svc
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_priorities_endpoint(client_connected):
    resp = client_connected.get("/api/v1/gmail/unread/priorities?max_results=5")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    item = body["emails"][0]
    assert item["priority_envelope"]["agent"] == "Priority Agent"
    assert "priority_level" in item["priority"]
    assert "score_breakdown" in item["priority"]


def test_priorities_endpoint_requires_auth(client_disconnected):
    assert client_disconnected.get("/api/v1/gmail/unread/priorities").status_code == 401


def test_all_prior_endpoints_still_work(client_connected):
    for path in ("/api/v1/gmail/unread", "/api/v1/gmail/unread/triage",
                 "/api/v1/gmail/unread/actions", "/api/v1/gmail/unread/deadlines"):
        assert client_connected.get(path).status_code == 200
    assert "/api/v1/gmail/unread/priorities" in TestClient(app).get("/").json()["endpoints"]
