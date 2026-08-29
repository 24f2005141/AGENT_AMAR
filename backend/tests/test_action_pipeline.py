"""Action pipeline + endpoint tests (Phase 5, STEP 12)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.agents.action_agent import ActionAgent
from app.agents.intake_agent import MailIntakeAgent
from app.agents.triage_agent import TriageAgent
from app.api.deps import get_action_agent, get_auth_service, get_gmail_service
from app.core.config import Settings
from app.main import app
from app.models.action import ActionData
from app.models.agent_output import AgentOutput
from app.models.email import NormalizedEmail
from app.services.action_pipeline import fetch_unread_actions
from app.services.gmail_auth_service import GmailAuthService
from app.services.gmail_service import GmailService
from app.services.token_store import InMemoryTokenStore
from tests.fakes import FakeGmailResource, minimal_raw_message


def _agents():
    return MailIntakeAgent(), TriageAgent(settings=Settings()), ActionAgent(settings=Settings())


def test_pipeline_intake_triage_action(sample_gmail_message):
    fake = FakeGmailResource(
        unread_ids=["big", "promo"],
        messages={
            "big": sample_gmail_message,
            "promo": minimal_raw_message("promo", subject="50% OFF sale", body="Buy now, limited discount!"),
        },
    )
    intake, triage, action = _agents()
    result = fetch_unread_actions(GmailService(service=fake), intake, triage, action, max_results=10)

    assert result["count"] == 2
    assert result["errors"] == []
    for item in result["emails"]:
        NormalizedEmail.model_validate(item["email"])
        AgentOutput.model_validate(item["action_envelope"])
        ActionData.model_validate(item["action_envelope"]["data"])

    by_id = {e["email"]["email_id"]: e for e in result["emails"]}
    big = by_id["gmail_18f0a1b2c3d4e5f6"]
    assert big["triage"]["category"] == "INTERNSHIP"
    assert big["action"]["action_required"] is True
    assert "FORM_SUBMISSION" in [a["action_type"] for a in big["action"]["actions"]]
    assert big["action"]["detection_method"] == "deterministic"

    promo = by_id["gmail_promo"]
    assert promo["action"]["action_required"] is False


def test_pipeline_handles_no_unread():
    intake, triage, action = _agents()
    result = fetch_unread_actions(
        GmailService(service=FakeGmailResource(unread_ids=[])), intake, triage, action
    )
    assert result["count"] == 0


# --- HTTP ------------------------------------------------------------

@pytest.fixture
def client_connected(sample_gmail_message):
    fake = FakeGmailResource(
        unread_ids=["big"], messages={"big": sample_gmail_message}, email="me@gmail.com"
    )
    app.dependency_overrides[get_gmail_service] = lambda: GmailService(service=fake)
    app.dependency_overrides[get_action_agent] = lambda: ActionAgent(settings=Settings())
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def client_disconnected():
    svc = GmailAuthService(Settings(), InMemoryTokenStore())
    app.dependency_overrides[get_auth_service] = lambda: svc
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_actions_endpoint_runs_full_pipeline(client_connected):
    resp = client_connected.get("/api/v1/gmail/unread/actions?max_results=5")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    item = body["emails"][0]
    assert item["triage"]["category"] == "INTERNSHIP"
    assert item["action"]["action_required"] is True
    assert item["action_envelope"]["agent"] == "Action Agent"
    for a in item["action"]["actions"]:
        assert "confidence" in a and "evidence" in a


def test_actions_endpoint_requires_auth(client_disconnected):
    resp = client_disconnected.get("/api/v1/gmail/unread/actions")
    assert resp.status_code == 401
    assert resp.json()["error"] == "GmailNotConnectedError"


def test_existing_endpoints_still_work(client_connected):
    assert client_connected.get("/api/v1/gmail/unread").status_code == 200
    assert client_connected.get("/api/v1/gmail/unread/triage").status_code == 200
    r = client_connected.get("/")
    assert "/api/v1/gmail/unread/actions" in r.json()["endpoints"]
