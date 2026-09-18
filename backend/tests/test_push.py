"""Phase 16 — FCM device registration + backend push dispatch."""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_auth_service, get_current_user
from app.db import session as db_session
from app.db.base import utcnow
from app.db.models import DeviceRegistration, EmailRecord, NotificationRecord
from app.main import app
from app.repositories import DeviceRepository, NotificationRepository
from app.services import push_service as push_mod
from app.services.fcm_client import FcmClient, FcmResult
from app.services.push_service import PushNotificationService, dispatch_unpushed, render_copy
from tests.auth_helpers import auth_headers, issue_token, seed_user
from tests.monitor_helpers import NOW, make_monitored
from tests.persistence_helpers import decision_for, internship_email

client = TestClient(app)


class FakeFcm(FcmClient):
    def __init__(self, *, invalid_tokens: set[str] | None = None, fail: bool = False) -> None:
        self.sent: list[dict] = []
        self.invalid = invalid_tokens or set()
        self.fail = fail

    @property
    def enabled(self) -> bool:
        return True

    def send(self, token, *, title, body, data) -> FcmResult:
        self.sent.append({"token": token, "title": title, "body": body, "data": data})
        if token in self.invalid:
            return FcmResult(ok=False, token_invalid=True, error="UnregisteredError")
        if self.fail:
            return FcmResult(ok=False, error="UnavailableError")
        return FcmResult(ok=True)


@pytest.fixture
def real_auth():
    saved = {k: app.dependency_overrides.pop(k, None) for k in (get_current_user, get_auth_service)}
    yield
    for k, v in saved.items():
        if v is not None:
            app.dependency_overrides[k] = v


# --- registration endpoint ------------------------------------------

def test_register_requires_auth(real_auth):
    r = client.post("/api/v1/devices/register", json={"fcm_token": "tok-abcdefgh"})
    assert r.status_code == 401


def test_register_and_list_device(real_auth):
    uid = seed_user(sub="dev-1")
    t = issue_token(uid)
    r = client.post(
        "/api/v1/devices/register",
        json={"fcm_token": "tok-user1-aaaa", "platform": "android", "device_label": "Pixel"},
        headers=auth_headers(t),
    )
    assert r.status_code == 200 and r.json()["active"] is True
    listed = client.get("/api/v1/devices", headers=auth_headers(t)).json()
    assert len(listed) == 1 and listed[0]["platform"] == "android"


def test_register_is_idempotent_and_refreshes(real_auth):
    uid = seed_user(sub="dev-2")
    t = issue_token(uid)
    for _ in range(3):
        client.post("/api/v1/devices/register",
                    json={"fcm_token": "tok-same", "platform": "android"}, headers=auth_headers(t))
    with db_session.db_session() as db:
        assert db.query(DeviceRegistration).filter_by(fcm_token="tok-same").count() == 1


def test_token_rotation_deactivates_previous(real_auth):
    uid = seed_user(sub="dev-3")
    t = issue_token(uid)
    client.post("/api/v1/devices/register", json={"fcm_token": "tok-old-1"}, headers=auth_headers(t))
    client.post("/api/v1/devices/register",
                json={"fcm_token": "tok-new-1", "previous_token": "tok-old-1"}, headers=auth_headers(t))
    with db_session.db_session() as db:
        repo = DeviceRepository(db)
        assert repo.get_by_token("tok-old-1").active is False
        assert repo.get_by_token("tok-new-1").active is True


def test_same_handset_reassigned_when_a_different_user_logs_in(real_auth):
    a, b = seed_user(sub="dev-a"), seed_user(sub="dev-b")
    ta, tb = issue_token(a), issue_token(b)
    client.post("/api/v1/devices/register", json={"fcm_token": "handset-x"}, headers=auth_headers(ta))
    client.post("/api/v1/devices/register", json={"fcm_token": "handset-x"}, headers=auth_headers(tb))
    with db_session.db_session() as db:
        row = DeviceRepository(db).get_by_token("handset-x")
        assert row.user_pk == b and row.active is True
        assert DeviceRepository(db).list_active(a) == []


def test_logout_deactivates_the_device(real_auth):
    uid = seed_user(sub="dev-4")
    t = issue_token(uid)
    client.post("/api/v1/devices/register", json={"fcm_token": "tok-logout"}, headers=auth_headers(t))
    assert client.post("/api/v1/auth/logout", json={"fcm_token": "tok-logout"},
                       headers=auth_headers(t)).status_code == 200
    with db_session.db_session() as db:
        assert DeviceRepository(db).get_by_token("tok-logout").active is False


# --- push dispatch -------------------------------------------------

