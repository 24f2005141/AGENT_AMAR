"""Tests for the LLM abstraction (STEP 7 + multi-provider update).

No real API calls, no real Ollama server, no network — every provider is
exercised with a fake SDK client / mocked ``httpx.post``.
"""

from __future__ import annotations

import sys

import pytest

from app.core.config import Settings
from app.services import llm_service
from app.services.llm_service import (
    AnthropicLLMClient,
    GeminiLLMClient,
    LLMResponseError,
    LLMUnavailableError,
    NullLLMClient,
    OllamaLLMClient,
    OpenAILLMClient,
    _extract_json_object,
    build_llm_client,
)


def test_null_client_is_unavailable():
    client = NullLLMClient()
    assert client.is_available is False
    with pytest.raises(LLMUnavailableError):
        client.complete_json("s", "u")


def test_build_llm_client_defaults_to_null():
    assert isinstance(build_llm_client(Settings()), NullLLMClient)


def test_build_llm_client_anthropic_without_key_is_unavailable():
    client = build_llm_client(Settings(llm_provider="anthropic"))
    assert client.provider == "anthropic"
    assert client.is_available is False  # no API key


def test_build_llm_client_openai_selected():
    client = build_llm_client(Settings(llm_provider="openai", llm_api_key="sk-test", llm_model="gpt-4o-mini"))
    assert client.provider == "openai"
    assert client.model == "gpt-4o-mini"
    assert client.is_available is True  # has a key (no call is made here)


def test_unknown_provider_falls_back_to_null():
    assert isinstance(build_llm_client(Settings(llm_provider="banana")), NullLLMClient)


@pytest.mark.parametrize(
    "text,expected",
    [
        ('{"category": "SPAM"}', {"category": "SPAM"}),
        ('  {"a": 1}  ', {"a": 1}),
        ('Here is the answer:\n{"category": "EXAM", "confidence": 0.9}\nThanks!',
         {"category": "EXAM", "confidence": 0.9}),
    ],
)
def test_extract_json_object_ok(text, expected):
    assert _extract_json_object(text) == expected


@pytest.mark.parametrize("text", ["", "not json at all", "[1, 2, 3]", "{broken"])
def test_extract_json_object_rejects_bad_input(text):
    with pytest.raises(LLMResponseError):
        _extract_json_object(text)


# ============================================================================
# Provider factory selection (all 5 + unknown)
# ============================================================================

@pytest.mark.parametrize(
    "provider,cls,kwargs",
    [
        ("none", NullLLMClient, {}),
        ("openai", OpenAILLMClient, {"llm_api_key": "sk-x", "llm_model": "gpt-4o-mini"}),
        ("anthropic", AnthropicLLMClient, {"llm_api_key": "sk-x"}),
        ("gemini", GeminiLLMClient, {"llm_api_key": "sk-x"}),
        ("ollama", OllamaLLMClient, {"llm_model": "qwen2.5:7b"}),
        ("GEMINI", GeminiLLMClient, {"llm_api_key": "sk-x"}),   # case-insensitive
        ("  ollama  ", OllamaLLMClient, {"llm_model": "x"}),      # trimmed
    ],
)
def test_factory_selects_provider(provider, cls, kwargs):
    client = build_llm_client(Settings(llm_provider=provider, **kwargs))
    assert isinstance(client, cls)


def test_factory_unknown_provider_falls_back_to_null():
    assert isinstance(build_llm_client(Settings(llm_provider="banana")), NullLLMClient)
    assert isinstance(build_llm_client(Settings(llm_provider="")), NullLLMClient)


# ============================================================================
# Config — llm_configured
# ============================================================================

@pytest.mark.parametrize(
    "settings_kwargs,expected",
    [
        ({"llm_provider": "none"}, False),
        ({"llm_provider": "banana", "llm_api_key": "k"}, False),
        ({"llm_provider": "openai"}, False),
        ({"llm_provider": "openai", "llm_api_key": "k"}, True),
        ({"llm_provider": "anthropic", "llm_api_key": "k"}, True),
        ({"llm_provider": "gemini"}, False),
        ({"llm_provider": "gemini", "llm_api_key": "k"}, True),
        ({"llm_provider": "ollama"}, False),                 # no model
        ({"llm_provider": "ollama", "llm_model": "qwen2.5"}, True),  # no key needed
    ],
)
def test_llm_configured(settings_kwargs, expected):
    assert Settings(**settings_kwargs).llm_configured is expected


