"""Google OAuth 2.0 for Gmail (authorization-code flow).

Responsibilities:
    1. Build the Google consent-screen URL.
    2. Exchange the authorization code for credentials.
    3. Persist credentials via a :class:`~app.services.token_store.TokenStore`.
    4. Load + refresh credentials on demand.
    5. Hand an authorized ``google.oauth2.credentials.Credentials`` object to
       :class:`~app.services.gmail_service.GmailService`.

This module is intentionally free of FastAPI imports so the OAuth logic stays
testable and reusable.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from google.auth.exceptions import GoogleAuthError, RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

from app.core.config import Settings
from app.core.errors import (
    OAuthAccessDeniedError,
    OAuthConfigError,
    OAuthExchangeError,
    TokenRefreshError,
)
from app.core.logging_setup import secure_logger
from app.core.sanitization import mask_sensitive
from app.services.google_identity import GoogleIdentity, GoogleIdentityError, decode_id_token
from app.services.token_store import DEFAULT_ACCOUNT, TokenStore

# --- OAuth scopes ---------------------------------------------------------
#
# Phase 15: sign-in with Google needs identity scopes (openid / email / profile)
# so we can key the application user by the stable Google ``sub``.
#
# Gmail access is two narrow scopes, nothing wider:
#   * gmail.readonly — read message bodies; NO modify / delete / settings.
#   * gmail.send     — send a message the user explicitly approved (the AI
#                      reply feature). It CANNOT read, modify or delete mail.
# Together they still cannot touch labels, filters, or account settings.
_GMAIL_READONLY = "https://www.googleapis.com/auth/gmail.readonly"
_GMAIL_SEND = "https://www.googleapis.com/auth/gmail.send"
#: Public so the send path can verify a stored credential actually carries it
#: (a user connected before this scope existed must reconnect once).
GMAIL_SEND_SCOPE = _GMAIL_SEND
OAUTH_SCOPES: list[str] = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    _GMAIL_READONLY,
    _GMAIL_SEND,
]
# Back-compat alias (older imports / the Gmail scope check).
GMAIL_SCOPES: list[str] = OAUTH_SCOPES


@dataclass(frozen=True)
class GoogleExchange:
    """Result of a successful OAuth code exchange (not yet persisted)."""

    identity: GoogleIdentity | None
    credentials: Credentials
    account_email: str | None
    blob: dict[str, Any]

_GOOGLE_AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
_GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"

# Google frequently returns a superset of the requested scopes (e.g. it adds
# openid). Without this, oauthlib raises "Scope has changed".
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

logger = secure_logger("agent_amar.gmail_auth")


class GmailAuthService:
    """Owns the OAuth flow and the credential lifecycle."""

    def __init__(self, settings: Settings, token_store: TokenStore) -> None:
        self.settings = settings
        self.token_store = token_store
        # Localhost redirect URIs are http://; the OAuth libs refuse non-HTTPS
        # callbacks unless this is set. Only relax it in development.
        if settings.app_env == "development":
            os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")

    # -- flow construction ----------------------------------------------

    def _client_config(self) -> dict[str, Any]:
        if not self.settings.oauth_configured:
            raise OAuthConfigError()
        return {
            "web": {
                "client_id": self.settings.google_client_id,
                "client_secret": self.settings.google_client_secret,
                "auth_uri": _GOOGLE_AUTH_URI,
                "token_uri": _GOOGLE_TOKEN_URI,
                "redirect_uris": [self.settings.google_redirect_uri_resolved],
            }
        }

    def _build_flow(self, state: str | None = None) -> Flow:
        # PKCE is disabled: login and callback are separate requests with
        # separate Flow objects, so the auto-generated code_verifier from the
        # login step would not survive to the token exchange ("Missing code
        # verifier"). This is a confidential web client (it has a client
        # secret), so PKCE is not required. To re-enable it, persist the
        # verifier alongside `state`.
        return Flow.from_client_config(
            self._client_config(),
            scopes=OAUTH_SCOPES,
            redirect_uri=self.settings.google_redirect_uri_resolved,
            state=state,
            autogenerate_code_verifier=False,
        )

    # -- step 1: authorization URL ------------------------------------

    def build_authorization_url(self) -> tuple[str, str]:
        """Return ``(authorization_url, state)`` for the consent screen."""
        flow = self._build_flow()
        url, state = flow.authorization_url(
            access_type="offline",          # request a refresh token
            include_granted_scopes="true",
            prompt="consent",               # force refresh_token on every connect
        )
        return url, state

    # -- step 2: code -> credentials --------------------------------

    def exchange_code(
        self,
        *,
        code: str | None = None,
        error: str | None = None,
        state: str | None = None,
        authorization_response: str | None = None,
    ) -> GoogleExchange:
        """Exchange an authorization code for credentials + the Google identity.

        Does **not** persist — the caller keys the token store by the resolved
        application user id (Phase 15). Raises an ``OAuth*Error`` on failure.
        """
        if error:
            raise OAuthAccessDeniedError(f"Google returned: {error}")
        if not code and not authorization_response:
            raise OAuthExchangeError("No authorization code in the callback.")

        flow = self._build_flow(state=state)
        try:
            if authorization_response:
                flow.fetch_token(authorization_response=authorization_response)
            else:
                flow.fetch_token(code=code)
        except (GoogleAuthError, Exception) as exc:  # oauthlib raises bare exceptions
            # The exception text can echo the auth code / a token — mask it
            # before it reaches logs or (in dev) the API response (Phase 14).
            safe_reason = mask_sensitive(str(exc))
            logger.warning("OAuth code exchange failed: %s: %s", type(exc).__name__, safe_reason)
            detail = None
            if self.settings.app_env == "development":
                # The developer's own misconfiguration — surface the (masked) reason.
                detail = f"Google rejected the sign-in ({type(exc).__name__}: {safe_reason})"
            raise OAuthExchangeError(detail) from exc

        creds: Credentials = flow.credentials
        identity: GoogleIdentity | None = None
        try:
            identity = decode_id_token(
                getattr(creds, "id_token", None), client_id=self.settings.google_client_id
            )
        except GoogleIdentityError as exc:
            logger.warning("could not decode Google id_token: %s", exc)
        account_email = (identity.email if identity else None) or self._safe_lookup_email(creds)
        blob = _credentials_to_blob(creds)
        if account_email:
            blob["account_email"] = account_email
        return GoogleExchange(
            identity=identity, credentials=creds, account_email=account_email, blob=blob
        )

    def persist_credentials(
        self,
        creds: Credentials,
        *,
        account_id: str,
        account_email: str | None = None,
    ) -> None:
        """Store (or replace) a user's credential blob in the token store."""
        self._persist(creds, account_id=account_id, account_email=account_email)

    # -- step 3+4: load / refresh ----------------------------------

    def get_credentials(self, account_id: str = DEFAULT_ACCOUNT) -> Credentials | None:
        """Return valid credentials for ``account_id``, refreshing if needed.

        Returns ``None`` when the account was never connected. Raises
        :class:`TokenRefreshError` when a stored token cannot be revalidated.
        """
        blob = self.token_store.get(account_id)
        if not blob:
            return None

        try:
            creds = Credentials.from_authorized_user_info(
                blob, scopes=blob.get("scopes") or OAUTH_SCOPES
            )
        except (ValueError, KeyError) as exc:
            raise TokenRefreshError("Stored credentials are unreadable.") from exc

        if creds.valid:
            return creds

        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except RefreshError as exc:
                raise TokenRefreshError() from exc
            self._persist(
                creds, account_id=account_id, account_email=blob.get("account_email")
            )
            return creds

        raise TokenRefreshError()

    # -- status / disconnect ----------------------------------------

    def connection_info(self, account_id: str = DEFAULT_ACCOUNT) -> dict[str, Any]:
        """Non-raising connection summary for the status endpoint."""
        blob = self.token_store.get(account_id)
        connected = bool(blob and blob.get("refresh_token"))
        stored_scopes = list((blob or {}).get("scopes") or [])
        if connected:
            # report what was actually granted (a pre-send-scope connection
            # still only lists gmail.readonly until the user reconnects)
            scopes = [s for s in (_GMAIL_READONLY, _GMAIL_SEND) if s in stored_scopes] or [_GMAIL_READONLY]
        else:
            scopes = []
        return {
            "connected": connected,
            "provider": "gmail",
            "account_email": (blob or {}).get("account_email"),
            "scopes": scopes,
        }

    def disconnect(self, account_id: str = DEFAULT_ACCOUNT) -> None:
        """Forget the stored credentials for ``account_id``."""
        self.token_store.delete(account_id)

    # -- internals -------------------------------------------------

    def _persist(
        self,
        creds: Credentials,
        *,
        account_id: str,
        account_email: str | None,
    ) -> None:
        blob = _credentials_to_blob(creds)
        if account_email:
            blob["account_email"] = account_email
        self.token_store.put(blob, account_id=account_id)

    @staticmethod
    def _safe_lookup_email(creds: Credentials) -> str | None:
        """Best-effort: read the connected address via the Gmail profile."""
        try:
            from googleapiclient.discovery import build

            service = build("gmail", "v1", credentials=creds, cache_discovery=False)
            profile = service.users().getProfile(userId="me").execute()
            return profile.get("emailAddress")
        except Exception:
            return None


def _credentials_to_blob(creds: Credentials) -> dict[str, Any]:
    """Serialise credentials to a plain dict for the token store."""
    return {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes or OAUTH_SCOPES),
        "expiry": creds.expiry.isoformat() if creds.expiry else None,
    }
