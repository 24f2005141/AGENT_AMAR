"""Google OAuth + application session endpoints (Phase 15 / Phase 17).

    POST /api/v1/auth/google/start        -> {authorization_url, flow_id}
    GET  /api/v1/auth/google/login        -> 307 redirect to Google (manual browser)
    GET  /api/v1/auth/google/callback     -> Google's redirect target. Completes
                                             OAuth, then (app flow) 302-redirects
                                             to the app deep link with a one-time
                                             handoff code, or (manual browser)
                                             renders a "return to the app" page.
    POST /api/v1/auth/session/exchange    -> swap a one-time handoff code for the
                                             application session token (deep-link
                                             flow — the mobile primary path)
    GET  /api/v1/auth/google/session      -> poll flow_id for the session token
                                             (manual-browser / compatibility path)
    GET  /api/v1/auth/me                  -> current user + gmail_connected  (bearer)
    POST /api/v1/auth/logout              -> revoke the current session      (bearer)
    GET  /api/v1/auth/google/status       -> current user's Gmail connection  (bearer)
    POST /api/v1/auth/google/disconnect   -> forget current user's Gmail creds (bearer)

The raw Google client secret and OAuth refresh tokens NEVER leave the server —
Flutter only ever receives the opaque application session token, and only via the
exchange endpoint (never in a URL).
"""

from __future__ import annotations

import time
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.deps import get_auth_service, get_current_user, get_db, get_settings
from app.core.config import Settings
from app.core.logging_setup import secure_logger
from app.db.models import User
from app.services.auth_handoff_service import AuthHandoffService
from app.services.auth_session_service import AuthSessionService
from app.services.gmail_auth_service import GmailAuthService
from app.services.gmail_service import GmailService
from app.services.gmail_sync_service import GmailSyncService
from app.services.push_service import PushNotificationService

logger = secure_logger("agent_amar.auth")

router = APIRouter(tags=["auth"])

# In-memory OAuth-flow store. Maps the OAuth ``state`` nonce issued by /start (or
# /login) to {ts, mode, result}. ``mode`` is "app" (deep-link handoff) or "web"
# (manual browser + /session poll). It also gives the callback CSRF protection:
# a callback whose ``state`` was never issued here is rejected. Single-instance
# only (a signed cookie / Redis replaces this when scaling out).
_PENDING: dict[str, dict] = {}
_PENDING_TTL_SECONDS = 300
_MAX_PENDING = 128


def _prune_pending() -> None:
    now = time.time()
    for key in [k for k, v in _PENDING.items() if now - v["ts"] > _PENDING_TTL_SECONDS]:
        _PENDING.pop(key, None)
    if len(_PENDING) > _MAX_PENDING:
        _PENDING.clear()


def _user_public(user: User) -> dict:
    return {
        "id": user.id,
        "google_email": user.google_email or None,
        "display_name": user.display_name,
    }


# -- start / login -----------------------------------------------------

@router.post("/api/v1/auth/google/start")
def google_start(auth: GmailAuthService = Depends(get_auth_service)) -> dict:
    """Begin the OAuth flow for the Flutter app. The app opens
    ``authorization_url`` in a secure system browser / Custom Tab; after consent
    the backend 302-redirects the browser to the app deep link with a one-time
    handoff code, which the app swaps at ``POST /api/v1/auth/session/exchange``.
    ``flow_id`` is retained only as a manual-browser polling fallback."""
    url, state = auth.build_authorization_url()
    _prune_pending()
    _PENDING[state] = {"ts": time.time(), "result": None, "mode": "app"}
    return {"authorization_url": url, "flow_id": state}


@router.get("/api/v1/auth/google/login")
def google_login(auth: GmailAuthService = Depends(get_auth_service)) -> RedirectResponse:
    """Browser-initiated OAuth (manual / dev). Redirects straight to Google; the
    callback then renders a page and the token is collected via ``/session``."""
    url, state = auth.build_authorization_url()
    _prune_pending()
    _PENDING[state] = {"ts": time.time(), "result": None, "mode": "web"}
    return RedirectResponse(url)