# ============================================================================
# Gemini provider
# ============================================================================

class _FakeGeminiResponse:
    def __init__(self, text: str) -> None:
        self.text = text


def _install_fake_genai(monkeypatch, *, response_text=None, raise_on_call=None):
    """Patch google.genai.Client with a fake that never hits the network."""
    genai = pytest.importorskip("google.genai")

    class _FakeModels:
        def generate_content(self, **_kw):
            if raise_on_call is not None:
                raise raise_on_call
            return _FakeGeminiResponse(response_text)

    class _FakeClient:
        def __init__(self, **_kw):
            self.models = _FakeModels()

    monkeypatch.setattr(genai, "Client", _FakeClient)


def test_gemini_selected_and_model_default():
    client = build_llm_client(Settings(llm_provider="gemini", llm_api_key="k"))
    assert client.provider == "gemini"
    assert client.model == "gemini-2.5-flash"       # default when LLM_MODEL empty
    assert client.is_available is True


def test_gemini_custom_model():
    client = build_llm_client(
        Settings(llm_provider="gemini", llm_api_key="k", llm_model="gemini-1.5-pro")
    )
    assert client.model == "gemini-1.5-pro"


def test_gemini_missing_api_key_is_unavailable():
    client = GeminiLLMClient(api_key="", model="gemini-2.5-flash")
    assert client.is_available is False
    with pytest.raises(LLMUnavailableError):
        client.complete_json("s", "u")


def test_gemini_missing_sdk(monkeypatch):
    monkeypatch.setitem(sys.modules, "google.genai", None)
    client = GeminiLLMClient(api_key="k", model="gemini-2.5-flash")
    with pytest.raises(LLMUnavailableError):
        client.complete_json("s", "u")


def test_gemini_success(monkeypatch):
    _install_fake_genai(monkeypatch, response_text='{"category": "SPAM", "confidence": 0.9}')
    client = GeminiLLMClient(api_key="k", model="gemini-2.5-flash", timeout=5.0)
    assert client.complete_json("s", "u") == {"category": "SPAM", "confidence": 0.9}


def test_gemini_api_failure_is_unavailable(monkeypatch):
    _install_fake_genai(monkeypatch, raise_on_call=RuntimeError("429 quota exceeded"))
    client = GeminiLLMClient(api_key="k", model="gemini-2.5-flash")
    with pytest.raises(LLMUnavailableError):
        client.complete_json("s", "u")


def test_gemini_empty_response_is_response_error(monkeypatch):
    _install_fake_genai(monkeypatch, response_text="")
    client = GeminiLLMClient(api_key="k", model="gemini-2.5-flash")
    with pytest.raises(LLMResponseError):
        client.complete_json("s", "u")


def test_gemini_malformed_response_is_response_error(monkeypatch):
    _install_fake_genai(monkeypatch, response_text="sorry, I can't help with that")
    client = GeminiLLMClient(api_key="k", model="gemini-2.5-flash")
    with pytest.raises(LLMResponseError):
        client.complete_json("s", "u")


# ============================================================================
# Ollama provider
# ============================================================================

class _FakeHTTPResponse:
    def __init__(self, *, json_data=None, json_exc=None, status_error=None):
        self._json_data = json_data
        self._json_exc = json_exc
        self._status_error = status_error

    def raise_for_status(self):
        if self._status_error is not None:
            raise self._status_error

    def json(self):
        if self._json_exc is not None:
            raise self._json_exc
        return self._json_data


def _patch_ollama_post(monkeypatch, handler):
    monkeypatch.setattr(llm_service.httpx, "post", handler)


def test_ollama_selected_no_api_key_needed():
    client = build_llm_client(Settings(llm_provider="ollama", llm_model="qwen2.5:7b"))
    assert client.provider == "ollama"
    assert client.model == "qwen2.5:7b"
    assert client.is_available is True          # no key required
    assert Settings(llm_provider="ollama", llm_model="qwen2.5:7b").llm_api_key == ""


