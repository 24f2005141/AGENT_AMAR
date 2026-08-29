"""Google OAuth endpoints.

    GET /api/v1/auth/google/login     -> redirect to Google's consent screen
    GET /api/v1/auth/google/callback  -> exchange code, store credentials
    GET /api/v1/auth/google/status    -> {"connected": bool, "provider": "gmail"}
    POST /api/v1/auth/google/disconnect -> forget stored credentials (dev helper)
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.api.deps import get_auth_service, get_db
from app.services.gmail_auth_service import GmailAuthService
from app.services.gmail_service import GmailService
from app.services.gmail_sync_service import GmailSyncService

logger = logging.getLogger("agent_amar.auth")

router = APIRouter(prefix="/api/v1/auth/google", tags=["auth"])

# Development-only, in-memory CSRF-state cache. A DB / signed cookie replaces
# this in production.
_ISSUED_STATES: set[str] = set()
_MAX_STATES = 64


@router.get("/login")
def google_login(auth: GmailAuthService = Depends(get_auth_service)) -> RedirectResponse:
    """Start the OAuth flow: redirect the browser to Google."""
    url, state = auth.build_authorization_url()
    if len(_ISSUED_STATES) >= _MAX_STATES:
        _ISSUED_STATES.clear()
    _ISSUED_STATES.add(state)
    return RedirectResponse(url)


@router.get("/callback")
def google_callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    auth: GmailAuthService = Depends(get_auth_service),
    db: Session = Depends(get_db),
) -> dict:
    """Handle Google's redirect: exchange the code and store credentials."""
    # State check is best-effort in development (missing state != fatal).
    if state and _ISSUED_STATES:
        _ISSUED_STATES.discard(state)

    info = auth.exchange_code(code=code, error=error, state=state)

    # Phase 12: record the monitoring baseline (current mailbox historyId) so the
    # historical unread inbox is NOT ingested. Best-effort — a hiccup here must
    # never fail the connect; the first sync will baseline lazily otherwise.
    try:
        credentials = auth.get_credentials()
        if credentials is not None:
            GmailSyncService(db).ensure_baseline(
                GmailService(credentials=credentials),
                account_email=info.get("account_email"),
            )
    except Exception:  # noqa: BLE001
        logger.warning("gmail sync baseline after connect failed; will baseline lazily")

    return {
        "status": "connected",
        "message": "Gmail connected successfully. You can close this tab.",
        **info,
    }


@router.get("/status")
def google_status(auth: GmailAuthService = Depends(get_auth_service)) -> dict:
    """Report whether Gmail is connected. Never exposes tokens."""
    return auth.connection_info()


@router.post("/disconnect")
def google_disconnect(auth: GmailAuthService = Depends(get_auth_service)) -> dict:
    """Development helper: delete the stored credentials."""
    auth.disconnect()
    return {"status": "disconnected", "provider": "gmail"}
