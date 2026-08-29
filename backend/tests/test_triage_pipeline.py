"""Triage pipeline + endpoint tests (STEP 8, STEP 9 flow)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.agents.intake_agent import MailIntakeAgent
from app.agents.triage_agent import TriageAgent
from app.api.deps import get_auth_service, get_gmail_service, get_triage_agent
from app.core.config import Settings
from app.main import app
from app.models.agent_output import AgentOutput
from app.models.email import NormalizedEmail
from app.models.triage import TriageCategory, TriageData
from app.services.gmail_auth_service import GmailAuthService
from app.services.gmail_service import GmailService
from app.services.triage_pipeline import fetch_unread_triaged
from app.services.token_store import InMemoryTokenStore
from tests.fakes import FakeGmailResource, minimal_raw_message


# --- pipeline (no HTTP) -----------------------------------------------

def test_pipeline_normalizes_then_classifies(sample_gmail_message):
    fake = FakeGmailResource(
        unread_ids=["big", "m2"],
        messages={
            "big": sample_gmail_message,
            "m2": minimal_raw_message("m2", subject="FLAT 50% OFF sale", body="Buy now, limited time discount coupon."),
        },
    )
    result = fetch_unread_triaged(
        GmailService(service=fake), MailIntakeAgent(), TriageAgent(settings=Settings()), max_results=10
    )

    assert result["count"] == 2
    assert result["errors"] == []
    for item in result["emails"]:
        NormalizedEmail.model_validate(item["email"])
        AgentOutput.model_validate(item["triage_envelope"])
        TriageData.model_validate(item["triage_envelope"]["data"])
        assert TriageCategory(item["triage"]["category"])
        assert item["triage"]["classification_method"] == "deterministic"

    by_id = {e["email"]["email_id"]: e for e in result["emails"]}
    assert by_id["gmail_18f0a1b2c3d4e5f6"]["triage"]["category"] == "INTERNSHIP"
    assert by_id["gmail_m2"]["triage"]["category"] == "PROMOTIONAL"


def test_pipeline_handles_no_unread():
    result = fetch_unread_triaged(
        GmailService(service=FakeGmailResource(unread_ids=[])),
        MailIntakeAgent(),
        TriageAgent(settings=Settings()),
    )
    assert result["count"] == 0
    assert result["emails"] == []


# --- HTTP endpoint --------------------------------------------------

@pytest.fixture
def client_connected(sample_gmail_message):
    fake = FakeGmailResource(
        unread_ids=["big"], messages={"big": sample_gmail_message}, email="me@gmail.com"
    )
    app.dependency_overrides[get_gmail_service] = lambda: GmailService(service=fake)
    app.dependency_overrides[get_triage_agent] = lambda: TriageAgent(settings=Settings())
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def client_disconnected():
    svc = GmailAuthService(Settings(), InMemoryTokenStore())
    app.dependency_overrides[get_auth_service] = lambda: svc
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_triage_endpoint_runs_full_flow(client_connected):
    resp = client_connected.get("/api/v1/gmail/unread/triage?max_results=5")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    item = body["emails"][0]
    NormalizedEmail.model_validate(item["email"])
    assert item["triage"]["category"] == "INTERNSHIP"
    assert item["triage"]["classification_method"] == "deterministic"
    assert "confidence" in item["triage"]
    assert item["triage_envelope"]["agent"] == "Triage Agent"


def test_triage_endpoint_requires_auth(client_disconnected):
    resp = client_disconnected.get("/api/v1/gmail/unread/triage")
    assert resp.status_code == 401
    assert resp.json()["error"] == "GmailNotConnectedError"


def test_plain_unread_endpoint_still_works(client_connected):
    resp = client_connected.get("/api/v1/gmail/unread")
    assert resp.status_code == 200
    assert resp.json()["count"] == 1
    # the plain endpoint must NOT classify
    assert "triage" not in resp.json()["emails"][0]


def test_root_lists_triage_endpoint():
    resp = TestClient(app).get("/")
    assert "/api/v1/gmail/unread/triage" in resp.json()["endpoints"]
