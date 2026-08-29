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


def test_intake_gmail_endpoint(sample_gmail_message):
    resp = client.post("/intake/gmail", json=sample_gmail_message)
    assert resp.status_code == 200
    body = resp.json()
    assert body["agent"] == "Mail Intake Agent"
    assert body["status"] == "ok"
    NormalizedEmail.model_validate(body["data"])
    assert body["data"]["email_id"] == "gmail_18f0a1b2c3d4e5f6"
