"""Tests for GmailAuthService — OAuth flow + credential lifecycle (STEP 8.1-8.3).

No real Google network calls: URL building is offline, and refresh is patched.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials

from app.core.config import Settings
from app.core.errors import (
    OAuthAccessDeniedError,
    OAuthConfigError,
    TokenRefreshError,
)
from app.services.gmail_auth_service import GMAIL_SCOPES, OAUTH_SCOPES, GmailAuthService
from app.services.token_store import InMemoryTokenStore


def _settings(**over) -> Settings:
    base = dict(
        app_env="development",
        google_client_id="test-client-id.apps.googleusercontent.com",
        google_client_secret="test-secret",
        google_redirect_uri="http://localhost:8000/api/v1/auth/google/callback",
    )
    base.update(over)
    return Settings(**base)


@pytest.fixture
def auth() -> GmailAuthService:
    return GmailAuthService(_settings(), InMemoryTokenStore())


def _blob(expiry: datetime) -> dict:
    return {
        "token": "access-old",
        "refresh_token": "refresh-xyz",
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": "test-client-id.apps.googleusercontent.com",
        "client_secret": "test-secret",
        "scopes": GMAIL_SCOPES,
        "expiry": expiry.replace(microsecond=0).isoformat(),
        "account_email": "person@gmail.com",
    }


# --- scope --------------------------------------------------------------------

def test_gmail_scopes_are_readonly_and_send_only():
    # Identity scopes for "Sign in with Google" + exactly two narrow Gmail
    # scopes: read message bodies, and send a user-approved reply. NOTHING
    # wider — no modify / delete / labels / filters / settings.
    gmail_scopes = sorted(s for s in OAUTH_SCOPES if "gmail" in s)
    assert gmail_scopes == [
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.send",
    ]
    for forbidden in ("gmail.modify", "gmail.compose", "mail.google.com", "gmail.settings"):
        assert not any(forbidden in s for s in OAUTH_SCOPES)
    assert "openid" in OAUTH_SCOPES
    assert GMAIL_SCOPES is OAUTH_SCOPES


# --- authorization URL ------------------------------------------------------

def test_build_authorization_url(auth: GmailAuthService):
    url, state = auth.build_authorization_url()
    assert url.startswith("https://accounts.google.com/o/oauth2/auth")
    assert "test-client-id.apps.googleusercontent.com" in url
    assert "gmail.readonly" in url
    assert "access_type=offline" in url
    assert "localhost%3A8000" in url  # the explicitly-configured redirect_uri
    assert state and isinstance(state, str)


def test_authorization_url_requires_config():
    svc = GmailAuthService(Settings(), InMemoryTokenStore())  # no client id/secret
    with pytest.raises(OAuthConfigError):
        svc.build_authorization_url()


# --- redirect URI resolution (config-driven, tunnel-ready) -----------------

def test_redirect_uri_resolution_precedence():
    creds = dict(google_client_id="c", google_client_secret="s")

    # 1. explicit GOOGLE_REDIRECT_URI wins (back-compat)
    s = Settings(**creds, google_redirect_uri="https://explicit.example.com/api/v1/auth/google/callback",
                 api_public_base_url="https://ignored.example.com")
    assert s.google_redirect_uri_resolved == "https://explicit.example.com/api/v1/auth/google/callback"

    # 2. derived from API_PUBLIC_BASE_URL when the explicit one is blank
    s = Settings(**creds, api_public_base_url="https://amar-api.example.com")
    assert s.google_redirect_uri_resolved == "https://amar-api.example.com/api/v1/auth/google/callback"

    # 3. trailing slash on API_PUBLIC_BASE_URL -> no "//"
    s = Settings(**creds, api_public_base_url="https://amar-api.example.com/")
    assert s.google_redirect_uri_resolved == "https://amar-api.example.com/api/v1/auth/google/callback"
    assert "//api/v1" not in s.google_redirect_uri_resolved

    # 4. nothing set -> localhost dev default (only used when co-located)
    s = Settings(**creds)
    assert s.google_redirect_uri_resolved == "http://localhost:8000/api/v1/auth/google/callback"


def test_authorization_url_uses_https_public_base_url_not_localhost():
    svc = GmailAuthService(
        _settings(google_redirect_uri="", api_public_base_url="https://amar-api.example.com"),
        InMemoryTokenStore(),
    )
    url, _ = svc.build_authorization_url()
    assert "amar-api.example.com%2Fapi%2Fv1%2Fauth%2Fgoogle%2Fcallback" in url
    assert "localhost" not in url
    assert "192.168." not in url


def test_token_exchange_flow_uses_the_same_resolved_redirect_uri(monkeypatch):
    """The redirect_uri in the code->token exchange must match the one in the
    consent URL — both come from google_redirect_uri_resolved."""
    svc = GmailAuthService(
        _settings(google_redirect_uri="", api_public_base_url="https://amar-api.example.com"),
        InMemoryTokenStore(),
    )
    seen = {}

    class _FakeFlow:
        credentials = None

        def fetch_token(self, **kw):
            seen["kw"] = kw

        def authorization_url(self, **kw):
            return "https://accounts.google.com/x", "state-x"

    def _fake_build_flow(self, state=None):
        seen["redirect_uri"] = self.settings.google_redirect_uri_resolved
        return _FakeFlow()

    monkeypatch.setattr(GmailAuthService, "_build_flow", _fake_build_flow)
    try:
        svc.exchange_code(code="abc", state="state-x")
    except Exception:
        pass  # _FakeFlow.credentials is None -> downstream fails, we only check the URI
    assert seen["redirect_uri"] == "https://amar-api.example.com/api/v1/auth/google/callback"


# --- callback errors ----------------------------------------------------

def test_exchange_code_user_denied(auth: GmailAuthService):
    with pytest.raises(OAuthAccessDeniedError):
        auth.exchange_code(error="access_denied")


def test_exchange_code_missing_code(auth: GmailAuthService):
    from app.core.errors import OAuthExchangeError

    with pytest.raises(OAuthExchangeError):
        auth.exchange_code(code=None)


# --- credential lifecycle --------------------------------------------

def test_get_credentials_none_when_not_connected(auth: GmailAuthService):
    assert auth.get_credentials() is None
    info = auth.connection_info()
    assert info == {
        "connected": False,
        "provider": "gmail",
        "account_email": None,
        "scopes": [],
    }


def test_get_credentials_valid_token_returned(auth: GmailAuthService):
    auth.token_store.put(_blob(datetime.utcnow() + timedelta(hours=1)))
    creds = auth.get_credentials()
    assert isinstance(creds, Credentials)
    assert creds.token == "access-old"
    assert creds.valid


def test_connection_info_when_connected(auth: GmailAuthService):
    auth.token_store.put(_blob(datetime.utcnow() + timedelta(hours=1)))
    info = auth.connection_info()
    assert info["connected"] is True
    assert info["account_email"] == "person@gmail.com"
    assert info["provider"] == "gmail"


def test_expired_token_is_refreshed_and_persisted(auth: GmailAuthService, monkeypatch):
    auth.token_store.put(_blob(datetime.utcnow() - timedelta(hours=1)))

    def fake_refresh(self, request):
        self.token = "access-new"
        self.expiry = datetime.utcnow() + timedelta(hours=1)

    monkeypatch.setattr(Credentials, "refresh", fake_refresh)

    creds = auth.get_credentials()
    assert creds.token == "access-new"
    # persisted
    assert auth.token_store.get()["token"] == "access-new"
    assert auth.token_store.get()["account_email"] == "person@gmail.com"


def test_refresh_failure_raises_token_refresh_error(auth: GmailAuthService, monkeypatch):
    auth.token_store.put(_blob(datetime.utcnow() - timedelta(hours=1)))

    def boom(self, request):
        raise RefreshError("invalid_grant")

    monkeypatch.setattr(Credentials, "refresh", boom)

    with pytest.raises(TokenRefreshError):
        auth.get_credentials()


def test_disconnect(auth: GmailAuthService):
    auth.token_store.put(_blob(datetime.utcnow() + timedelta(hours=1)))
    auth.disconnect()
    assert auth.connection_info()["connected"] is False
