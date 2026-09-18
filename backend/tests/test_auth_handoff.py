"""Phase 17 — browser→app OAuth handoff + one-time session exchange.

No real Google: ``exchange_code`` is faked. Covers the deep-link redirect, the
single-use / expiry / replay / state-binding properties of the handoff code, and
that credentials + tokens never appear in the callback URL.
"""

from __future__ import annotations

from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_settings
from app.core.config import Settings
from app.db import session as db_session
from app.main import app
from app.services import gmail_auth_service as gas
from app.services.auth_handoff_service import AuthHandoffService, _hash
from app.services.gmail_auth_service import GoogleExchange
from app.services.gmail_sync_service import GmailSyncService
from app.services.google_identity import GoogleIdentity
from tests.auth_helpers import auth_headers

client = TestClient(app)


@pytest.fixture
def google_flow(monkeypatch):
    """Drop the auto-login override, fake the Google code exchange."""
    from app.api.deps import get_auth_service, get_current_user

    saved = {k: app.dependency_overrides.pop(k, None)
             for k in (get_current_user, get_auth_service)}

    cfg = Settings(
        app_env="development",
        google_client_id="cid.apps.googleusercontent.com",
        google_client_secret="secret",
        google_redirect_uri="https://amar-api.example.com/api/v1/auth/google/callback",
        auth_handoff_ttl_seconds=120,
    )
    app.dependency_overrides[get_settings] = lambda: cfg

    def _identity(sub="sub-1", email="pat@gmail.com", name="Pat"):
        return GoogleIdentity(sub=sub, email=email, name=name)

    holder = {"identity": _identity()}

    def fake_exchange(self, *, code=None, error=None, state=None, authorization_response=None):
        if code == "DENY":
            from app.core.errors import OAuthAccessDeniedError
            raise OAuthAccessDeniedError("denied")
        return GoogleExchange(
            identity=holder["identity"],
            credentials=SimpleNamespace(
                token="acc", refresh_token="ref", token_uri="u",
                client_id="c", client_secret="s", scopes=["x"], expiry=None),
            account_email=holder["identity"].email, blob={},
        )

    monkeypatch.setattr(gas.GmailAuthService, "exchange_code", fake_exchange)
    monkeypatch.setattr(GmailSyncService, "ensure_baseline", lambda self, *a, **k: None)
    monkeypatch.setattr(gas.GmailAuthService, "get_credentials",
                        lambda self, account_id=None, **k: None)
    yield holder
    app.dependency_overrides.pop(get_settings, None)
    for k, v in saved.items():
        if v is not None:
            app.dependency_overrides[k] = v


def _start() -> str:
    return client.post("/api/v1/auth/google/start").json()["flow_id"]


def _callback(flow_id: str, *, code="abc"):
    r = client.get(f"/api/v1/auth/google/callback?code={code}&state={flow_id}",
                   follow_redirects=False)
    return r


def _deep_link_params(flow_id: str, *, code="abc") -> dict:
    r = _callback(flow_id, code=code)
    assert r.status_code == 302
    return parse_qs(urlparse(r.headers["location"]).query)


# --- callback -> deep link -------------------------------------------

def test_callback_redirects_to_app_deep_link_with_a_one_time_code(google_flow):
    fid = _start()
    r = _callback(fid)
    assert r.status_code == 302
    loc = r.headers["location"]
    assert loc.startswith("agentamar://auth/callback?")
    q = parse_qs(urlparse(loc).query)
    assert q["code"][0] and q["state"][0] == fid


def test_no_token_or_credential_ever_appears_in_the_callback_url(google_flow):
    fid = _start()
    loc = _callback(fid).headers["location"]
    lower = loc.lower()
    for banned in ("session_token", "refresh_token", "access_token", "ya29",
                   "client_secret", "id_token", "bearer"):
        assert banned not in lower


def test_unknown_state_is_rejected_and_creates_no_handoff(google_flow):
    r = client.get("/api/v1/auth/google/callback?code=abc&state=never-issued",
                   follow_redirects=False)
    # bounced back to the app with an error marker, no handoff minted
    assert r.status_code == 302
    assert "error=expired_state" in r.headers["location"]
    with db_session.db_session() as s:
        from app.db.models import AuthHandoff
        assert s.query(AuthHandoff).count() == 0


def test_user_denied_consent_bounces_back_with_an_error(google_flow):
    fid = _start()
    r = _callback(fid, code="DENY")
    assert r.status_code == 302
    assert "error=signin_failed" in r.headers["location"]


# --- session exchange ------------------------------------------------

def test_exchange_succeeds_once_and_returns_a_working_bearer(google_flow):
    fid = _start()
    handoff = _deep_link_params(fid)["code"][0]

    ex = client.post("/api/v1/auth/session/exchange",
                     json={"code": handoff, "state": fid})
    assert ex.status_code == 200
    body = ex.json()
    assert body["status"] == "ready" and body["user"]["google_email"] == "pat@gmail.com"

    me = client.get("/api/v1/auth/me", headers=auth_headers(body["session_token"]))
    assert me.status_code == 200 and me.json()["user"]["google_email"] == "pat@gmail.com"


