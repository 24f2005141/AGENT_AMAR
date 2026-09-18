"""FastAPI app for the AGENT AMAR backend.

Phase 2, slice 2 — real Gmail integration:

    GET  /health                          liveness probe
    GET  /                                service metadata
    POST /intake/gmail                    run the Mail Intake Agent on a raw payload
    GET  /api/v1/auth/google/login        start Google OAuth
    GET  /api/v1/auth/google/callback     OAuth redirect target
    GET  /api/v1/auth/google/status       is Gmail connected?
    POST /api/v1/auth/google/disconnect   forget stored credentials (dev)
    GET  /api/v1/gmail/unread             unread messages -> NormalizedEmail
    GET  /api/v1/gmail/unread/triage      ... -> Triage Agent classification
    GET  /api/v1/gmail/unread/actions     ... -> Action Agent required actions
    GET  /api/v1/gmail/unread/deadlines   ... -> Deadline Agent extracted deadlines
    GET  /api/v1/gmail/unread/priorities  ... -> Priority Agent score + level
    GET  /api/v1/gmail/unread/process     ... -> Final Decision + persisted state
    GET  /api/v1/emails[...]              persisted email state + user actions
    POST /api/v1/monitor/deadlines/check  run the Deadline Monitor (time injectable)
    GET  /api/v1/monitor/status           background scheduler status
    POST /api/v1/emails/{id}/reminders    create a user-scheduled reminder
    GET  /api/v1/notifications            query generated notification events

Phase 10: the Deadline Monitor evaluates persisted deadlines + user reminders
and produces escalating notification events (NORMAL → REMINDER → URGENT →
ALARM).
Phase 11B.1: an in-process background scheduler runs those checks automatically
on a configurable interval (startup → run → shutdown). Still no actual
delivery — the Flutter layer consumes the ``notifications`` rows.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import Body, FastAPI, Request
from fastapi.responses import JSONResponse

from app import __version__
from app.agents.intake_agent import MailIntakeAgent
from app.api import (
    audit_router,
    auth_router,
    devices_router,
    gmail_router,
    monitor_router,
    reply_router,
    state_router,
    system_router,
)
from app.core import crypto
from app.core.errors import GmailIntegrationError
from app.services.llm_service import LLMError, LLMUnavailableError
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.middleware import RateLimitMiddleware, RequestContextMiddleware
from app.core.config import enforce_production_config, get_settings
from app.services import llm_concurrency
from app.core.logging_setup import install as install_log_redaction
from app.core.logging_setup import secure_logger
from sqlalchemy import text

from app.db.session import db_session, init_db
from app.services.scheduler import get_scheduler
from app.services.system_status import check_llm

logger = secure_logger("agent_amar")
settings = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Phase 14: redact sensitive values from every log line; validate the
    # encryption config (fails fast in production if the key is missing/bad).
    install_log_redaction()
    cfg = get_settings()
    # Production must be fully configured before it serves a single request —
    # a half-configured prod process silently runs on a dev database or an
    # unreachable OAuth redirect. Dev/staging are unaffected.
    enforce_production_config(cfg)
    crypto.configure(cfg)
    # Bound LLM inference to what the private box can actually run at once.
    llm_concurrency.configure(
        cfg.llm_max_concurrent_requests, cfg.llm_queue_timeout_seconds
    )
    # Phase 17: mobile "Continue with Google" cannot work if Google redirects the
    # browser to localhost — the phone can't reach it, so the deep-link handoff
    # never runs. Warn loudly at startup.
    if cfg.oauth_configured:
        redirect = cfg.google_redirect_uri_resolved
        if "localhost" in redirect or "127.0.0.1" in redirect:
            logger.warning(
                "GOOGLE_REDIRECT_URI resolves to %s — this only works for "
                "co-located desktop dev. For sign-in from a phone/tablet set "
                "API_PUBLIC_BASE_URL=https://<public-host> (and clear "
                "GOOGLE_REDIRECT_URI), then add "
                "https://<public-host>/api/v1/auth/google/callback to the Google "
                "OAuth client. See docs/OAUTH_TUNNEL_TESTING.md.",
                redirect,
            )
        else:
            logger.info("OAuth redirect URI: %s -> app deep link %s",
                        redirect, cfg.app_auth_callback_url)
    # Phase 9: create any missing tables at startup (dev; Alembic later).
    init_db()
    # Phase 11B.1: start the background monitoring scheduler (no-op if disabled).
    scheduler = get_scheduler()
    scheduler.start()
    try:
        yield
    finally:
        await scheduler.stop()


app = FastAPI(
    title="AGENT AMAR Backend",
    version=__version__,
    description="AGENT AMAR — multi-agent email intelligence + persistent state.",
    lifespan=lifespan,
)

# --- Production middleware -------------------------------------------------
# Order matters: the outermost middleware is added last. Request context wraps
# everything so even a rate-limited response carries a request id.
app.add_middleware(RateLimitMiddleware, settings=settings)
app.add_middleware(RequestContextMiddleware)

_allowed_hosts = [h.strip() for h in settings.allowed_hosts.split(",") if h.strip()]
if _allowed_hosts:
    # Blocks Host-header spoofing when the app is not behind a proxy that
    # already pins the host.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=_allowed_hosts)

_cors_origins = [o.strip() for o in settings.cors_allow_origins.split(",") if o.strip()]
if _cors_origins:
    # Only for an explicit browser client. The mobile app sends no Origin, so
    # CORS stays off (and never "*") unless origins are configured.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    )

app.include_router(auth_router)
app.include_router(gmail_router)
app.include_router(state_router)
app.include_router(monitor_router)
app.include_router(audit_router)
app.include_router(devices_router)
app.include_router(system_router)
app.include_router(reply_router)

_intake_agent = MailIntakeAgent(settings)


@app.exception_handler(GmailIntegrationError)
def _handle_gmail_error(request: Request, exc: GmailIntegrationError) -> JSONResponse:
    """Turn typed Gmail errors into clean responses (no secrets, no stack traces)."""
    logger.warning("gmail integration error: %s", type(exc).__name__)
    return JSONResponse(
        status_code=exc.http_status,
        content={
            "error": type(exc).__name__,
            "detail": exc.public_message,
            "provider": "gmail",
        },
    )


@app.exception_handler(LLMError)
def _handle_llm_error(request: Request, exc: LLMError) -> JSONResponse:
    """Map an LLM failure to a clean response. Never leaks a prompt or a key.

    * ``LLMUnavailableError`` -> ``503`` (no provider / provider down -> retry).
    * ``LLMResponseError``    -> ``502`` (the model replied with unusable output).
    The client shows an error state with a retry; no fake replies are produced.
    """
    unavailable = isinstance(exc, LLMUnavailableError)
    logger.warning("llm error: %s", type(exc).__name__)
    return JSONResponse(
        status_code=503 if unavailable else 502,
        content={
            "error": type(exc).__name__,
            "detail": (
                "The AI service is currently unavailable. Please try again in a moment."
                if unavailable
                else "The AI response could not be used. Please try again."
            ),
            "provider": "llm",
        },
    )


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness probe — is the process up? Intentionally dependency-free so a
    database blip never causes the orchestrator to kill a healthy container."""
    return {"status": "ok", "service": settings.app_name}


