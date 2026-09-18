"""Production hardening: config validation, endpoint gating, header/limit
behaviour, and the "no infrastructure detail leaks" rule.

No network, Gmail, LLM, Firebase or production database is touched.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import (
    ProductionConfigError,
    Settings,
    enforce_production_config,
    validate_production_config,
)
from app.main import app
from app.services import llm_concurrency
from app.services.system_status import check_llm

client = TestClient(app)


def _prod(**overrides) -> Settings:
    """A production Settings that is otherwise fully valid."""
    base = dict(
        app_env="production",
        database_url="postgresql+psycopg://u:p@db:5432/sorted",
        google_client_id="cid",
        google_client_secret="csecret",
        api_public_base_url="https://api.example.com",
        data_encryption_key="0" * 64,
    )
    base.update(overrides)
    return Settings(**base)


# --- production configuration validation ------------------------------

def test_development_config_is_never_blocked():
    assert validate_production_config(Settings()) == []


def test_a_complete_production_config_passes():
    assert validate_production_config(_prod()) == []
    enforce_production_config(_prod())  # must not raise


def test_production_rejects_sqlite():
    problems = validate_production_config(_prod(database_url="sqlite:///./x.db"))
    assert any("SQLite" in p for p in problems)


def test_production_rejects_missing_oauth_client():
    problems = validate_production_config(_prod(google_client_id="", google_client_secret=""))
    assert any("GOOGLE_CLIENT_ID" in p for p in problems)


def test_production_rejects_non_https_public_url():
    problems = validate_production_config(_prod(api_public_base_url="http://api.example.com"))
    assert any("HTTPS" in p for p in problems)


def test_production_rejects_missing_encryption_key():
    problems = validate_production_config(_prod(data_encryption_key=""))
    assert any("DATA_ENCRYPTION_KEY" in p for p in problems)


def test_production_rejects_the_debug_intake_endpoint():
    problems = validate_production_config(_prod(enable_debug_intake_endpoint=True))
    assert any("intake" in p.lower() for p in problems)


def test_production_rejects_plaintext_remote_ollama():
    problems = validate_production_config(
        _prod(llm_provider="ollama", llm_model="qwen", ollama_base_url="http://203.0.113.9:11434")
    )
    assert any("OLLAMA_BASE_URL" in p for p in problems)


def test_production_allows_loopback_ollama():
    # Correct when the worker and Ollama share a host / private namespace.
    assert validate_production_config(
        _prod(llm_provider="ollama", llm_model="qwen", ollama_base_url="http://127.0.0.1:11434")
    ) == []


def test_enforce_raises_with_every_problem_listed():
    with pytest.raises(ProductionConfigError) as exc:
        enforce_production_config(Settings(app_env="production"))
    assert "Refusing to start" in str(exc.value)


# --- endpoint surface --------------------------------------------------

def test_debug_intake_endpoint_is_not_registered_by_default():
    assert client.post("/intake/gmail", json={}).status_code == 404


def test_protected_routes_require_authentication(db):
    # The suite installs an autouse `get_current_user` override so the other
    # ~780 tests can run as a seeded user. Lift it here to exercise what an
    # unauthenticated caller from the internet actually gets.
    from app.api.deps import get_current_user

    override = app.dependency_overrides.pop(get_current_user, None)
    try:
        for path in ("/api/v1/emails", "/api/v1/reminders", "/api/v1/notifications"):
            assert client.get(path).status_code == 401, path
    finally:
        if override is not None:
            app.dependency_overrides[get_current_user] = override


def test_health_is_public_and_minimal():
    body = client.get("/health").json()
    assert body["status"] == "ok"
    # No hostnames, versions of dependencies, or connection strings.
    assert set(body) == {"status", "service"}


def test_readiness_reports_coarse_dependency_state_only(db):
    body = client.get("/health/ready").json()
    assert set(body) == {"status", "checks"}
    for value in body["checks"].values():
        assert isinstance(value, str)
        assert "://" not in value  # never a URL
        assert "password" not in value.lower()


def test_security_headers_are_present():
    headers = client.get("/health").headers
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "DENY"
    assert headers["Referrer-Policy"] == "no-referrer"
    assert headers["X-Request-ID"]


def test_request_id_is_echoed_when_supplied():
    resp = client.get("/health", headers={"X-Request-ID": "abc123"})
    assert resp.headers["X-Request-ID"] == "abc123"


# --- no infrastructure leakage ----------------------------------------

def test_public_status_never_leaks_the_private_llm_host():
    settings = Settings(llm_provider="ollama", llm_model="qwen",
                        ollama_base_url="http://10.9.9.9:11434")

    def _refused(url, timeout):  # no real network
        raise ConnectionError("refused")

    status = check_llm(settings, http_get=_refused)
    assert status.status == "offline"
    assert "10.9.9.9" not in (status.detail or "")
    assert "11434" not in (status.detail or "")


def test_status_endpoint_body_has_no_urls(db):
    body = client.get("/api/v1/system/status").json()
    assert "://" not in str(body)


# --- LLM concurrency limiting -----------------------------------------

def test_inference_slot_serialises_and_sheds_load():
    llm_concurrency.configure(1, 0.05)
    try:
        with llm_concurrency.inference_slot():
            # A second concurrent inference must be shed, not queued forever —
            # the caller falls back to the local ML result.
            with pytest.raises(llm_concurrency.LLMBusyError):
                with llm_concurrency.inference_slot():
                    pass
        # Slot released: the next caller succeeds.
        with llm_concurrency.inference_slot():
            pass
    finally:
        llm_concurrency.configure(0, 30.0)


def test_inference_limit_can_be_disabled():
    llm_concurrency.configure(0, 30.0)
    with llm_concurrency.inference_slot():
        with llm_concurrency.inference_slot():
            pass