def test_handoff_code_is_single_use_replay_fails(google_flow):
    fid = _start()
    handoff = _deep_link_params(fid)["code"][0]

    first = client.post("/api/v1/auth/session/exchange", json={"code": handoff})
    assert first.status_code == 200
    replay = client.post("/api/v1/auth/session/exchange", json={"code": handoff})
    assert replay.status_code == 400 and replay.json()["error"] == "invalid_handoff"


def test_expired_handoff_code_fails(google_flow):
    fid = _start()
    handoff = _deep_link_params(fid)["code"][0]
    with db_session.db_session() as s:
        from datetime import datetime, timedelta, timezone
        from app.db.models import AuthHandoff
        row = s.query(AuthHandoff).filter(AuthHandoff.code_hash == _hash(handoff)).one()
        row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        s.commit()
    r = client.post("/api/v1/auth/session/exchange", json={"code": handoff})
    assert r.status_code == 400


def test_invalid_handoff_code_fails(google_flow):
    r = client.post("/api/v1/auth/session/exchange", json={"code": "not-a-real-code"})
    assert r.status_code == 400 and r.json()["error"] == "invalid_handoff"


def test_state_mismatch_is_rejected(google_flow):
    fid = _start()
    handoff = _deep_link_params(fid)["code"][0]
    r = client.post("/api/v1/auth/session/exchange",
                    json={"code": handoff, "state": "some-other-state"})
    assert r.status_code == 400
    # ...and the code is still usable with the correct state (mismatch didn't consume it)
    ok = client.post("/api/v1/auth/session/exchange",
                     json={"code": handoff, "state": fid})
    assert ok.status_code == 200


def test_oauth_state_validation_survives(google_flow):
    """A fabricated callback (no matching /start) cannot mint a session."""
    r = client.get("/api/v1/auth/google/callback?code=abc&state=forged",
                   follow_redirects=False)
    assert r.status_code == 302 and "error=" in r.headers["location"]
    # the forged state yields no handoff, so nothing to exchange
    r2 = client.post("/api/v1/auth/session/exchange", json={"code": "anything", "state": "forged"})
    assert r2.status_code == 400


# --- multi-account association -------------------------------------

def test_each_handoff_maps_to_its_own_google_account(google_flow):
    # user A signs in
    google_flow["identity"] = GoogleIdentity(sub="sub-A", email="a@gmail.com", name="A")
    fa = _start()
    ha = _deep_link_params(fa)["code"][0]
    ba = client.post("/api/v1/auth/session/exchange", json={"code": ha, "state": fa}).json()

    # user B signs in (different Google sub) — must not collide with A
    google_flow["identity"] = GoogleIdentity(sub="sub-B", email="b@gmail.com", name="B")
    fb = _start()
    hb = _deep_link_params(fb)["code"][0]
    bb = client.post("/api/v1/auth/session/exchange", json={"code": hb, "state": fb}).json()

    assert ba["user"]["id"] != bb["user"]["id"]
    assert client.get("/api/v1/auth/me", headers=auth_headers(ba["session_token"])).json(
        )["user"]["google_email"] == "a@gmail.com"
    assert client.get("/api/v1/auth/me", headers=auth_headers(bb["session_token"])).json(
        )["user"]["google_email"] == "b@gmail.com"

    # A's handoff can never yield B's account
    with db_session.db_session() as s:
        from app.db.models import User
        assert s.query(User).filter(User.google_sub.in_(["sub-A", "sub-B"])).count() == 2


def test_credentials_remain_server_side_after_exchange(google_flow, monkeypatch):
    monkeypatch.setattr(gas.GmailAuthService, "get_credentials",
                        lambda self, account_id=None, **k: None)
    fid = _start()
    handoff = _deep_link_params(fid)["code"][0]
    body = client.post("/api/v1/auth/session/exchange",
                       json={"code": handoff, "state": fid}).json()
    # the exchange response is only status/token/user — no google secret, no blob
    assert set(body) == {"status", "session_token", "user"}
    assert set(body["user"]) == {"id", "google_email", "display_name"}


# --- service-level unit checks -----------------------------------

def test_service_redeem_is_atomic_single_use(db):
    from tests.auth_helpers import seed_user
    uid = seed_user(sub="handoff-unit")
    svc = AuthHandoffService(db, settings=Settings(auth_handoff_ttl_seconds=120))
    code = svc.create(uid, oauth_state="st")
    db.commit()
    assert svc.redeem(code, oauth_state="st") == uid
    db.commit()
    assert svc.redeem(code, oauth_state="st") is None  # consumed
    assert svc.redeem("garbage") is None
