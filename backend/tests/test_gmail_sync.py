"""Phase 12 — incremental Gmail sync & persistent monitoring baseline."""

from __future__ import annotations

import threading

import pytest
from fastapi.testclient import TestClient

from app.agents.intake_agent import MailIntakeAgent
from app.api.deps import get_gmail_service
from app.core.config import Settings
from app.core.errors import GmailHistoryExpiredError
from app.db.models import EmailRecord, GmailSyncState
from app.main import app
from app.repositories import GmailSyncRepository
from app.services import gmail_sync_service as sync_module
from app.services.gmail_service import GmailService
from app.services.gmail_sync_service import GmailSyncService
from tests.fakes import FakeGmailResource, make_http_error, minimal_raw_message


def _gmail(**kw) -> GmailService:
    return GmailService(service=FakeGmailResource(**kw))


def _raw(mid: str) -> dict:
    return minimal_raw_message(mid, subject=f"Subject {mid}", body=f"Body for {mid}, please reply.")


# ============================================================================
# model + repository
# ============================================================================

def test_repo_get_or_create_is_singleton(db):
    repo = GmailSyncRepository(db)
    a = repo.get_or_create()
    db.commit()
    b = repo.get_or_create()
    assert a.id == b.id
    assert db.query(GmailSyncState).count() == 1


# ============================================================================
# baseline
# ============================================================================

def test_baseline_records_history_id_and_processes_nothing(db):
    svc = GmailSyncService(db)
    gmail = _gmail(history_id="5000", email="me@gmail.com")

    state = svc.ensure_baseline(gmail)

    assert state.last_history_id == "5000"
    assert state.monitoring_started_at is not None
    assert state.account_email == "me@gmail.com"
    assert db.query(EmailRecord).count() == 0   # historical inbox NOT ingested


def test_baseline_is_idempotent(db):
    svc = GmailSyncService(db)
    first = svc.ensure_baseline(_gmail(history_id="100"))
    started = first.monitoring_started_at
    # a second call with a *different* mailbox historyId must not move the baseline
    second = svc.ensure_baseline(_gmail(history_id="999"))
    assert second.last_history_id == "100"
    assert second.monitoring_started_at == started


# ============================================================================
# incremental sync
# ============================================================================

def test_first_sync_baselines_and_processes_zero(db):
    svc = GmailSyncService(db)
    result = svc.sync_new_messages(_gmail(history_id="200"))
    assert result["status"] == "baselined"
    assert result["processed"] == 0
    assert svc.get_state().last_history_id == "200"
    assert db.query(EmailRecord).count() == 0


def test_sync_processes_only_new_messages(db):
    svc = GmailSyncService(db)
    svc.ensure_baseline(_gmail(history_id="100"))

    gmail = _gmail(
        history_id="120",
        history=[{"id": 110, "added_message_ids": ["m_a", "m_b"]}],
        messages={"m_a": _raw("m_a"), "m_b": _raw("m_b")},
    )
    result = svc.sync_new_messages(gmail)

    assert result["status"] == "synced"
    assert result["processed"] == 2
    assert set(result["new_message_ids"]) == {"m_a", "m_b"}
    assert svc.get_state().last_history_id == "120"
    assert {e.email_id for e in db.query(EmailRecord).all()} == {"gmail_m_a", "gmail_m_b"}


def test_sync_is_idempotent_no_duplicate_emails(db):
    svc = GmailSyncService(db)
    svc.ensure_baseline(_gmail(history_id="100"))
    gmail = _gmail(
        history_id="120",
        history=[{"id": 110, "added_message_ids": ["m_a"]}],
        messages={"m_a": _raw("m_a")},
    )
    svc.sync_new_messages(gmail)
    # replay the exact same history window (e.g. crash before the historyId
    # advance was committed, or a repeated history event) — must not create a
    # second EmailRecord.
    state = svc.get_state()
    state.last_history_id = "100"        # simulate "progress was never saved"
    db.commit()
    result = svc.sync_new_messages(gmail)
    assert result["processed"] == 1
    assert db.query(EmailRecord).filter_by(email_id="gmail_m_a").count() == 1


