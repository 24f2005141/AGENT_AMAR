"""Smoke tests for the minimal FastAPI app."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app
from app.models.email import NormalizedEmail

client = TestClient(app)


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    # `service` reflects APP_NAME from the environment / .env, so don't hard-code it.
    assert resp.json() == {"status": "ok", "service": get_settings().app_name}


def test_root_lists_endpoints():
    resp = client.get("/")
    assert resp.status_code == 200
    assert "/intake/gmail" in resp.json()["endpoints"]


def test_intake_gmail_endpoint_is_off_by_default():
    """The unauthenticated dev helper must not exist unless explicitly enabled
    (production additionally refuses to start with the flag on)."""
    assert client.post("/intake/gmail", json={}).status_code == 404


def test_intake_gmail_endpoint_normalizes_when_explicitly_enabled(sample_gmail_message):
    """Same behaviour as before, now only when the operator opts in."""
    from fastapi import FastAPI

    from app.core.config import Settings
    from app.main import register_debug_routes

    debug_app = FastAPI()
    assert register_debug_routes(debug_app, Settings(enable_debug_intake_endpoint=True))

    resp = TestClient(debug_app).post("/intake/gmail", json=sample_gmail_message)
    assert resp.status_code == 200
    body = resp.json()
    assert body["agent"] == "Mail Intake Agent"
    assert body["status"] == "ok"
    NormalizedEmail.model_validate(body["data"])
    assert body["data"]["email_id"] == "gmail_18f0a1b2c3d4e5f6"


def test_debug_routes_are_not_registered_when_disabled():
    from fastapi import FastAPI

    from app.core.config import Settings
    from app.main import register_debug_routes

    plain = FastAPI()
    assert register_debug_routes(plain, Settings()) is False
    assert TestClient(plain).post("/intake/gmail", json={}).status_code == 404
