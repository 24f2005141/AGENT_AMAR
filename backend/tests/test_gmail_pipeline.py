"""End-to-end pipeline + API tests (STEP 8.2, 8.7).

    mocked Gmail API  ->  raw payload  ->  MailIntakeAgent  ->  NormalizedEmail
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.agents.intake_agent import MailIntakeAgent
from app.api.deps import get_auth_service, get_gmail_service
from app.core.config import Settings
from app.main import app
from app.models.email import NormalizedEmail
from app.services.gmail_auth_service import GmailAuthService
from app.services.gmail_pipeline import fetch_unread_normalized
from app.services.gmail_service import GmailService
from app.services.token_store import InMemoryTokenStore
from tests.fakes import FakeGmailResource, minimal_raw_message


# --- pipeline function (no HTTP) ----------------------------------------

def test_pipeline_normalizes_each_unread_message(sample_gmail_message):
    fake = FakeGmailResource(
        unread_ids=["big", "m2"],
        messages={
            "big": sample_gmail_message,
            "m2": minimal_raw_message("m2", subject="Second"),
        },
    )
    result = fetch_unread_normalized(
        GmailService(service=fake), MailIntakeAgent(), max_results=10
    )

    assert result["count"] == 2
    assert result["errors"] == []
    for item in result["emails"]:
        NormalizedEmail.model_validate(item["email"])  # still a valid contract
    ids = [e["email"]["email_id"] for e in result["emails"]]
    assert ids == ["gmail_18f0a1b2c3d4e5f6", "gmail_m2"]
    assert result["emails"][0]["summary"]["subject"].startswith("Software Engineering")


def test_pipeline_handles_no_unread():
    result = fetch_unread_normalized(
        GmailService(service=FakeGmailResource(unread_ids=[])), MailIntakeAgent()
    )
    assert result == {
        "count": 0,
        "max_results": 10,
        "unread_ids_seen": 0,
        "emails": [],
        "errors": [],
    }


def test_pipeline_reports_per_message_failure_without_aborting(sample_gmail_message):
    fake = FakeGmailResource(
        unread_ids=["ok", "missing"],
        messages={"ok": sample_gmail_message},  # "missing" is absent -> 404
    )
    result = fetch_unread_normalized(GmailService(service=fake), MailIntakeAgent())
    assert result["count"] == 1
    assert len(result["errors"]) == 1
    assert result["errors"][0]["message_id"] == "missing"


# --- HTTP endpoints -----------------------------------------------------

@pytest.fixture
def client_disconnected():
    """App whose auth service has no stored credentials."""
    svc = GmailAuthService(Settings(), InMemoryTokenStore())
    app.dependency_overrides[get_auth_service] = lambda: svc
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def client_connected(sample_gmail_message):
    """App wired to a fake Gmail resource (as if OAuth had succeeded)."""
    fake = FakeGmailResource(
        unread_ids=["big"], messages={"big": sample_gmail_message}, email="me@gmail.com"
    )
    app.dependency_overrides[get_gmail_service] = lambda: GmailService(service=fake)
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_status_when_not_connected(client_disconnected):
    resp = client_disconnected.get("/api/v1/auth/google/status")
    assert resp.status_code == 200
    assert resp.json() == {
        "connected": False,
        "provider": "gmail",
        "account_email": None,
        "scopes": [],
    }


def test_unread_requires_authentication(client_disconnected):
    resp = client_disconnected.get("/api/v1/gmail/unread")
    assert resp.status_code == 401
    body = resp.json()
    assert body["error"] == "GmailNotConnectedError"
    assert body["provider"] == "gmail"
    # no secret material leaked
    blob = resp.text.lower()
    for leak in ("token", "secret", "client_id", "refresh"):
        assert leak not in blob


def test_unread_endpoint_runs_pipeline(client_connected):
    resp = client_connected.get("/api/v1/gmail/unread?max_results=5")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    email = body["emails"][0]["email"]
    NormalizedEmail.model_validate(email)
    assert email["email_id"] == "gmail_18f0a1b2c3d4e5f6"
    assert body["emails"][0]["intake"]["status"] == "ok"


def test_login_without_config_returns_clean_error(client_disconnected):
    resp = client_disconnected.get(
        "/api/v1/auth/google/login", follow_redirects=False
    )
    assert resp.status_code == 503
    assert resp.json()["error"] == "OAuthConfigError"
