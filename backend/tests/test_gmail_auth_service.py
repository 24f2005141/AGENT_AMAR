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
from app.services.gmail_auth_service import GMAIL_SCOPES, GmailAuthService
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

def test_scope_is_readonly_only():
    assert GMAIL_SCOPES == ["https://www.googleapis.com/auth/gmail.readonly"]


# --- authorization URL ------------------------------------------------------

def test_build_authorization_url(auth: GmailAuthService):
    url, state = auth.build_authorization_url()
    assert url.startswith("https://accounts.google.com/o/oauth2/auth")
    assert "test-client-id.apps.googleusercontent.com" in url
    assert "gmail.readonly" in url
    assert "access_type=offline" in url
    assert "localhost%3A8000" in url  # redirect_uri, url-encoded
    assert state and isinstance(state, str)


def test_authorization_url_requires_config():
    svc = GmailAuthService(Settings(), InMemoryTokenStore())  # no client id/secret
    with pytest.raises(OAuthConfigError):
        svc.build_authorization_url()


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
