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

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Body, FastAPI, Request
from fastapi.responses import JSONResponse

from app import __version__
from app.agents.intake_agent import MailIntakeAgent
from app.api import auth_router, gmail_router, monitor_router, state_router
from app.core.errors import GmailIntegrationError
from app.core.config import get_settings
from app.db.session import init_db
from app.services.scheduler import get_scheduler

logger = logging.getLogger("agent_amar")
settings = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI):
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

app.include_router(auth_router)
app.include_router(gmail_router)
app.include_router(state_router)
app.include_router(monitor_router)

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


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok", "service": settings.app_name}


@app.get("/")
def root() -> dict[str, Any]:
    """Service metadata and available routes."""
    return {
        "service": settings.app_name,
        "version": __version__,
        "phase": "12 - Incremental Gmail sync + automatic monitoring",
        "endpoints": [
            "/health",
            "/intake/gmail",
            "/api/v1/auth/google/login",
            "/api/v1/auth/google/callback",
            "/api/v1/auth/google/status",
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
            "/api/v1/emails/{email_id}/snooze",
            "/api/v1/emails/{email_id}/actions/{action_ref}/complete",
            "/api/v1/emails/{email_id}/actions/{action_ref}/dismiss",
            "/api/v1/emails/{email_id}/processing",
            "/api/v1/actions/pending",
            "/api/v1/deadlines/upcoming",
            "/api/v1/monitor/deadlines/check",
            "/api/v1/monitor/status",
            "/api/v1/emails/{email_id}/reminders",
            "/api/v1/reminders",
            "/api/v1/notifications",
            "/api/v1/notifications/{id}",
        ],
    }


@app.post("/intake/gmail")
def intake_gmail(raw_message: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Normalize a raw Gmail API message resource (development / testing).

    Request body: the JSON from ``users.messages.get`` (``format=full``).
    Response: the Agent Output envelope; ``data`` is the normalized email.
    """
    return _intake_agent.run(raw_message).to_wire()