# -- callback --------------------------------------------------------

_CALLBACK_HTML = """<!doctype html><html><head><meta charset="utf-8">
<title>AGENT AMAR</title><style>body{{font-family:system-ui;background:#142838;
color:#e8eef2;display:flex;min-height:100vh;align-items:center;justify-content:center;
margin:0}}div{{text-align:center;max-width:22rem;padding:2rem}}h1{{font-size:1.2rem}}
</style></head><body><div><h1>{heading}</h1><p>{body}</p></div></body></html>"""


def _app_redirect(settings: Settings, *, code: str | None = None,
                  state: str | None = None, error: str | None = None) -> RedirectResponse:
    """302 the browser back to the Flutter app's deep link. Carries only the
    one-time handoff ``code`` (+ ``state``) or an ``error`` marker — never a
    session or Gmail token."""
    params = []
    if code:
        params.append(f"code={quote(code, safe='')}")
    if state:
        params.append(f"state={quote(state, safe='')}")
    if error:
        params.append(f"error={quote(error, safe='')}")
    sep = "&" if "?" in settings.app_auth_callback_url else "?"
    url = settings.app_auth_callback_url + (sep + "&".join(params) if params else "")
    return RedirectResponse(url, status_code=302)


@router.get("/api/v1/auth/google/callback")
def google_callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    auth: GmailAuthService = Depends(get_auth_service),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Google's redirect target. Validates the OAuth ``state`` (CSRF), exchanges
    the code, upserts the user, stores that user's Gmail credentials, then:

      * **app flow** (``/start``) — mints a single-use handoff code and 302s to
        the app deep link so the user returns automatically.
      * **manual browser** (``/login``) — mints a session, parks it for
        ``/session`` polling, and renders a "return to the app" page.
    """
    _prune_pending()

    # CSRF / session-fixation: the callback's state MUST be one we issued.
    entry = _PENDING.get(state) if state else None
    mode = (entry or {}).get("mode", "web")
    if entry is None:
        logger.warning("google callback with an unknown/expired state")
        if state:
            # keep the app UX clean — bounce back with an error marker
            return _app_redirect(settings, error="expired_state")
        return HTMLResponse(
            _CALLBACK_HTML.format(
                heading="Sign-in link expired",
                body="Please return to AGENT AMAR and start sign-in again.",
            ),
            status_code=400,
        )

    try:
        exchange = auth.exchange_code(code=code, error=error, state=state)
        if exchange.identity is None:
            raise ValueError("no id_token")
    except Exception as exc:  # noqa: BLE001 — friendly page, never a stack trace
        logger.warning("google callback failed: %s", type(exc).__name__)
        if mode == "app":
            return _app_redirect(settings, error="signin_failed")
        return HTMLResponse(
            _CALLBACK_HTML.format(
                heading="Sign-in failed",
                body="Please return to AGENT AMAR and try connecting again.",
            ),
            status_code=400,
        )

    sessions = AuthSessionService(db)
    user = sessions.upsert_user(exchange.identity)
    db.flush()
    # associate THIS Google account's credentials with THIS user only (multi-user
    # safe: keyed by the resolved app user id, never overwriting another user).
    auth.persist_credentials(
        exchange.credentials,
        account_id=str(user.id),
        account_email=exchange.account_email,
    )

    # Phase 12: (re)anchor the per-user monitoring baseline to *now* so neither
    # the historical inbox nor mail that arrived while disconnected is ingested.
    # ``force=True`` — connecting/reconnecting always means "watch from here on".
    try:
        creds = auth.get_credentials(account_id=str(user.id))
        if creds is not None:
            GmailSyncService(db, user_pk=user.id).ensure_baseline(
                GmailService(credentials=creds),
                account_email=exchange.account_email,
                force=True,
            )
    except Exception:  # noqa: BLE001
        logger.warning("gmail baseline after connect failed; will baseline lazily")

    if mode == "app":
        handoff = AuthHandoffService(db).create(user.id, oauth_state=state)
        db.commit()
        _PENDING.pop(state, None)  # the flow is done; no polling fallback needed
        return _app_redirect(settings, code=handoff, state=state)

    # manual-browser path: mint the session now, park it for /session.
    raw_token, _ = sessions.create_session(user)
    db.commit()
    _PENDING[state] = {
        "ts": time.time(),
        "mode": "web",
        "result": {"session_token": raw_token, "user": _user_public(user)},
    }
    return HTMLResponse(
        _CALLBACK_HTML.format(
            heading="You're signed in",
            body="You can close this tab and return to AGENT AMAR.",
        )
    )


class SessionExchangeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=1, max_length=512)
    state: str | None = Field(default=None, max_length=128)


@router.post("/api/v1/auth/session/exchange")
def session_exchange(
    body: SessionExchangeIn, db: Session = Depends(get_db)
) -> JSONResponse:
    """Swap a one-time browser→app handoff code for the application session
    token (deep-link flow). Validates + atomically consumes the code, then mints
    the session via the normal :class:`AuthSessionService` (opaque bearer,
    hashed in ``app_sessions``). Replay / expired / unknown / wrong-state → 400.
    """
    handoffs = AuthHandoffService(db)
    user_pk = handoffs.redeem(body.code, oauth_state=body.state)
    if user_pk is None:
        db.commit()  # persist the _prune() housekeeping
        return JSONResponse(
            {
                "error": "invalid_handoff",
                "detail": "This sign-in code is invalid, has expired, or was already used.",
            },
            status_code=400,
        )
    sessions = AuthSessionService(db)
    user = sessions.get_user(user_pk)
    if user is None:
        db.commit()
        return JSONResponse(
            {"error": "invalid_handoff", "detail": "Sign-in could not be completed."},
            status_code=400,
        )
    raw_token, _ = sessions.create_session(user)
    db.commit()
    return JSONResponse(
        {"status": "ready", "session_token": raw_token, "user": _user_public(user)}
    )


@router.get("/api/v1/auth/google/session")
def google_session(flow_id: str = Query(...)) -> JSONResponse:
    """Poll a ``flow_id`` (from ``/login``, or ``/start`` as a fallback) for the
    session token, once. The mobile app uses the deep-link exchange instead."""
    _prune_pending()
    entry = _PENDING.get(flow_id)
    if entry is None:
        return JSONResponse({"status": "expired"}, status_code=410)
    if entry["result"] is None:
        return JSONResponse({"status": "pending"}, status_code=202)
    _PENDING.pop(flow_id, None)  # one-time
    return JSONResponse({"status": "ready", **entry["result"]})


# -- session-scoped -------------------------------------------------

@router.get("/api/v1/auth/me")
def auth_me(
    user: User = Depends(get_current_user),
    auth: GmailAuthService = Depends(get_auth_service),
) -> dict:
    info = auth.connection_info(account_id=str(user.id))
    return {"user": _user_public(user), "gmail_connected": bool(info["connected"])}


class LogoutIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fcm_token: str | None = None


@router.post("/api/v1/auth/logout")
def auth_logout(
    body: LogoutIn | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    # revoke every active session for this user (simplest + safest for a phone app)
    AuthSessionService(db).revoke_all_for_user(user.id)
    # Phase 16: stop pushing to the device the user just logged out of.
    if body is not None and body.fcm_token:
        PushNotificationService(db).unregister_device(body.fcm_token)
    db.commit()
    return {"status": "logged_out"}


@router.get("/api/v1/auth/google/status")
def google_status(
    user: User = Depends(get_current_user),
    auth: GmailAuthService = Depends(get_auth_service),
) -> dict:
    """Current user's Gmail connection state. Never exposes tokens."""
    return auth.connection_info(account_id=str(user.id))


@router.post("/api/v1/auth/google/disconnect")
def google_disconnect(
    user: User = Depends(get_current_user),
    auth: GmailAuthService = Depends(get_auth_service),
) -> dict:
    """Forget the current user's Gmail credentials. App data + session are kept."""
    auth.disconnect(account_id=str(user.id))
    return {"status": "disconnected", "provider": "gmail"}
