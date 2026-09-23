from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import routes_system
from app.core.config import Settings
from app.main import app
from app.services.decision_service import get_shared_decision_client
from app.services.ai_mode_service import AiModeStore, ensure_mode_available, mode_options


def test_ai_mode_store_persists_selection(tmp_path: Path):
    path = tmp_path / "ai_mode.json"
    first = AiModeStore(path, default="conventional")
    assert first.get() == "conventional"
    assert first.set("laya") == "laya"
    assert AiModeStore(path).get() == "laya"


def test_ai_mode_store_rejects_unknown_mode(tmp_path: Path):
    with pytest.raises(ValueError):
        AiModeStore(tmp_path / "mode.json").set("unknown")


def test_mode_options_report_configured_backends():
    settings = Settings(
        llm_provider="gemini",
        llm_api_key="gemini-key",
        llm_fallback_provider="groq",
        llm_fallback_api_key="groq-key",
        openrouter_api_key="openrouter-key",
    )
    options = {item["id"]: item for item in mode_options(settings)}
    assert options["conventional"]["available"] is True
    assert options["jev"]["available"] is True
    assert options["laya"]["available"] is True
    ensure_mode_available("jev", settings)


def test_decision_client_is_reused_across_requests():
    settings = Settings(
        decision_provider="jev",
        openrouter_api_key="test-key-for-cache",
        jev_model="typesafe/jev-1.13",
    )
    assert get_shared_decision_client(settings) is get_shared_decision_client(settings)


def test_ai_mode_route_persists_authenticated_selection(monkeypatch, tmp_path: Path):
    store = AiModeStore(tmp_path / "mode.json")
    monkeypatch.setattr(routes_system, "get_ai_mode_store", lambda: store)
    monkeypatch.setattr(routes_system, "ensure_mode_available", lambda mode, settings: None)
    client = TestClient(app)
    assert client.get("/api/v1/system/ai-mode").json()["selected"] == "conventional"

    response = client.put("/api/v1/system/ai-mode", json={"mode": "jev"})
    assert response.status_code == 200
    assert response.json()["selected"] == "jev"
    assert client.get("/api/v1/system/ai-mode").json()["selected"] == "jev"


def test_ai_mode_route_rejects_unavailable_selection(monkeypatch, tmp_path: Path):
    store = AiModeStore(tmp_path / "mode.json")
    monkeypatch.setattr(routes_system, "get_ai_mode_store", lambda: store)

    def unavailable(mode, settings):
        raise RuntimeError("not configured")

    monkeypatch.setattr(routes_system, "ensure_mode_available", unavailable)
    response = TestClient(app).put("/api/v1/system/ai-mode", json={"mode": "laya"})
    assert response.status_code == 409
    assert store.get() == "conventional"