def test_sync_skips_messages_that_are_no_longer_unread(db):
    svc = GmailSyncService(db)
    svc.ensure_baseline(_gmail(history_id="100"))
    gmail = _gmail(
        history_id="130",
        history=[
            {"id": 110, "added_message_ids": ["still_unread"], "labels": ["INBOX", "UNREAD"]},
            {"id": 111, "added_message_ids": ["now_read"], "labels": ["INBOX"]},  # no UNREAD
        ],
        messages={"still_unread": _raw("still_unread"), "now_read": _raw("now_read")},
    )
    result = svc.sync_new_messages(gmail)
    assert result["new_message_ids"] == ["still_unread"]


def test_sync_caps_at_max_messages(db):
    svc = GmailSyncService(db, settings=Settings(gmail_sync_max_messages=2))
    svc.ensure_baseline(_gmail(history_id="100"))
    ids = [f"m{i}" for i in range(5)]
    gmail = _gmail(
        history_id="200",
        history=[{"id": 110, "added_message_ids": ids}],
        messages={i: _raw(i) for i in ids},
    )
    result = svc.sync_new_messages(gmail)
    assert len(result["new_message_ids"]) == 2


def test_history_expired_rebaselines(db):
    svc = GmailSyncService(db)
    svc.ensure_baseline(_gmail(history_id="100"))
    gmail = _gmail(history_id="9000", history_error=make_http_error(404, "history id too old"))
    result = svc.sync_new_messages(gmail)
    assert result["status"] == "history_expired_rebaselined"
    assert svc.get_state().last_history_id == "9000"
    assert db.query(EmailRecord).count() == 0


def test_list_added_raises_history_expired_on_404():
    gmail = _gmail(history_error=make_http_error(404, "too old"))
    with pytest.raises(GmailHistoryExpiredError):
        gmail.list_added_message_ids_since("1")


def test_progress_not_advanced_when_history_call_fails(db):
    svc = GmailSyncService(db)
    svc.ensure_baseline(_gmail(history_id="100"))

    boom = _gmail(history_id="150", history_error=make_http_error(503, "backend error"))
    with pytest.raises(Exception):
        svc.sync_new_messages(boom)
    assert svc.get_state().last_history_id == "100"   # unchanged → safe retry

    # retry succeeds and processes the window
    ok = _gmail(
        history_id="150",
        history=[{"id": 120, "added_message_ids": ["m_x"]}],
        messages={"m_x": _raw("m_x")},
    )
    result = svc.sync_new_messages(ok)
    assert result["processed"] == 1
    assert svc.get_state().last_history_id == "150"


def test_per_message_failure_does_not_stall_sync(db):
    svc = GmailSyncService(db)
    svc.ensure_baseline(_gmail(history_id="100"))
    gmail = _gmail(
        history_id="120",
        history=[{"id": 110, "added_message_ids": ["ok", "missing"]}],
        messages={"ok": _raw("ok")},  # "missing" -> 404
    )
    result = svc.sync_new_messages(gmail)
    assert result["processed"] == 1
    assert result["errors"] and result["errors"][0]["message_id"] == "missing"
    assert svc.get_state().last_history_id == "120"   # still advances


# ============================================================================
# concurrency
# ============================================================================

def test_overlapping_sync_returns_skipped_locked(db):
    svc = GmailSyncService(db)
    svc.ensure_baseline(_gmail(history_id="100"))
    sync_module._SYNC_LOCK.acquire()
    try:
        result = svc.sync_new_messages(_gmail(history_id="120"))
    finally:
        sync_module._SYNC_LOCK.release()
    assert result["status"] == "skipped_locked"


# ============================================================================
# persistence across "restart"
# ============================================================================

