"""System connection status — backend + configured LLM provider.

Powers ``GET /api/v1/system/status`` (a compact status bar in the Flutter app).

Design rules:
  * **Never** run an LLM completion / inference or spend API tokens just to check
    connectivity. Ollama is probed with the cheap ``GET /api/tags`` (lists local
    models — no generation). API providers are only *config-validated*: the
    existing ``LLMClient`` interface has no free reachability check, and a real
    request could hit rate limits / cost money.
  * Never echo API keys, tokens or secrets. Only ``bool(key)`` is inspected.
  * Never raise — the worst case is ``status="unknown"`` / ``"offline"``.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.core.config import Settings
from app.core.logging_setup import secure_logger
from app.services.llm_service import build_llm_client

logger = secure_logger(__name__)

#: Machine-readable component states.
ONLINE = "online"
OFFLINE = "offline"
UNCONFIGURED = "unconfigured"
UNKNOWN = "unknown"

KNOWN_PROVIDERS = frozenset({"none", "ollama", "gemini", "groq", "openai", "anthropic"})
_API_KEY_PROVIDERS = frozenset({"gemini", "groq", "openai", "anthropic"})

#: Ollama's model-list probe must be fast — a status bar, not a health gate.
_OLLAMA_PROBE_TIMEOUT_SECONDS = 3.0


@dataclass(frozen=True)
class LlmStatus:
    status: str
    provider: str | None = None
    model: str | None = None
    detail: str | None = None


def _http_get(url: str, *, timeout: float) -> httpx.Response:
    """Isolated so tests can monkeypatch it (no real network in unit tests)."""
    return httpx.get(url, timeout=timeout)


def _probe_ollama(base_url: str, model: str | None, http_get) -> LlmStatus:
    base = (base_url or "http://127.0.0.1:11434").rstrip("/")
    try:
        resp = http_get(f"{base}/api/tags", timeout=_OLLAMA_PROBE_TIMEOUT_SECONDS)
    except Exception as exc:  # noqa: BLE001 — connection refused / DNS / timeout / …
        logger.info("Ollama status probe failed: %s", type(exc).__name__)
        # Never echo the private inference host: this endpoint is public
        # (the status bar renders before login), so the detail must stay
        # user-facing, not infrastructure-revealing.
        return LlmStatus(OFFLINE, "ollama", model, "AI service is not reachable")

    status_code = getattr(resp, "status_code", 0)
    if status_code != 200:
        return LlmStatus(OFFLINE, "ollama", model, f"Ollama returned HTTP {status_code}")

    try:
        names = [str(m.get("name", "")) for m in (resp.json() or {}).get("models", [])]
    except Exception:  # noqa: BLE001 — unexpected body shape
        names = []

    if model:
        base_name = model.split(":", 1)[0]
        if names and not any(n == model or n.split(":", 1)[0] == base_name for n in names):
            return LlmStatus(
                OFFLINE, "ollama", model,
                f"model '{model}' is not pulled on the Ollama server",
            )
    return LlmStatus(ONLINE, "ollama", model, f"{len(names)} model(s) available")


def check_llm(settings: Settings, *, http_get=None) -> LlmStatus:
    """Determine the configured LLM provider's connection status.

    Returns one of ``online`` / ``offline`` / ``unconfigured`` / ``unknown``.
    """
    provider = (settings.llm_provider or "none").strip().lower()
    fallback_provider = (settings.llm_fallback_provider or "none").strip().lower()

    if fallback_provider != "none":
        primary_settings = settings.model_copy(
            update={
                "llm_fallback_provider": "none",
                "llm_fallback_model": "",
                "llm_fallback_api_key": "",
            }
        )
        fallback_settings = settings.model_copy(
            update={
                "llm_provider": fallback_provider,
                "llm_model": settings.llm_fallback_model,
                "llm_api_key": settings.llm_fallback_api_key,
                "llm_fallback_provider": "none",
                "llm_fallback_model": "",
                "llm_fallback_api_key": "",
            }
        )
        primary_status = check_llm(primary_settings, http_get=http_get)
        fallback_status = check_llm(fallback_settings, http_get=http_get)
        statuses = {primary_status.status, fallback_status.status}
        if ONLINE in statuses:
            combined = ONLINE
        elif statuses == {UNCONFIGURED}:
            combined = UNCONFIGURED
        elif UNKNOWN in statuses:
            combined = UNKNOWN
        else:
            combined = OFFLINE
        return LlmStatus(
            combined,
            f"{primary_status.provider}->{fallback_status.provider}",
            f"{primary_status.model or '-'}->{fallback_status.model or '-'}",
            f"primary {primary_status.status}; fallback {fallback_status.status}",
        )

    if provider not in KNOWN_PROVIDERS:
        return LlmStatus(UNKNOWN, provider or None, None, "unrecognised LLM_PROVIDER value")

    if provider == "none":
        return LlmStatus(UNCONFIGURED, "none", None, "No LLM provider configured (LLM_PROVIDER=none)")

    # Effective model name (the client normalises provider defaults).
    try:
        model = build_llm_client(settings).model or None
    except Exception:  # noqa: BLE001 — never let status checking break
        model = settings.llm_model or None

    if provider == "ollama":
        return _probe_ollama(settings.ollama_base_url, model, http_get or _http_get)

    # Remote API providers — config validation only (no safe cheap probe).
    if provider in _API_KEY_PROVIDERS:
        if not settings.llm_api_key:
            return LlmStatus(UNCONFIGURED, provider, model, "API key not configured")
        return LlmStatus(ONLINE, provider, model, "configured (remote connectivity not probed)")

    return LlmStatus(UNKNOWN, provider, None, None)  # unreachable, defensive
