"""Persistent runtime selection of AMAR's decision approach.

The selected mode is intentionally global: this project runs one personal
backend on the user's laptop, including a background Gmail scheduler. Keeping
the choice server-side means foreground API calls and background processing use
the same approach after a restart.
"""

from __future__ import annotations

import importlib.util
import json
import threading
from functools import lru_cache
from pathlib import Path

from app.core.config import Settings, get_settings

AI_MODES = ("conventional", "jev", "laya")


class AiModeStore:
    def __init__(self, path: Path, default: str = "conventional") -> None:
        self._path = path
        self._default = default if default in AI_MODES else "conventional"
        self._lock = threading.RLock()

    def get(self) -> str:
        with self._lock:
            try:
                value = json.loads(self._path.read_text("utf-8")).get("mode")
            except (OSError, ValueError, AttributeError):
                return self._default
            return value if value in AI_MODES else self._default

    def set(self, mode: str) -> str:
        mode = mode.strip().lower()
        if mode not in AI_MODES:
            raise ValueError(f"Unknown AI mode: {mode}")
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._path.with_suffix(self._path.suffix + ".tmp")
            temporary.write_text(json.dumps({"mode": mode}, indent=2), "utf-8")
            temporary.replace(self._path)
        return mode


def _default_mode(settings: Settings) -> str:
    configured = settings.decision_provider.strip().lower()
    return configured if configured in {"jev", "laya"} else "conventional"


@lru_cache
def get_ai_mode_store() -> AiModeStore:
    settings = get_settings()
    return AiModeStore(
        settings.ai_mode_state_path_resolved,
        default=_default_mode(settings),
    )


def effective_ai_settings(settings: Settings | None = None) -> Settings:
    base = settings or get_settings()
    mode = get_ai_mode_store().get()
    provider = "none" if mode == "conventional" else mode
    return base.model_copy(update={"decision_provider": provider})


def mode_options(settings: Settings | None = None) -> list[dict[str, object]]:
    base = settings or get_settings()
    laya_installed = importlib.util.find_spec("laya") is not None
    return [
        {
            "id": "conventional",
            "label": "Gemini + Groq",
            "available": base.llm_configured,
            "model": f"{base.llm_provider} → {base.llm_fallback_provider}",
            "detail": "Generative classification; Groq is the fallback.",
        },
        {
            "id": "jev",
            "label": "Jev AI",
            "available": bool(base.jev_api_key),
            "model": base.jev_model,
            "detail": "OpenRouter Decisions API; paid per input token.",
        },
        {
            "id": "laya",
            "label": "Laya (local)",
            "available": laya_installed,
            "model": (
                f"{base.laya_model_id}/{base.laya_model_subfolder}"
                if base.laya_model_subfolder
                else base.laya_model_id
            ),
            "detail": "Runs locally; the first use downloads and loads the checkpoint.",
        },
    ]


def ensure_mode_available(mode: str, settings: Settings | None = None) -> None:
    option = next((item for item in mode_options(settings) if item["id"] == mode), None)
    if option is None:
        raise ValueError(f"Unknown AI mode: {mode}")
    if not option["available"]:
        raise RuntimeError(f"AI mode {mode!r} is not configured on this backend")
