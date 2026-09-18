"""Phase 15 — multi-user Google login & per-user data isolation."""

from __future__ import annotations

from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_auth_service, get_current_user, get_settings
from app.core.config import Settings
from app.db import session as db_session
from app.main import app
from app.services import gmail_auth_service as gas
from app.services.auth_session_service import AuthSessionService
from app.services.gmail_auth_service import GoogleExchange
from app.services.gmail_sync_service import GmailSyncService
from app.services.google_identity import GoogleIdentity
from app.services.persistence_service import PersistenceService
from app.services.token_store import DbTokenStore
from tests.auth_helpers import auth_headers, issue_token, seed_user
from tests.persistence_helpers import decision_for, internship_email, promo_email

client = TestClient(app)


@pytest.fixture
def real_auth():
    """Drop the conftest auto-login + offline-Gmail overrides so real bearer
    tokens and the real per-user DB token store are exercised."""
    saved = {
        k: app.dependency_overrides.pop(k, None)
        for k in (get_current_user, get_auth_service)
    }
    yield
    for k, v in saved.items():
        if v is not None:
            app.dependency_overrides[k] = v
        else:
            app.dependency_overrides.pop(k, None)


def _persist(email, user_pk):
    with db_session.db_session() as db:
        return PersistenceService(db, user_pk=user_pk).persist_decision(
            email, decision_for(email)
        ).email_id


# --- authentication ---------------------------------------------------

def test_unauthenticated_request_is_401(real_auth):
    assert client.get("/api/v1/emails").status_code == 401
    assert client.get("/api/v1/notifications").status_code == 401
    assert client.post("/api/v1/gmail/sync").status_code == 401
    assert client.get("/api/v1/audit/verify").status_code == 401


def test_open_endpoints_need_no_auth(real_auth):
    assert client.get("/health").status_code == 200
    assert client.get("/").status_code == 200
    assert client.get("/api/v1/monitor/status").status_code == 200


def test_bad_bearer_is_401(real_auth):
    r = client.get("/api/v1/emails", headers={"Authorization": "Bearer not-a-real-token"})
    assert r.status_code == 401


def test_deleted_session_is_401(real_auth):
    """A token that WAS valid stops working the moment its app_sessions row is
    gone (backend DB reset / migration / manual revoke) — the exact production
    scenario behind the stale-token 401."""
    a = seed_user(sub="del-sess")
    token = issue_token(a)
    assert client.get("/api/v1/auth/me", headers=auth_headers(token)).status_code == 200

    with db_session.db_session() as s:
        from app.db.models import AppSession
        s.query(AppSession).delete()
        s.commit()

    assert client.get("/api/v1/auth/me", headers=auth_headers(token)).status_code == 401
    assert client.post("/api/v1/gmail/sync", headers=auth_headers(token)).status_code == 401
    assert client.get("/api/v1/emails", headers=auth_headers(token)).status_code == 401


def test_expired_session_is_401(real_auth):
    a = seed_user(sub="exp-sess")
    token = issue_token(a)
    with db_session.db_session() as s:
        from datetime import datetime, timedelta, timezone
        from app.db.models import AppSession
        row = s.query(AppSession).first()
        row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        s.commit()
    assert client.get("/api/v1/auth/me", headers=auth_headers(token)).status_code == 401


def test_gmail_sync_requires_auth_and_is_user_scoped(real_auth, monkeypatch):
    """POST /api/v1/gmail/sync: 401 without a session; with one, the sync +
    persistence services are built for the authenticated user only."""
    from app.api import deps
    from app.services.gmail_service import GmailService

    assert client.post("/api/v1/gmail/sync").status_code == 401  # no bypass

    a, b = seed_user(sub="gsync-a"), seed_user(sub="gsync-b")
    ta = issue_token(a)

    # give user A a (fake) connected Gmail so get_gmail_service resolves
    monkeypatch.setattr(
        "app.services.gmail_auth_service.GmailAuthService.get_credentials",
        lambda self, account_id=None, **k: object() if account_id == str(a) else None,
    )
    monkeypatch.setattr(GmailService, "__init__", lambda self, **k: None)
    seen: dict = {}
    monkeypatch.setattr(
        GmailSyncService, "sync_new_messages",
        lambda self, *ar, **k: seen.update(user_pk=self.user_pk) or
        {"status": "synced", "new_message_ids": [], "processed": 0},
    )

    r = client.post("/api/v1/gmail/sync", headers=auth_headers(ta))
    assert r.status_code == 200
    assert seen["user_pk"] == a           # scoped to the caller, never b
    assert seen["user_pk"] != b


