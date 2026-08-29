"""LLM abstraction for the agents.

An agent only needs one capability: *given a short system + user prompt, return a
small JSON object*. This module hides the provider behind :class:`LLMClient` so
nothing else in the codebase depends on a specific SDK.

Rules honoured here:
  * No API keys in code — everything comes from :class:`Settings`.
  * Provider SDKs (``anthropic`` / ``openai`` / ``google-genai``) are imported
    lazily, so the app and the deterministic-only tests run with none installed.
  * When unavailable or failing, callers get a typed error and degrade — the
    application never crashes because an LLM is missing / a server is down.

Supported providers (``LLM_PROVIDER``): ``none`` · ``openai`` · ``anthropic`` ·
``gemini`` · ``ollama``.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any

import httpx  # always installed (fastapi.testclient); used by the Ollama client

from app.core.config import Settings


class LLMError(Exception):
    """Base class for LLM failures."""


class LLMUnavailableError(LLMError):
    """No provider configured, SDK missing, or the call could not be made."""


class LLMResponseError(LLMError):
    """The provider replied but the content was not usable JSON."""


class LLMClient(ABC):
    """Minimal JSON-completion interface."""

    provider: str = "none"
    model: str = ""

    @property
    @abstractmethod
    def is_available(self) -> bool:
        """Whether a real call can be attempted right now."""

    @abstractmethod
    def complete_json(
        self, system: str, user: str, *, max_tokens: int = 512
    ) -> dict[str, Any]:
        """Return the parsed JSON object from the model's reply.

        Raises :class:`LLMUnavailableError` or :class:`LLMResponseError`.
        """


class NullLLMClient(LLMClient):
    """Always unavailable. The default when ``LLM_PROVIDER=none``."""

    provider = "none"

    @property
    def is_available(self) -> bool:
        return False

    def complete_json(self, system: str, user: str, *, max_tokens: int = 512) -> dict[str, Any]:
        raise LLMUnavailableError("No LLM provider configured (LLM_PROVIDER=none).")


def _extract_json_object(text: str) -> dict[str, Any]:
    """Parse the first top-level JSON object in ``text``."""
    text = (text or "").strip()
    if not text:
        raise LLMResponseError("Empty LLM response.")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise LLMResponseError("No JSON object found in LLM response.")
        try:
            parsed = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise LLMResponseError(f"Malformed JSON in LLM response: {exc}") from exc
    if not isinstance(parsed, dict):
        raise LLMResponseError("LLM response JSON was not an object.")
    return parsed


class AnthropicLLMClient(LLMClient):
    """Anthropic Messages API provider."""

    provider = "anthropic"

    def __init__(self, api_key: str, model: str, timeout: float = 20.0) -> None:
        self._api_key = api_key
        self.model = model or "claude-sonnet-5"
        self._timeout = timeout

    @property
    def is_available(self) -> bool:
        return bool(self._api_key)

    def complete_json(self, system: str, user: str, *, max_tokens: int = 512) -> dict[str, Any]:
        if not self._api_key:
            raise LLMUnavailableError("ANTHROPIC api key not configured.")
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover
            raise LLMUnavailableError("`anthropic` package is not installed.") from exc

        try:
            client = anthropic.Anthropic(api_key=self._api_key, timeout=self._timeout)
            message = client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            parts = [b.text for b in message.content if getattr(b, "type", None) == "text"]
        except Exception as exc:  # network / auth / rate limit
            raise LLMUnavailableError(f"Anthropic request failed: {type(exc).__name__}") from exc

        return _extract_json_object("".join(parts))


class OpenAILLMClient(LLMClient):
    """OpenAI Chat Completions provider."""

    provider = "openai"

    def __init__(self, api_key: str, model: str, timeout: float = 20.0) -> None:
        self._api_key = api_key
        self.model = model or "gpt-4o-mini"
        self._timeout = timeout

    @property
    def is_available(self) -> bool:
        return bool(self._api_key)

    def complete_json(self, system: str, user: str, *, max_tokens: int = 512) -> dict[str, Any]:
        if not self._api_key:
            raise LLMUnavailableError("OPENAI api key not configured.")
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover
            raise LLMUnavailableError("`openai` package is not installed.") from exc

        try:
            client = OpenAI(api_key=self._api_key, timeout=self._timeout)
            completion = client.chat.completions.create(
                model=self.model,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
            content = completion.choices[0].message.content or ""
        except Exception as exc:
            raise LLMUnavailableError(f"OpenAI request failed: {type(exc).__name__}") from exc

        return _extract_json_object(content)


class GeminiLLMClient(LLMClient):
    """Google Gemini provider (modern ``google-genai`` SDK)."""

    provider = "gemini"

    def __init__(self, api_key: str, model: str, timeout: float = 20.0) -> None:
        self._api_key = api_key
        self.model = model or "gemini-2.5-flash"
        self._timeout = timeout

    @property
    def is_available(self) -> bool:
        return bool(self._api_key)

    def complete_json(self, system: str, user: str, *, max_tokens: int = 512) -> dict[str, Any]:
        if not self._api_key:
            raise LLMUnavailableError("GEMINI api key not configured.")
        try:
            import google.genai as genai
            from google.genai import types
        except ImportError as exc:  # pragma: no cover
            raise LLMUnavailableError("`google-genai` package is not installed.") from exc

        try:
            try:
                client = genai.Client(
                    api_key=self._api_key,
                    http_options=types.HttpOptions(timeout=int(self._timeout * 1000)),
                )
            except Exception:  # older SDK without http_options timeout
                client = genai.Client(api_key=self._api_key)
            response = client.models.generate_content(
                model=self.model,
                contents=user,
                config=types.GenerateContentConfig(
                    system_instruction=system,
                    max_output_tokens=max_tokens,
                    response_mime_type="application/json",
                ),
            )
            text = getattr(response, "text", None) or ""
        except Exception as exc:  # network / auth / rate limit / quota
            raise LLMUnavailableError(f"Gemini request failed: {type(exc).__name__}") from exc

        return _extract_json_object(text)


class OllamaLLMClient(LLMClient):
    """Local or remote Ollama server (``/api/generate``). No API key needed."""

    provider = "ollama"

    def __init__(
        self, model: str, base_url: str = "http://127.0.0.1:11434", timeout: float = 20.0
    ) -> None:
        self.model = model or "llama3.1"
        self._base_url = (base_url or "http://127.0.0.1:11434").rstrip("/")
        self._timeout = timeout

    @property
    def is_available(self) -> bool:
        # No key — a model name is all Ollama needs.
        return bool(self.model)

    def complete_json(self, system: str, user: str, *, max_tokens: int = 512) -> dict[str, Any]:
        if not self.model:
            raise LLMUnavailableError("OLLAMA model not configured (LLM_MODEL).")
        payload = {
            "model": self.model,
            "system": system,
            "prompt": user,
            "stream": False,
            "format": "json",
            "options": {"num_predict": max_tokens},
        }
        try:
            resp = httpx.post(
                f"{self._base_url}/api/generate", json=payload, timeout=self._timeout
            )
            resp.raise_for_status()
        except httpx.TimeoutException as exc:
            raise LLMUnavailableError("Ollama request timed out.") from exc
        except httpx.HTTPStatusError as exc:
            raise LLMUnavailableError(
                f"Ollama returned HTTP {exc.response.status_code}."
            ) from exc
        except httpx.HTTPError as exc:  # connection refused, DNS, read error, …
            raise LLMUnavailableError(
                f"Ollama request failed: {type(exc).__name__}"
            ) from exc

        try:
            data = resp.json()
        except ValueError as exc:
            raise LLMResponseError("Ollama response body was not valid JSON.") from exc
        if isinstance(data, dict) and data.get("error"):
            # e.g. model not pulled — surfaced as {"error": "model '…' not found"}
            raise LLMUnavailableError(f"Ollama error: {data['error']}")

        text = data.get("response", "") if isinstance(data, dict) else ""
        return _extract_json_object(text)


def build_llm_client(settings: Settings) -> LLMClient:
    """Factory: pick a provider from settings. Unknown / unset -> NullLLMClient."""
    provider = (settings.llm_provider or "none").strip().lower()
    if provider == "anthropic":
        return AnthropicLLMClient(
            settings.llm_api_key, settings.llm_model, settings.llm_timeout_seconds
        )
    if provider == "openai":
        return OpenAILLMClient(
            settings.llm_api_key, settings.llm_model, settings.llm_timeout_seconds
        )
    if provider == "gemini":
        return GeminiLLMClient(
            settings.llm_api_key, settings.llm_model, settings.llm_timeout_seconds
        )
    if provider == "ollama":
        return OllamaLLMClient(
            settings.llm_model, settings.ollama_base_url, settings.llm_timeout_seconds
        )
    return NullLLMClient()
