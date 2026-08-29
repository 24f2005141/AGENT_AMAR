"""AMAR pipeline + /process endpoint tests (Phase 8, STEP 13, 15)."""

from __future__ import annotations

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
from app.main import app
from app.models.agent_output import AgentOutput
from app.models.decision import FinalDecision
from app.services.amar_pipeline import process_unread
from app.services.gmail_auth_service import GmailAuthService
from app.services.gmail_service import GmailService
from app.services.token_store import InMemoryTokenStore
from tests.fakes import FakeGmailResource, minimal_raw_message


def _orch() -> AMAROrchestrator:
    s = Settings()
    return AMAROrchestrator(
        TriageAgent(settings=s), ActionAgent(settings=s),
        DeadlineAgent(settings=s), PriorityAgent(settings=s), settings=s,
    )


def test_pipeline_runs_full_chain(sample_gmail_message):
    fake = FakeGmailResource(
        unread_ids=["big", "promo"],
        messages={
            "big": sample_gmail_message,
            "promo": minimal_raw_message("promo", subject="50% OFF", body="Buy now, limited offer!"),
        },
    )
    result = process_unread(GmailService(service=fake), MailIntakeAgent(), _orch(), max_results=10)
    assert result["count"] == 2
    assert result["errors"] == []
    for item in result["emails"]:
        FinalDecision.model_validate(item["final_decision"])
        assert "activity_log" in item
        assert "body" not in item  # STEP 13 — no full bodies

    by_id = {e["email_id"]: e for e in result["emails"]}
    big = by_id["gmail_18f0a1b2c3d4e5f6"]["final_decision"]
    assert big["final_category"] == "INTERNSHIP"
    assert big["priority_level"] in {"CRITICAL", "URGENT", "HIGH"}
    assert big["routing"]["folder_label"] == "AMAR/Opportunities"

    promo = by_id["gmail_promo"]["final_decision"]
    assert promo["priority_level"] == "LOW"
    assert promo["routing"]["notify"] is False


def test_pipeline_no_unread():
    result = process_unread(
        GmailService(service=FakeGmailResource(unread_ids=[])), MailIntakeAgent(), _orch()
    )
    assert result["count"] == 0


@pytest.fixture
def client_connected(sample_gmail_message):
    fake = FakeGmailResource(unread_ids=["big"], messages={"big": sample_gmail_message})
    app.dependency_overrides[get_gmail_service] = lambda: GmailService(service=fake)
    app.dependency_overrides[get_amar_orchestrator] = _orch
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def client_disconnected():
    svc = GmailAuthService(Settings(), InMemoryTokenStore())
    app.dependency_overrides[get_auth_service] = lambda: svc
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_process_endpoint(client_connected):
    resp = client_connected.get("/api/v1/gmail/unread/process?max_results=5")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    item = body["emails"][0]
    assert "final_decision" in item and "activity_log" in item
    fd = item["final_decision"]
    assert fd["final_category"] and fd["priority_level"]
    assert set(fd["routing"]) == {"store", "notify", "monitor", "folder_label"}
    assert [t["agent"] for t in fd["agent_trace"]][0] == "Mail Intake Agent"


def test_process_endpoint_requires_auth(client_disconnected):
    assert client_disconnected.get("/api/v1/gmail/unread/process").status_code == 401


def test_all_prior_endpoints_still_work(client_connected):
    for path in (
        "/api/v1/gmail/unread", "/api/v1/gmail/unread/triage",
        "/api/v1/gmail/unread/actions", "/api/v1/gmail/unread/deadlines",
        "/api/v1/gmail/unread/priorities",
    ):
        assert client_connected.get(path).status_code == 200
    assert "/api/v1/gmail/unread/process" in TestClient(app).get("/").json()["endpoints"]
