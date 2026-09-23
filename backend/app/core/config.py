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

    # Public HTTPS URL the Flutter app + Google reach this backend at — the
    # Cloudflare Tunnel hostname in prod, the LAN address in dev. Used only to
    # DERIVE ``google_redirect_uri`` when that is left blank. NOT related to
    # ``ollama_base_url`` (that is a separate tunnel to the Ollama box).
    #   dev:     http://192.168.1.10:8000
    #   tunnel:  https://amar-api.example.com
    api_public_base_url: str = ""

    google_client_id: str = ""
    google_client_secret: str = ""
    # Blank ⇒ derived as ``{api_public_base_url}/api/v1/auth/google/callback``
    # (or the localhost dev default). Set explicitly to override. Whatever value
    # ``google_redirect_uri_resolved`` yields MUST match an Authorized redirect
    # URI on the Google OAuth client.
    google_redirect_uri: str = ""
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
    #
    # The background scheduler runs this automatically for EVERY connected user
    # every ``gmail_sync_interval_seconds`` — so new mail appears without the
    # user pulling to refresh. Configurable via ``GMAIL_SYNC_INTERVAL_SECONDS``
    # (e.g. 900 = 15 min for lighter API usage; min 1s). Manual
    # ``POST /api/v1/gmail/sync`` still works and shares the per-user lock.
    gmail_sync_enabled: bool = True
    gmail_sync_interval_seconds: int = 300
    # Per-sync ceiling on newly-added messages. Hitting it is safe: the next sync
    # resumes from the last fully-consumed history record (idempotent pipeline —
    # no duplicates, no state reset), it just does the rest next cycle.
    gmail_sync_max_messages: int = 100

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

    # --- Data security (Phase 14) ---
    # Application-level AES-256-GCM encryption of sensitive email content at
    # rest (subject / snippet / sender / action & deadline text / reminder note
    # / notification detail). Transparent to agents and API responses.
    data_encryption_enabled: bool = True
    # 32-byte key, base64url- or hex-encoded. NEVER hardcode. Generate with:
    #   python -c "import secrets,base64;print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
    # Required when APP_ENV=production and DATA_ENCRYPTION_ENABLED=true (the app
    # refuses to start otherwise). In development a missing key falls back to a
    # built-in INSECURE key with a loud warning.
    data_encryption_key: str = ""

    # --- Multi-user auth (Phase 15) ---
    # How long an application session (Flutter bearer token) stays valid.
    session_ttl_days: int = 30

    # --- Browser→app auth handoff (Phase 17) ---
    # After Google sign-in the browser callback 302-redirects here so the Flutter
    # app returns automatically (no manual browser close, no polling). The URL
    # carries only a short-lived, single-use handoff CODE — never a session or
    # Gmail token. Custom scheme today; a future HTTPS Android App Link
    # (https://app.<domain>/auth/callback) is just a value change here + on the
    # Flutter side (AUTH_APP_LINK_ORIGIN).
    app_auth_callback_url: str = "agentamar://auth/callback"
    # How long a handoff code is valid before the app must exchange it. Short by
    # design — the app redeems it within a second of the redirect. Min 30s.
    auth_handoff_ttl_seconds: int = 120

    # --- Push notifications / Firebase Cloud Messaging (Phase 16) ---
    # Backend-initiated push so a user is notified even when the Flutter app is
    # closed. Disabled or unconfigured ⇒ the pipeline still runs; only the FCM
    # send is skipped (local notifications keep working when the app is open).
    push_enabled: bool = True
    firebase_project_id: str = ""
    # EITHER a path to the service-account JSON on the server …
    firebase_credentials_file: str = ""
    # … OR the JSON itself (e.g. injected as a secret env var). Never commit it.
    firebase_credentials_json: str = ""
    # Only push notifications not older than this (avoids a backlog flood on first deploy).
    push_max_age_minutes: int = 120

    # --- Local ML pre-classifier (hybrid triage) ---
    # A lightweight scikit-learn (TF-IDF + LogisticRegression) classifier that
    # runs locally on CPU *between* the deterministic layer and the LLM. When the
    # deterministic confidence is low but the local model is confident (and does
    # not conflict with a strong deterministic signal) its answer is used and the
    # LLM call is skipped. Optional: with no trained model the triage agent
    # behaves exactly as before (deterministic -> LLM fallback).
    ml_classifier_enabled: bool = True
    ml_classifier_threshold: float = 0.85
    ml_classifier_model_path: str = "data/models/email_classifier.joblib"

    # --- Shared typed decision layer (optional) ---
    # Jev and Laya are deliberately separate from the generative LLM
    # abstraction. One request/forward-pass answers all bounded questions for
    # an email; Gemini/Groq remain the fallback for unresolved fields and for
    # reply generation. ``typesafe_api_key`` is retained for compatibility with
    # existing .env files, but the configured credential is an OpenRouter key.
    decision_provider: str = "none"  # none | jev | laya
    typesafe_api_key: str = ""
    openrouter_api_key: str = ""
    jev_model: str = "typesafe/jev-1.13"
    jev_endpoint_url: str = "https://openrouter.ai/api/alpha/decisions"
    jev_timeout_seconds: float = 30.0
    jev_choice_confidence_threshold: float = 0.85
    jev_noul_decision_threshold: float = 0.85
    jev_cache_size: int = 512
    jev_max_state_chars: int = 6000
    laya_model_id: str = "convaiinnovations/laya"
    laya_model_subfolder: str = "typed-decisions"
    laya_device: str = "cpu"
    ai_mode_state_path: str = "data/ai_mode.json"

    # --- LLM abstraction (Phase 3) ---
    llm_provider: str = "none"  # none | openai | anthropic | gemini | groq | ollama
    llm_model: str = ""
    llm_api_key: str = ""
    # Optional automatic failover. The primary provider is always attempted
    # first; this provider is used only when the primary is unavailable or
    # returns unusable JSON. Its key/model are deliberately separate so two
    # vendors can be configured without overloading LLM_API_KEY.
    llm_fallback_provider: str = "none"
    llm_fallback_model: str = ""
    llm_fallback_api_key: str = ""
    llm_max_tokens: int = 512
    llm_timeout_seconds: float = 45.0
    # Drafting 3 reply options needs a longer generation than a small
    # classification JSON — a slow local model must not 503 the reply endpoint.
    # Falls back to llm_timeout_seconds when <= 0.
    llm_reply_timeout_seconds: float = 120.0
    # Ollama (local/remote model server) — no API key required.
    ollama_base_url: str = "http://127.0.0.1:11434"

    # Max simultaneous LLM inferences. The private Ollama box has limited
    # hardware: two large concurrent generations swap and time out, so the
    # backend queues instead of overwhelming it. 0 disables the limit.
    llm_max_concurrent_requests: int = 1
    # How long a caller waits for a free inference slot before giving up and
    # falling back to the local ML result.
    llm_queue_timeout_seconds: float = 30.0

    # --- Production hardening ---
    # The unauthenticated POST /intake/gmail development helper. Off unless a
    # developer explicitly turns it on; production refuses to start with it on.
    enable_debug_intake_endpoint: bool = False
    # Comma-separated Host header allowlist (e.g. "api.example.com"). Empty =
    # allow any host (fine behind a single trusted reverse proxy).
    allowed_hosts: str = ""
    # Comma-separated browser CORS origins. Empty = no CORS headers at all,
    # which is correct for a mobile-only client (native apps send no Origin).
    cors_allow_origins: str = ""
    # Simple per-session rate limits for expensive endpoints.
    rate_limit_enabled: bool = True
    rate_limit_llm_per_minute: int = 6
    rate_limit_sync_per_minute: int = 12

    # --- derived helpers ------------------------------------------------
    @property
    def llm_reply_timeout_resolved(self) -> float:
        """Timeout for reply-draft generation — the dedicated value, or the
        general LLM timeout when it is unset/non-positive."""
        configured = self.llm_reply_timeout_seconds
        return configured if configured and configured > 0 else self.llm_timeout_seconds

    @property
    def api_public_base_url_resolved(self) -> str:
        """The backend's public origin, no trailing slash. Falls back to the
        localhost dev origin when unset."""
        return (self.api_public_base_url or "http://localhost:8000").rstrip("/")

    @property
    def google_redirect_uri_resolved(self) -> str:
        """The single source of truth for the OAuth redirect URI.

        Explicit ``GOOGLE_REDIRECT_URI`` wins (back-compat); otherwise it is
        derived from ``API_PUBLIC_BASE_URL`` so a mobile OAuth callback is never
        pointed at ``localhost`` just because that was a default.
        """
        if self.google_redirect_uri:
            return self.google_redirect_uri
        return f"{self.api_public_base_url_resolved}/api/v1/auth/google/callback"

    @property
    def oauth_configured(self) -> bool:
        """True when both OAuth client credentials are present."""
        return bool(self.google_client_id and self.google_client_secret)

    @property
    def llm_configured(self) -> bool:
        """True when the primary or fallback provider can run.

        Remote providers need an API key; ``ollama`` needs only a model name;
        ``none`` (and anything unknown) is never configured.
        """
        def configured(provider: str, api_key: str, model: str) -> bool:
            provider = provider.strip().lower()
            if provider in {"openai", "anthropic", "gemini", "groq"}:
                return bool(api_key)
            if provider == "ollama":
                return bool(model)
            return False

        return configured(self.llm_provider, self.llm_api_key, self.llm_model) or configured(
            self.llm_fallback_provider,
            self.llm_fallback_api_key,
            self.llm_fallback_model,
        )

    @property
    def jev_configured(self) -> bool:
        return (
            self.decision_provider.strip().lower() == "jev"
            and bool(self.jev_api_key)
        )

    @property
    def jev_api_key(self) -> str:
        """OpenRouter credential, with the legacy variable as fallback."""
        return (self.openrouter_api_key or self.typesafe_api_key).strip()

    @property
    def laya_configured(self) -> bool:
        return self.decision_provider.strip().lower() == "laya"

    @property
    def ai_mode_state_path_resolved(self) -> Path:
        path = Path(self.ai_mode_state_path)
        return path if path.is_absolute() else _BACKEND_DIR / path

    @property
    def is_production(self) -> bool:
        return self.app_env.strip().lower() == "production"

    @property
    def push_configured(self) -> bool:
        """True when FCM can actually send (enabled + a credential source)."""
        return bool(
            self.push_enabled
            and (self.firebase_credentials_file or self.firebase_credentials_json)
        )

    @property
    def ml_classifier_model_path_resolved(self) -> Path:
        """Absolute path to the local ML model file.

        A relative path resolves against the backend directory so it does not
        depend on the process working directory.
        """
        p = Path(self.ml_classifier_model_path)
        return p if p.is_absolute() else _BACKEND_DIR / p

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