@app.get("/health/ready")
def health_ready() -> JSONResponse:
    """Readiness probe — can this instance actually serve traffic?

    Reports only coarse per-dependency states. It deliberately exposes no
    hostnames, URLs, driver versions or error text: an unauthenticated probe
    must never become an infrastructure map. 503 when a hard dependency is
    down, so a load balancer stops sending traffic here.
    """
    checks: dict[str, str] = {}

    try:
        with db_session() as session:
            session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:
        logger.exception("readiness: database check failed")
        checks["database"] = "error"

    scheduler = get_scheduler()
    checks["scheduler"] = "running" if scheduler.running else "stopped"

    # Advisory only: the app degrades to the local ML classifier when the LLM
    # is unavailable, so this never fails readiness.
    try:
        checks["llm"] = check_llm(settings).status
    except Exception:
        checks["llm"] = "unknown"

    ready = checks["database"] == "ok"
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"status": "ready" if ready else "degraded", "checks": checks},
    )


@app.get("/")
def root() -> dict[str, Any]:
    """Service metadata and available routes."""
    return {
        "service": settings.app_name,
        "version": __version__,
        "phase": "18 - User classification feedback + full email viewing",
        # Effective OAuth config (no secrets) — for verifying a mobile setup:
        # oauth_redirect_uri MUST be the reachable HTTPS host you registered with
        # Google (never localhost for a phone). app_auth_callback is where the
        # browser is 302'd back to the app.
        "oauth": {
            "configured": settings.oauth_configured,
            "redirect_uri": settings.google_redirect_uri_resolved,
            "app_auth_callback": settings.app_auth_callback_url,
        },
        "endpoints": [
            "/health",
            "/api/v1/audit/verify",
            "/api/v1/audit/events",
            "/intake/gmail",
            "/api/v1/auth/google/start",
            "/api/v1/auth/google/login",
            "/api/v1/auth/google/callback",
            "/api/v1/auth/session/exchange",
            "/api/v1/auth/google/session",
            "/api/v1/auth/me",
            "/api/v1/auth/logout",
            "/api/v1/auth/google/status",
            "/api/v1/auth/google/disconnect",
            "/api/v1/devices/register",
            "/api/v1/devices/unregister",
            "/api/v1/devices",
            "/api/v1/gmail/unread",
            "/api/v1/gmail/unread/triage",
            "/api/v1/gmail/unread/actions",
            "/api/v1/gmail/unread/deadlines",
            "/api/v1/gmail/unread/priorities",
            "/api/v1/gmail/unread/process",
            "/api/v1/gmail/sync",
            "/api/v1/gmail/sync/status",
            "/api/v1/emails",
            "/api/v1/emails/{email_id}",
            "/api/v1/emails/human-review",
            "/api/v1/emails/{email_id}/viewed",
            "/api/v1/emails/{email_id}/complete",
            "/api/v1/emails/{email_id}/reopen",
            "/api/v1/emails/clear-acknowledged",
            "/api/v1/emails/{email_id}/snooze",
            "/api/v1/emails/{email_id}/actions/{action_ref}/complete",
            "/api/v1/emails/{email_id}/actions/{action_ref}/dismiss",
            "/api/v1/emails/{email_id}/processing",
            "/api/v1/emails/{email_id}/classification-feedback",
            "/api/v1/emails/{email_id}/full",
            "/api/v1/emails/{email_id}/reply-suggestions",
            "/api/v1/emails/{email_id}/reply",
            "/api/v1/actions/pending",
            "/api/v1/deadlines/upcoming",
            "/api/v1/monitor/deadlines/check",
            "/api/v1/monitor/status",
            "/api/v1/system/status",
            "/api/v1/emails/{email_id}/reminders",
            "/api/v1/reminders",
            "/api/v1/notifications",
            "/api/v1/notifications/{id}",
        ],
    }


def register_debug_routes(target_app: FastAPI, cfg) -> bool:
    """Attach the unauthenticated development helpers to ``target_app``.

    Kept as an explicit, testable function rather than an inline ``if`` so the
    gating itself can be verified without reloading modules. Returns whether
    anything was registered.

    ``POST /intake/gmail`` takes a raw Gmail message and returns the normalized
    envelope. It has no authentication by design (local agent debugging), which
    is exactly why it is opt-in and why production refuses to start with
    ENABLE_DEBUG_INTAKE_ENDPOINT=true.
    """
    if not cfg.enable_debug_intake_endpoint:
        return False

    @target_app.post("/intake/gmail")
    def intake_gmail(raw_message: dict[str, Any] = Body(...)) -> dict[str, Any]:
        return _intake_agent.run(raw_message).to_wire()

    return True


register_debug_routes(app, settings)