# --- Google login flow (no real Google) ------------------------------

@pytest.fixture
def google_flow(real_auth, monkeypatch):
    cfg = Settings(
        app_env="development",
        google_client_id="cid.apps.googleusercontent.com",
        google_client_secret="secret",
        google_redirect_uri="http://localhost:8000/api/v1/auth/google/callback",
    )
    app.dependency_overrides[get_settings] = lambda: cfg

    fake_creds = SimpleNamespace(
        token="acc", refresh_token="ref", token_uri="https://oauth2.googleapis.com/token",
        client_id="cid.apps.googleusercontent.com", client_secret="secret",
        scopes=["https://www.googleapis.com/auth/gmail.readonly"], expiry=None,
    )

    def fake_exchange(self, *, code=None, error=None, state=None, authorization_response=None):
        return GoogleExchange(
            identity=GoogleIdentity(sub="google-sub-42", email="pat@gmail.com", name="Pat"),
            credentials=fake_creds, account_email="pat@gmail.com", blob={},
        )

    monkeypatch.setattr(gas.GmailAuthService, "exchange_code", fake_exchange)
    monkeypatch.setattr(GmailSyncService, "ensure_baseline", lambda self, *a, **k: None)
    # skip the post-connect baseline block entirely (no Gmail resource build)
    monkeypatch.setattr(gas.GmailAuthService, "get_credentials",
                        lambda self, account_id=None, **k: None)
    yield
    app.dependency_overrides.pop(get_settings, None)


def _callback_deep_link(flow_id: str, *, code: str = "abc"):
    """Drive Google's redirect to the backend callback and return the parsed
    ``agentamar://auth/callback`` deep link the browser is 302'd to."""
    cb = client.get(
        f"/api/v1/auth/google/callback?code={code}&state={flow_id}",
        follow_redirects=False,
    )
    assert cb.status_code == 302, cb.text
    loc = cb.headers["location"]
    assert loc.startswith("agentamar://auth/callback"), loc
    return urlparse(loc), parse_qs(urlparse(loc).query)


def _run_login(sub_email=None) -> dict:
    """The mobile primary path: /start -> browser -> backend callback 302s to the
    app deep link with a one-time code -> POST /session/exchange -> session."""
    start = client.post("/api/v1/auth/google/start").json()
    assert start["authorization_url"].startswith("https://accounts.google.com/")
    flow_id = start["flow_id"]
    _, params = _callback_deep_link(flow_id)
    handoff = params["code"][0]
    ex = client.post(
        "/api/v1/auth/session/exchange", json={"code": handoff, "state": flow_id}
    )
    assert ex.status_code == 200, ex.text
    return ex.json()


def test_new_google_user_is_created_and_gets_a_session(google_flow):
    body = _run_login()
    token = body["session_token"]
    assert body["user"]["google_email"] == "pat@gmail.com"

    me = client.get("/api/v1/auth/me", headers=auth_headers(token))
    assert me.status_code == 200
    assert me.json()["user"]["google_email"] == "pat@gmail.com"

    # the session token is single-use to collect, and now works as a bearer
    again = client.get(f"/api/v1/auth/google/session?flow_id=nope")
    assert again.status_code == 410


def test_returning_user_reuses_the_same_row(google_flow, monkeypatch):
    _run_login()
    with db_session.db_session() as db:
        from app.db.models import User
        n_before = db.query(User).count()

    # second sign-in, same sub, different email
    def fake_exchange2(self, **kw):
        return GoogleExchange(
            identity=GoogleIdentity(sub="google-sub-42", email="pat.new@gmail.com", name="Pat"),
            credentials=SimpleNamespace(token="a", refresh_token="r",
                                        token_uri="u", client_id="c", client_secret="s",
                                        scopes=["x"], expiry=None),
            account_email="pat.new@gmail.com", blob={},
        )

    monkeypatch.setattr(gas.GmailAuthService, "exchange_code", fake_exchange2)
    body = _run_login()
    assert body["user"]["google_email"] == "pat.new@gmail.com"
    with db_session.db_session() as db:
        from app.db.models import User
        assert db.query(User).count() == n_before  # updated, not duplicated