def test_ollama_uses_configured_base_url(monkeypatch):
    seen = {}

    def _handler(url, **kwargs):
        seen["url"] = url
        seen["timeout"] = kwargs.get("timeout")
        return _FakeHTTPResponse(json_data={"response": '{"ok": true}'})

    _patch_ollama_post(monkeypatch, _handler)
    client = OllamaLLMClient(model="m", base_url="http://ollama.internal:11434/", timeout=7.0)
    assert client.complete_json("s", "u") == {"ok": True}
    assert seen["url"] == "http://ollama.internal:11434/api/generate"
    assert seen["timeout"] == 7.0


def test_ollama_success(monkeypatch):
    _patch_ollama_post(
        monkeypatch,
        lambda *a, **k: _FakeHTTPResponse(json_data={"response": '{"category": "EXAM"}'}),
    )
    client = OllamaLLMClient(model="qwen2.5:7b")
    assert client.complete_json("s", "u") == {"category": "EXAM"}


def test_ollama_server_unavailable(monkeypatch):
    def _refused(*a, **k):
        raise llm_service.httpx.ConnectError("connection refused")

    _patch_ollama_post(monkeypatch, _refused)
    with pytest.raises(LLMUnavailableError):
        OllamaLLMClient(model="m").complete_json("s", "u")


def test_ollama_timeout(monkeypatch):
    def _timeout(*a, **k):
        raise llm_service.httpx.TimeoutException("timed out")

    _patch_ollama_post(monkeypatch, _timeout)
    with pytest.raises(LLMUnavailableError):
        OllamaLLMClient(model="m", timeout=0.01).complete_json("s", "u")


def test_ollama_http_error_status(monkeypatch):
    err = llm_service.httpx.HTTPStatusError(
        "not found",
        request=llm_service.httpx.Request("POST", "http://x/api/generate"),
        response=llm_service.httpx.Response(404),
    )
    _patch_ollama_post(monkeypatch, lambda *a, **k: _FakeHTTPResponse(status_error=err))
    with pytest.raises(LLMUnavailableError):
        OllamaLLMClient(model="missing-model").complete_json("s", "u")


def test_ollama_error_field_means_missing_model(monkeypatch):
    _patch_ollama_post(
        monkeypatch,
        lambda *a, **k: _FakeHTTPResponse(json_data={"error": "model 'x' not found"}),
    )
    with pytest.raises(LLMUnavailableError):
        OllamaLLMClient(model="x").complete_json("s", "u")


def test_ollama_body_not_json_is_response_error(monkeypatch):
    _patch_ollama_post(
        monkeypatch,
        lambda *a, **k: _FakeHTTPResponse(json_exc=ValueError("no json")),
    )
    with pytest.raises(LLMResponseError):
        OllamaLLMClient(model="m").complete_json("s", "u")


def test_ollama_model_text_not_json_is_response_error(monkeypatch):
    _patch_ollama_post(
        monkeypatch,
        lambda *a, **k: _FakeHTTPResponse(json_data={"response": "here you go"}),
    )
    with pytest.raises(LLMResponseError):
        OllamaLLMClient(model="m").complete_json("s", "u")


# ============================================================================
# Existing providers unchanged
# ============================================================================

def test_openai_behavior_unchanged():
    client = build_llm_client(
        Settings(llm_provider="openai", llm_api_key="sk-test", llm_model="gpt-4o-mini")
    )
    assert client.provider == "openai"
    assert client.model == "gpt-4o-mini"
    assert client.is_available is True
    assert OpenAILLMClient(api_key="", model="").is_available is False


def test_anthropic_behavior_unchanged():
    client = build_llm_client(Settings(llm_provider="anthropic"))
    assert client.provider == "anthropic"
    assert client.model == "claude-sonnet-5"
    assert client.is_available is False  # no key


def test_null_behavior_unchanged():
    client = build_llm_client(Settings())
    assert isinstance(client, NullLLMClient)
    assert client.is_available is False
    with pytest.raises(LLMUnavailableError):
        client.complete_json("s", "u")