class ProductionConfigError(RuntimeError):
    """Raised at startup when APP_ENV=production is missing required config.

    Fail fast and loudly: a half-configured production process is worse than
    one that refuses to boot (it would serve real users off a dev database, an
    unreachable OAuth redirect, or an unauthenticated LLM path).
    """


def validate_production_config(settings: "Settings") -> list[str]:
    """Return the list of production misconfigurations in ``settings``.

    Pure and side-effect free so it can be unit-tested and also used by a
    pre-deploy check; :func:`enforce_production_config` is what startup calls.
    Every rule here maps to a real deployment failure mode, not a style
    preference.
    """
    problems: list[str] = []
    if not settings.is_production:
        return problems

    url = settings.database_url_resolved
    if url.startswith("sqlite"):
        problems.append(
            "DATABASE_URL still points at SQLite. Production needs PostgreSQL "
            "(postgresql+psycopg://user:pass@host:5432/dbname) — SQLite cannot "
            "serve the API and the worker process safely at the same time."
        )

    if not settings.google_client_id or not settings.google_client_secret:
        problems.append(
            "GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET are required — without "
            "them no user can connect Gmail."
        )

    public = settings.api_public_base_url.strip()
    if not public:
        problems.append(
            "API_PUBLIC_BASE_URL is required so Google redirects to the public "
            "HTTPS callback instead of a localhost/LAN address."
        )
    elif not public.lower().startswith("https://"):
        problems.append(
            "API_PUBLIC_BASE_URL must be HTTPS in production (OAuth "
            "credentials and session bearer tokens travel over it)."
        )

    redirect = settings.google_redirect_uri_resolved
    if redirect and not redirect.lower().startswith("https://"):
        problems.append(
            "The resolved Google redirect URI is not HTTPS "
            "— Google will reject it and tokens would travel in clear text."
        )

    if settings.data_encryption_enabled and not settings.data_encryption_key:
        problems.append(
            "DATA_ENCRYPTION_KEY is required when DATA_ENCRYPTION_ENABLED=true "
            "(OAuth credentials and PII are stored encrypted at rest)."
        )

    provider = settings.llm_provider.strip().lower()
    if provider == "ollama":
        base = settings.ollama_base_url.strip().lower()
        if base.startswith("http://127.0.0.1") or base.startswith("http://localhost"):
            # Loopback is correct when the worker and Ollama share a host or a
            # private network namespace; anything else must not be plain HTTP.
            pass
        elif base.startswith("http://"):
            problems.append(
                "OLLAMA_BASE_URL uses plain HTTP over a non-loopback address. "
                "Reach Ollama over a private network (Tailscale / Docker "
                "network / VPN) or HTTPS — never the public internet."
            )

    if settings.enable_debug_intake_endpoint:
        problems.append(
            "ENABLE_DEBUG_INTAKE_ENDPOINT=true exposes the unauthenticated "
            "POST /intake/gmail development endpoint. Disable it in production."
        )

    return problems


def enforce_production_config(settings: "Settings") -> None:
    """Abort startup when production configuration is incomplete."""
    problems = validate_production_config(settings)
    if problems:
        raise ProductionConfigError(
            "Refusing to start in production with an incomplete configuration:\n"
            + "\n".join(f"  * {p}" for p in problems)
        )