def test_logout_invalidates_the_session(google_flow):
    token = _run_login()["session_token"]
    assert client.get("/api/v1/auth/me", headers=auth_headers(token)).status_code == 200
    assert client.post("/api/v1/auth/logout", headers=auth_headers(token)).status_code == 200
    assert client.get("/api/v1/auth/me", headers=auth_headers(token)).status_code == 401


def test_login_flow_works_behind_an_https_public_base_url(real_auth, monkeypatch):
    """The exact production case: FastAPI behind an HTTPS tunnel, no explicit
    GOOGLE_REDIRECT_URI — the consent URL + session polling still complete."""
    cfg = Settings(
        app_env="development",
        google_client_id="cid.apps.googleusercontent.com", google_client_secret="secret",
        google_redirect_uri="",  # blank -> derived from api_public_base_url
        api_public_base_url="https://amar-api.example.com",
    )
    app.dependency_overrides[get_settings] = lambda: cfg
    monkeypatch.setattr(
        gas.GmailAuthService, "exchange_code",
        lambda self, **kw: GoogleExchange(
            identity=GoogleIdentity(sub="tunnel-sub", email="t@gmail.com", name="T"),
            credentials=SimpleNamespace(token="a", refresh_token="r", token_uri="u",
                                        client_id="c", client_secret="s", scopes=["x"], expiry=None),
            account_email="t@gmail.com", blob={}),
    )
    monkeypatch.setattr(GmailSyncService, "ensure_baseline", lambda self, *a, **k: None)
    monkeypatch.setattr(gas.GmailAuthService, "get_credentials",
                        lambda self, account_id=None, **k: None)
    try:
        start = client.post("/api/v1/auth/google/start").json()
        assert "amar-api.example.com%2Fapi%2Fv1%2Fauth%2Fgoogle%2Fcallback" in start["authorization_url"]
        assert "localhost" not in start["authorization_url"]
        fid = start["flow_id"]
        _, params = _callback_deep_link(fid)
        ex = client.post("/api/v1/auth/session/exchange",
                         json={"code": params["code"][0], "state": fid}).json()
        assert ex["status"] == "ready"
        me = client.get("/api/v1/auth/me", headers=auth_headers(ex["session_token"]))
        assert me.status_code == 200 and me.json()["user"]["google_email"] == "t@gmail.com"
    finally:
        app.dependency_overrides.pop(get_settings, None)


# --- per-user data isolation ---------------------------------------

def test_two_users_have_disjoint_inboxes(real_auth):
    a, b = seed_user(sub="iso-a"), seed_user(sub="iso-b")
    ta, tb = issue_token(a), issue_token(b)
    ea = _persist(internship_email("gmail_a_1"), a)
    eb = _persist(promo_email("gmail_b_1"), b)

    la = client.get("/api/v1/emails", headers=auth_headers(ta)).json()
    lb = client.get("/api/v1/emails", headers=auth_headers(tb)).json()
    assert [e["email_id"] for e in la] == [ea]
    assert [e["email_id"] for e in lb] == [eb]

    # A cannot read B's email by id
    assert client.get(f"/api/v1/emails/{eb}", headers=auth_headers(ta)).status_code == 404
    assert client.get(f"/api/v1/emails/{ea}", headers=auth_headers(tb)).status_code == 404


def test_user_cannot_touch_another_users_reminder(real_auth):
    a, b = seed_user(sub="rem-a"), seed_user(sub="rem-b")
    ta, tb = issue_token(a), issue_token(b)
    ea = _persist(internship_email("gmail_ra_1"), a)

    from datetime import datetime, timedelta, timezone
    at = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    made = client.post(f"/api/v1/emails/{ea}/reminders", json={"reminder_at": at},
                       headers=auth_headers(ta))
    assert made.status_code == 201
    rid = made.json()["id"]

    # B cannot see or cancel it
    assert client.get(f"/api/v1/emails/{ea}/reminders", headers=auth_headers(tb)).status_code == 404
    assert client.delete(f"/api/v1/emails/{ea}/reminders/{rid}",
                         headers=auth_headers(tb)).status_code == 404
    # A still can
    assert client.delete(f"/api/v1/emails/{ea}/reminders/{rid}",
                         headers=auth_headers(ta)).status_code == 200