def test_state_survives_new_service_instance(db):
    GmailSyncService(db).ensure_baseline(_gmail(history_id="777"))
    # a fresh service (== a process restart) sees the persisted baseline
    reborn = GmailSyncService(db)
    state = reborn.get_state()
    assert state is not None and state.last_history_id == "777"
    # and does NOT re-baseline / re-ingest
    result = reborn.sync_new_messages(
        _gmail(history_id="780", history=[{"id": 778, "added_message_ids": ["n1"]},],
               messages={"n1": _raw("n1")})
    )
    assert result["status"] == "synced"
    assert result["processed"] == 1


# ============================================================================
# scheduler cycle
# ============================================================================

def test_scheduler_gmail_cycle_skips_when_not_connected(monkeypatch):
    from app.services.scheduler import MonitorScheduler

    sch = MonitorScheduler(Settings(scheduler_enabled=True, gmail_sync_enabled=True))
    monkeypatch.setattr(
        "app.services.gmail_auth_service.GmailAuthService.get_credentials",
        lambda self, *a, **k: None,
    )
    sch._gmail_cycle()          # must not raise
    assert sch.cycles["gmail"] == 0


def test_scheduler_gmail_cycle_runs_sync_when_connected(monkeypatch):
    from app.services.scheduler import MonitorScheduler

    calls: dict = {}
    monkeypatch.setattr(
        "app.services.gmail_auth_service.GmailAuthService.get_credentials",
        lambda self, *a, **k: object(),  # truthy "credentials"
    )

    def _fake_sync(self, gmail, **_kw):
        calls["gmail_type"] = type(gmail).__name__
        return {"status": "synced", "new_message_ids": [], "processed": 0}

    monkeypatch.setattr(GmailSyncService, "sync_new_messages", _fake_sync)

    sch = MonitorScheduler(Settings(scheduler_enabled=True, gmail_sync_enabled=True))
    sch._gmail_cycle()

    assert calls["gmail_type"] == "GmailService"   # the scheduler builds one and delegates
    assert sch.cycles["gmail"] == 1
    assert sch.last_gmail_sync is not None


# ============================================================================
# API
# ============================================================================

@pytest.fixture
def connected_client():
    fake = FakeGmailResource(history_id="100", email="me@gmail.com")
    app.dependency_overrides[get_gmail_service] = lambda: GmailService(service=fake)
    client = TestClient(app)
    yield client, fake
    app.dependency_overrides.pop(get_gmail_service, None)


def test_sync_status_endpoint_before_and_after(connected_client):
    client, fake = connected_client

    before = client.get("/api/v1/gmail/sync/status").json()
    assert before["monitoring"] is False and before["last_history_id"] is None

    r1 = client.post("/api/v1/gmail/sync")
    assert r1.status_code == 200
    assert r1.json()["status"] == "baselined"

    after = client.get("/api/v1/gmail/sync/status").json()
    assert after["monitoring"] is True
    assert after["last_history_id"] == "100"
    assert after["account_email"] == "me@gmail.com"


def test_sync_endpoint_processes_new_mail(connected_client):
    client, fake = connected_client
    client.post("/api/v1/gmail/sync")            # baseline at 100

    fake.history_id = "140"
    fake.history = [{"id": 120, "added_message_ids": ["api_m1"]}]
    fake.messages = {"api_m1": _raw("api_m1")}

    r = client.post("/api/v1/gmail/sync")
    body = r.json()
    assert body["status"] == "synced"
    assert body["processed"] == 1
    assert body["new_message_ids"] == ["api_m1"]

    listed = client.get("/api/v1/emails").json()
    assert [e["email_id"] for e in listed] == ["gmail_api_m1"]


def test_sync_endpoint_requires_gmail_connection():
    # no override -> conftest's _gmail_offline_by_default makes it disconnected
    assert TestClient(app).post("/api/v1/gmail/sync").status_code == 401


def test_unread_process_still_works(connected_client):
    """The pre-existing manual endpoint is unchanged."""
    client, fake = connected_client
    fake.unread_ids = ["u1"]
    fake.messages = {"u1": _raw("u1")}
    r = client.get("/api/v1/gmail/unread/process?max_results=5")
    assert r.status_code == 200
    assert r.json()["emails"][0]["persisted"]["email_id"] == "gmail_u1"
