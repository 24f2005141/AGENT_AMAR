"""GET /api/v1/system/status + app.services.system_status.check_llm.

No real network: the Ollama probe uses an injected / monkeypatched http getter.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import app
from app.services import system_status as ss
from app.services.system_status import LlmStatus, check_llm


class _FakeResponse:
    def __init__(self, status_code: int, body: dict | None = None) -> None:
        self.status_code = status_code
        self._body = body if body is not None else {}

    def json(self) -> dict:
        return self._body


def _ok_tags(*models: str):
    def _get(url: str, *, timeout: float):
        assert url.endswith("/api/tags")  # cheap list, never /api/generate
        return _FakeResponse(200, {"models": [{"name": m} for m in models]})

    return _get


# --- check_llm: provider matrix ---------------------------------------

def test_none_provider_is_unconfigured():
    s = check_llm(Settings(llm_provider="none"))
    assert s.status == "unconfigured"
    assert s.provider == "none"
    assert s.model is None


def test_unknown_provider_is_unknown():
    s = check_llm(Settings(llm_provider="mistral-cloud-xyz"))
    assert s.status == "unknown"
    assert s.provider == "mistral-cloud-xyz"


def test_ollama_online_when_model_present():
    s = check_llm(
        Settings(llm_provider="ollama", llm_model="qwen2.5:3b",
                 ollama_base_url="http://192.168.1.11:11434"),
        http_get=_ok_tags("qwen2.5:3b", "llama3.1:8b"),
    )
    assert s.status == "online"
    assert s.provider == "ollama"
    assert s.model == "qwen2.5:3b"


def test_ollama_offline_when_unreachable():
    def _boom(url: str, *, timeout: float):
        raise ConnectionError("connection refused")

    s = check_llm(Settings(llm_provider="ollama", llm_model="qwen2.5:3b"), http_get=_boom)
    assert s.status == "offline"
    assert s.provider == "ollama"


def test_ollama_offline_when_model_not_pulled():
    s = check_llm(
        Settings(llm_provider="ollama", llm_model="qwen2.5:3b"),
        http_get=_ok_tags("llama3.1:8b"),
    )
    assert s.status == "offline"
    assert "not pulled" in (s.detail or "")


def test_ollama_offline_on_http_error():
    s = check_llm(
        Settings(llm_provider="ollama", llm_model="x"),
        http_get=lambda url, *, timeout: _FakeResponse(500),
    )
    assert s.status == "offline"
    assert "HTTP 500" in (s.detail or "")


@pytest.mark.parametrize("provider", ["openai", "anthropic", "gemini", "groq"])
def test_api_provider_online_when_key_present(provider):
    s = check_llm(Settings(llm_provider=provider, llm_api_key="sk-test-do-not-log"))
    assert s.status == "online"
    assert s.provider == provider
    assert s.model  # normalised default model name
    assert "sk-test-do-not-log" not in (s.detail or "")


@pytest.mark.parametrize("provider", ["openai", "anthropic", "gemini", "groq"])
def test_api_provider_unconfigured_without_key(provider):
    s = check_llm(Settings(llm_provider=provider, llm_api_key=""))
    assert s.status == "unconfigured"


def test_check_llm_never_raises_on_weird_getter():
    def _weird(url: str, *, timeout: float):
        raise RuntimeError("totally unexpected")

    s = check_llm(Settings(llm_provider="ollama"), http_get=_weird)
    assert s.status == "offline"


def test_check_llm_does_not_run_inference(monkeypatch):
    """Guard: the status check must never call LLMClient.complete_json."""
    from app.services import llm_service

    called = {"n": 0}
    orig = llm_service.NullLLMClient.complete_json

    def _tracked(self, *a, **k):  # pragma: no cover - should never run
        called["n"] += 1
        return orig(self, *a, **k)

    monkeypatch.setattr(llm_service.NullLLMClient, "complete_json", _tracked)
    check_llm(Settings(llm_provider="openai", llm_api_key="sk-x"))
    check_llm(Settings(llm_provider="none"))
    assert called["n"] == 0


def test_gemini_primary_groq_fallback_status():
    s = check_llm(
        Settings(
            llm_provider="gemini",
            llm_api_key="gemini-key",
            llm_fallback_provider="groq",
            llm_fallback_api_key="groq-key",
        )
    )
    assert s.status == "online"
    assert s.provider == "gemini->groq"
    assert s.model == "gemini-3.5-flash-lite->openai/gpt-oss-20b"
    assert s.detail == "primary online; fallback online"


def test_status_is_online_when_only_fallback_is_configured():
    s = check_llm(
        Settings(
            llm_provider="gemini",
            llm_fallback_provider="groq",
            llm_fallback_api_key="groq-key",
        )
    )
    assert s.status == "online"
    assert s.detail == "primary unconfigured; fallback online"


# --- endpoint --------------------------------------------------------

def test_endpoint_reports_backend_online_and_llm_block():
    body = TestClient(app).get("/api/v1/system/status").json()
    assert body["backend"]["status"] == "online"
    assert set(body["llm"]) == {"status", "provider", "model", "detail"}
    # conftest forces LLM_PROVIDER=none
    assert body["llm"]["status"] == "unconfigured"
    assert body["llm"]["provider"] == "none"


def test_endpoint_requires_no_auth():
    r = TestClient(app).get("/api/v1/system/status")
    assert r.status_code == 200


def test_endpoint_never_leaks_secrets(monkeypatch):
    from app.api import routes_system

    def _fake_settings():
        return Settings(
            llm_provider="openai",
            llm_api_key="sk-SUPER-SECRET-VALUE-1234",
            llm_model="gpt-4o-mini",
        )

    app.dependency_overrides[routes_system.get_settings] = _fake_settings
    try:
        r = TestClient(app).get("/api/v1/system/status")
    finally:
        app.dependency_overrides.pop(routes_system.get_settings, None)

    assert r.status_code == 200
    assert "sk-SUPER-SECRET-VALUE-1234" not in r.text
    assert r.json()["llm"]["status"] == "online"
    assert r.json()["llm"]["model"] == "gpt-4o-mini"


def test_endpoint_ollama_shape(monkeypatch):
    from app.api import routes_system

    monkeypatch.setattr(
        routes_system, "check_llm",
        lambda settings: LlmStatus("online", "ollama", "qwen2.5:3b", "1 model(s) available"),
    )
    body = TestClient(app).get("/api/v1/system/status").json()
    assert body == {
        "backend": {"status": "online"},
        "llm": {
            "status": "online",
            "provider": "ollama",
            "model": "qwen2.5:3b",
            "detail": "1 model(s) available",
        },
    }


def test_endpoint_makes_no_real_network_call(monkeypatch):
    """With LLM_PROVIDER=none the endpoint must not touch httpx at all."""
    import app.services.system_status as mod

    def _forbidden(*a, **k):  # pragma: no cover
        raise AssertionError("system status made a real HTTP call")

    monkeypatch.setattr(mod.httpx, "get", _forbidden)
    r = TestClient(app).get("/api/v1/system/status")
    assert r.status_code == 200