def test_audit_events_are_user_scoped(real_auth):
    a, b = seed_user(sub="aud-a"), seed_user(sub="aud-b")
    ta, tb = issue_token(a), issue_token(b)
    _persist(internship_email("gmail_aud_a"), a)

    ea = client.get("/api/v1/audit/events", headers=auth_headers(ta)).json()
    eb = client.get("/api/v1/audit/events", headers=auth_headers(tb)).json()
    assert ea["total"] >= 2 and eb["total"] == 0
    # the whole-chain integrity check still validates across users
    assert client.get("/api/v1/audit/verify", headers=auth_headers(tb)).json()["valid"] is True


# --- per-user Gmail credentials + sync state ------------------------

def test_db_token_store_is_isolated_per_user(db):
    a, b = seed_user(sub="tok-a"), seed_user(sub="tok-b")
    store = DbTokenStore(db)
    store.put({"refresh_token": "ra", "account_email": "a@gmail.com"}, account_id=str(a))
    assert store.get(str(a))["refresh_token"] == "ra"
    assert store.get(str(b)) is None
    assert store.list_accounts() == [str(a)]


def test_independent_gmail_sync_state_per_user(db):
    from tests.fakes import FakeGmailResource
    from app.services.gmail_service import GmailService

    a, b = seed_user(sub="sync-a"), seed_user(sub="sync-b")
    GmailSyncService(db, user_pk=a).ensure_baseline(
        GmailService(service=FakeGmailResource(history_id="111", email="a@gmail.com"))
    )
    GmailSyncService(db, user_pk=b).ensure_baseline(
        GmailService(service=FakeGmailResource(history_id="222", email="b@gmail.com"))
    )
    assert GmailSyncService(db, user_pk=a).get_state().last_history_id == "111"
    assert GmailSyncService(db, user_pk=b).get_state().last_history_id == "222"


def test_disconnect_gmail_keeps_app_data_and_session(real_auth):
    a = seed_user(sub="disc-a")
    ta = issue_token(a)
    ea = _persist(internship_email("gmail_disc_a"), a)
    with db_session.db_session() as db:
        DbTokenStore(db).put({"refresh_token": "r", "account_email": "a@gmail.com"},
                             account_id=str(a))

    assert client.get("/api/v1/auth/google/status",
                      headers=auth_headers(ta)).json()["connected"] is True
    assert client.post("/api/v1/auth/google/disconnect",
                       headers=auth_headers(ta)).status_code == 200
    assert client.get("/api/v1/auth/google/status",
                      headers=auth_headers(ta)).json()["connected"] is False
    # app data + session survive
    assert [e["email_id"] for e in
            client.get("/api/v1/emails", headers=auth_headers(ta)).json()] == [ea]


def test_scheduler_gmail_cycle_iterates_connected_users(db, monkeypatch):
    from app.services.scheduler import MonitorScheduler

    a, b = seed_user(sub="sched-a"), seed_user(sub="sched-b")
    with db_session.db_session() as s:
        DbTokenStore(s).put({"refresh_token": "ra"}, account_id=str(a))
        DbTokenStore(s).put({"refresh_token": "rb"}, account_id=str(b))

    monkeypatch.setattr(
        "app.services.gmail_auth_service.GmailAuthService.get_credentials",
        lambda self, account_id=None, **k: object() if account_id in {str(a), str(b)} else None,
    )
    synced: list = []
    monkeypatch.setattr(GmailSyncService, "sync_new_messages",
                        lambda self, *ar, **k: synced.append(self.user_pk) or
                        {"status": "synced", "new_message_ids": [], "processed": 0})

    MonitorScheduler(Settings(scheduler_enabled=True, gmail_sync_enabled=True))._gmail_cycle()
    assert sorted(synced) == sorted([a, b])
