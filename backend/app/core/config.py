"""Application settings.

Loaded from environment variables, with an optional ``.env`` file in the
backend directory as a fallback. Every setting has a default so the intake
slice runs with zero configuration; the Gmail OAuth values must be filled in
before the Gmail integration can be used.

Kept dependency-free on purpose (no pydantic-settings): there are only a
handful of values and a plain loader is easier to follow.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field

_BACKEND_DIR = Path(__file__).resolve().parents[2]
_ENV_FILE = _BACKEND_DIR / ".env"


def _load_env_file(path: Path) -> dict[str, str]:
    """Parse a minimal ``KEY=VALUE`` .env file. Missing file -> empty dict."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text("utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


class Settings(BaseModel):
    """Backend configuration.

    Attributes:
        app_env: ``development`` / ``production`` marker.
        app_name: Service identifier reported by ``GET /health``.
        default_timezone: IANA timezone used to render the ISO 8601 timestamps
            in the normalized email (``received_at`` / ``ingested_at``).
        gmail_id_prefix / gmail_thread_id_prefix: ID convention from
            ``04-Schemas/Email Schema.md``.
        gmail_user_id: Gmail API user id; ``"me"`` means the authorized user.
        google_client_id / google_client_secret: OAuth 2.0 client credentials
            from the Google Cloud console. Empty until configured.
        google_redirect_uri: Must match a redirect URI registered on the OAuth
            client and the ``/api/v1/auth/google/callback`` route.
        google_token_storage_path: Directory for the file-based token store
            (development only; swap for a DB-backed store later).
    """

    app_env: str = "development"
    app_name: str = "agent-amar-backend"
    default_timezone: str = "Asia/Kolkata"

    gmail_id_prefix: str = "gmail_"
    gmail_thread_id_prefix: str = "gmail_thread_"
    gmail_user_id: str = "me"

    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/api/v1/auth/google/callback"
    google_token_storage_path: str = ".tokens"

    # --- Persistence (Phase 9) ---
    # Dev default: a SQLite file next to the backend. Swap for a postgresql://
    # URL later — the persistence layer is engine-agnostic (SQLAlchemy 2.x).
    database_url: str = "sqlite:///./agent_amar.db"
    database_echo: bool = False

    # --- Triage Agent (Phase 3) ---
    # Below this final confidence the classification is flagged
    # needs_human_review (Classification Rules example D ~0.35 -> OTHER + review).
    triage_review_threshold: float = 0.55
    # Deterministic confidence below this escalates to the LLM (if configured).
    triage_llm_threshold: float = 0.70
    # Opportunity email from an unknown external sender: cap confidence here
    # (Classification Rules "Edge cases").
    triage_unknown_opportunity_cap: float = 0.70

    # --- Action Agent (Phase 5) ---
    # Below this final confidence -> needs_human_review.
    action_review_threshold: float = 0.55
    # Deterministic confidence below this (or conflicting signals) -> LLM.
    action_llm_threshold: float = 0.65

    # --- Deadline Agent (Phase 6) ---
    deadline_review_threshold: float = 0.55
    deadline_llm_threshold: float = 0.60
    # How to read an ambiguous all-numeric date like 05/09/2026: DMY (India/EU) or MDY (US).
    deadline_date_locale: str = "DMY"

    # --- Background scheduler (Phase 11B.1) ---
    # Single in-process asyncio scheduler; starts/stops with the FastAPI app.
    scheduler_enabled: bool = True
    deadline_check_interval_seconds: int = 60
    reminder_check_interval_seconds: int = 60

    # --- Incremental Gmail sync (Phase 12) ---
    # First connect records a monitoring baseline (current historyId) and does
    # NOT ingest the historical unread inbox. Later cycles use the Gmail History
    # API to process only newly added messages.
    gmail_sync_enabled: bool = True
    gmail_sync_interval_seconds: int = 120
    gmail_sync_max_messages: int = 25

    # --- Deadline Monitoring + Reminder Escalation (Phase 10) ---
    # Quiet hours (local, [0..24)) — User Preferences §3 default 23:00–07:00.
    quiet_hours_start: int = 23
    quiet_hours_end: int = 7
    # Blank -> use default_timezone.
    quiet_hours_timezone: str = ""
    # An ALARM may break quiet hours, but only for a CRITICAL deadline.
    alarm_breaks_quiet_hours_for_critical: bool = True
    # Post-deadline grace before monitoring stops (User Preferences §4 = 24h).
    deadline_passed_grace_hours: int = 24
    # Reject a user-scheduled reminder further out than this.
    reminder_max_horizon_days: int = 365

    # --- Priority Agent (Phase 7) ---
    # Deadline-proximity points are multiplied by this when the deadline is
    # flagged ambiguous but still has a concrete datetime (Priority Rules).
    priority_ambiguous_deadline_factor: float = 0.7
    # Hard cap on the LLM's contextual score nudge (Priority Rules §3: -10..+10).
    priority_llm_max_adjustment: int = 10

    # --- LLM abstraction (Phase 3) ---
    llm_provider: str = "none"  # none | openai | anthropic | gemini | ollama
    llm_model: str = ""
    llm_api_key: str = ""
    llm_max_tokens: int = 512
    llm_timeout_seconds: float = 20.0
    # Ollama (local/remote model server) — no API key required.
    ollama_base_url: str = "http://127.0.0.1:11434"

    # --- derived helpers ------------------------------------------------
    @property
    def oauth_configured(self) -> bool:
        """True when both OAuth client credentials are present."""
        return bool(self.google_client_id and self.google_client_secret)

    @property
    def llm_configured(self) -> bool:
        """True when the selected provider has what it needs to run.

        ``openai`` / ``anthropic`` / ``gemini`` need an API key; ``ollama`` needs
        only a model name; ``none`` (and anything unknown) is never configured.
        """
        provider = self.llm_provider.strip().lower()
        if provider in {"openai", "anthropic", "gemini"}:
            return bool(self.llm_api_key)
        if provider == "ollama":
            return bool(self.llm_model)
        return False

    @property
    def token_storage_dir(self) -> Path:
        """Absolute path to the token storage directory."""
        p = Path(self.google_token_storage_path)
        return p if p.is_absolute() else _BACKEND_DIR / p

    @property
    def quiet_hours_tz_resolved(self) -> str:
        """Timezone for the quiet-hours window (falls back to default_timezone)."""
        return self.quiet_hours_timezone.strip() or self.default_timezone

    @property
    def database_url_resolved(self) -> str:
        """A relative ``sqlite:///./x`` URL is resolved against the backend dir
        so the DB file location does not depend on the current directory."""
        url = self.database_url
        prefix = "sqlite:///./"
        if url.startswith(prefix):
            return f"sqlite:///{(_BACKEND_DIR / url[len(prefix):]).as_posix()}"
        return url

    @classmethod
    def load(cls) -> "Settings":
        """Build settings from ``.env`` (if present) then the process env."""
        merged = {**_load_env_file(_ENV_FILE), **os.environ}
        fields = {
            name: merged[name.upper()]
            for name in cls.model_fields
            if name.upper() in merged
        }
        return cls(**fields)


@lru_cache
def get_settings() -> Settings:
    """Return a cached :class:`Settings` instance."""
    return Settings.load()