def _seed_notification(db, user_pk, *, email_id="gmail_push_1", ntype="new_priority_email",
                       detail="Your OTP is 998877"):
    from app.services.persistence_service import PersistenceService
    email = internship_email(email_id)
    rec = PersistenceService(db, user_pk=user_pk).persist_decision(email, decision_for(email))
    note = NotificationRepository(db).create(
        email_pk=rec.id, notification_type=ntype, reminder_level="NORMAL", detail=detail,
    )
    db.commit()
    return rec, note


def test_dispatch_sends_only_to_the_owning_user(db):
    a, b = seed_user(sub="push-a"), seed_user(sub="push-b")
    PushNotificationService(db).register_device(a, fcm_token="a-phone")
    PushNotificationService(db).register_device(b, fcm_token="b-phone")
    db.commit()
    _seed_notification(db, a, email_id="gmail_pa")

    fake = FakeFcm()
    dispatch_unpushed(a, fcm=fake)

    assert [s["token"] for s in fake.sent] == ["a-phone"]  # never b-phone


def test_dispatch_fans_out_to_multiple_devices_and_is_once_only(db):
    a = seed_user(sub="push-multi")
    for tok in ("a-1", "a-2", "a-3"):
        PushNotificationService(db).register_device(a, fcm_token=tok)
    db.commit()
    _, note = _seed_notification(db, a, email_id="gmail_multi")

    fake = FakeFcm()
    dispatch_unpushed(a, fcm=fake)
    dispatch_unpushed(a, fcm=fake)  # second pass: already pushed

    assert sorted(s["token"] for s in fake.sent) == ["a-1", "a-2", "a-3"]
    db.expire_all()
    row = db.get(NotificationRecord, note.id)
    assert row.pushed_at is not None and row.push_status == "sent"


def test_invalid_token_is_deactivated(db):
    a = seed_user(sub="push-invalid")
    PushNotificationService(db).register_device(a, fcm_token="good")
    PushNotificationService(db).register_device(a, fcm_token="stale")
    db.commit()
    _seed_notification(db, a, email_id="gmail_inv")

    dispatch_unpushed(a, fcm=FakeFcm(invalid_tokens={"stale"}))
    db.expire_all()
    assert DeviceRepository(db).get_by_token("stale").active is False
    assert DeviceRepository(db).get_by_token("good").active is True


def test_push_payload_carries_no_email_content_or_secrets(db):
    a = seed_user(sub="push-otp")
    PushNotificationService(db).register_device(a, fcm_token="otp-phone")
    db.commit()
    _seed_notification(db, a, email_id="gmail_otp", detail="Your login OTP is 483921")

    fake = FakeFcm()
    dispatch_unpushed(a, fcm=fake)

    blob = str(fake.sent)
    assert "483921" not in blob and "OTP" not in blob
    data = fake.sent[0]["data"]
    assert set(data) == {"notification_id", "email_id", "type", "severity", "requires_alarm"}
    assert data["email_id"] == "gmail_otp"  # an id, not content


def test_dispatch_failure_never_raises(db, monkeypatch):
    a = seed_user(sub="push-boom")
    PushNotificationService(db).register_device(a, fcm_token="boom-phone")
    db.commit()
    _seed_notification(db, a, email_id="gmail_boom")

    class Boom(FcmClient):
        @property
        def enabled(self):  # noqa: D401
            return True

        def send(self, *a, **k):
            raise RuntimeError("network down")

    # must not raise
    dispatch_unpushed(a, fcm=Boom())


def test_agent_persist_does_not_crash_when_push_broken(db, monkeypatch):
    a = seed_user(sub="push-pipeline")
    import app.services.persistence_service as ps

    def boom(*ar, **k):
        raise RuntimeError("push subsystem down")

    monkeypatch.setattr(ps, "dispatch_unpushed", boom)
    from app.services.persistence_service import PersistenceService
    email = internship_email("gmail_pipe_ok")
    rec = PersistenceService(db, user_pk=a).persist_decision(email, decision_for(email))
    assert rec.email_id == "gmail_pipe_ok"  # pipeline completed regardless


def test_scheduler_style_monitor_run_triggers_push(db, monkeypatch):
    a = seed_user(sub="push-monitor")
    fake = FakeFcm()
    monkeypatch.setattr(push_mod, "build_fcm_client", lambda *ar, **k: fake)
    PushNotificationService(db).register_device(a, fcm_token="mon-phone")
    db.commit()

    from app.services.deadline_monitor_service import DeadlineMonitorService
    make_monitored(db, email_id="gmail_mon", remaining=timedelta(hours=2), user_pk=a)
    res = DeadlineMonitorService(db, user_pk=a).run_deadline_check(NOW)
    assert res.notifications_created >= 1
    assert any(s["data"]["type"] == "deadline_escalation" for s in fake.sent)


def test_render_copy_is_generic():
    assert render_copy("new_priority_email")[0] == "Important email"
    assert render_copy("deadline_escalation", requires_alarm=True)[0].startswith("Urgent")
    # no interpolation of any dynamic content
    assert "{" not in render_copy("user_reminder")[1]
