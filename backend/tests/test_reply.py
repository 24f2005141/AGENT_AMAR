"""AI reply suggestions + send (`/api/v1/emails/{email_id}/reply-suggestions`,
`/api/v1/emails/{email_id}/reply`).

No real Gmail, no real LLM — a FakeGmailResource and a FakeLLM are injected.
"""

from __future__ import annotations

import base64
import email as email_lib
from email import policy as email_policy

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_user, get_gmail_service, get_reply_llm_client
from app.db import session as db_session
from app.main import app
from app.services.gmail_service import GmailService
from app.services.persistence_service import PersistenceService
from tests.fakes import FakeGmailResource, make_http_error, minimal_raw_message
from tests.persistence_helpers import decision_for, default_user_pk
from tests.triage_helpers import FakeLLM, make_email

client = TestClient(app)

_GOOD_LLM = {
    "suggestions": [
        {"label": "Direct", "body": "Yes, I will attend the project meeting tomorrow."},
        {
            "label": "Professional",
            "body": "Thank you for the note. I expect to be able to join the meeting tomorrow; "
            "please let me know if there is anything I should review beforehand.",
        },
        {
            "label": "Alternative",
            "body": "Unfortunately I do not think I can make the meeting tomorrow. "
            "Could you share the key updates afterwards?",
        },
    ]
}


def _reply_email(email_id: str = "gmail_reply1"):
    return make_email(
        sender="prof@college.edu",
        subject="Project meeting tomorrow",
        body="Hi, please reply to this email to confirm whether you will attend "
        "the project meeting tomorrow.",
    ).model_copy(update={"email_id": email_id, "thread_id": f"{email_id}_t"})


def _persist(email, user_pk):
    with db_session.db_session() as db:
        return PersistenceService(db, user_pk=user_pk).persist_decision(
            email, decision_for(email)
        ).email_id


@pytest.fixture
def owned_email():
    with db_session.db_session() as db:
        upk = default_user_pk(db)
    return _persist(_reply_email(), upk)


@pytest.fixture
def fake_gmail():
    """A FakeGmailResource holding the original message for `gmail_reply1`."""
    raw = minimal_raw_message(
        "reply1",
        subject="Project meeting tomorrow",
        body="Hi, can you confirm whether you will attend the project meeting tomorrow?",
        thread_id="thread-xyz",
        references="<root@college.edu>",
    )
    fake = FakeGmailResource(messages={"reply1": raw})
    app.dependency_overrides[get_gmail_service] = lambda: GmailService(service=fake)
    yield fake
    app.dependency_overrides.pop(get_gmail_service, None)


@pytest.fixture
def fake_llm():
    holder = {"llm": FakeLLM(response=_GOOD_LLM)}
    app.dependency_overrides[get_reply_llm_client] = lambda: holder["llm"]
    yield holder
    app.dependency_overrides.pop(get_reply_llm_client, None)


# --- reply suggestions ------------------------------------------------

def test_owner_gets_exactly_three_suggestions(owned_email, fake_gmail, fake_llm):
    r = client.post(f"/api/v1/emails/{owned_email}/reply-suggestions")
    assert r.status_code == 200
    body = r.json()
    assert body["email_id"] == owned_email
    assert len(body["suggestions"]) == 3
    assert [s["id"] for s in body["suggestions"]] == ["option_1", "option_2", "option_3"]
    bodies = [s["body"] for s in body["suggestions"]]
    assert len(set(bodies)) == 3  # meaningfully different
    # the shared LLM abstraction was actually used
    assert fake_llm["llm"].calls, "the LLM client was not invoked"


def test_suggestions_require_auth(owned_email, fake_gmail, fake_llm):
    app.dependency_overrides.pop(get_current_user, None)  # drop conftest auto-login
    try:
        r = client.post(f"/api/v1/emails/{owned_email}/reply-suggestions")
        assert r.status_code == 401
    finally:
        pass  # conftest fixture teardown restores the override


def test_other_user_cannot_get_suggestions(owned_email, fake_gmail, fake_llm):
    from app.db.models import User

    with db_session.db_session() as db:
        other = User(google_sub="reply-other", google_email="other@example.com")
        db.add(other)
        db.commit()
        db.refresh(other)
        db.expunge(other)
    app.dependency_overrides[get_current_user] = lambda: other
    try:
        r = client.post(f"/api/v1/emails/{owned_email}/reply-suggestions")
        assert r.status_code == 404
    finally:
        app.dependency_overrides[get_current_user] = lambda: _default()


def _default():
    from app.db.models import User

    with db_session.db_session() as db:
        return db.query(User).filter(User.google_sub == "test-sub-default").one()


def test_unknown_email_is_404(fake_gmail, fake_llm):
    r = client.post("/api/v1/emails/gmail_does_not_exist/reply-suggestions")
    assert r.status_code == 404


def test_llm_unavailable_returns_503(owned_email, fake_gmail):
    app.dependency_overrides[get_reply_llm_client] = lambda: FakeLLM(available=False)
    try:
        r = client.post(f"/api/v1/emails/{owned_email}/reply-suggestions")
        assert r.status_code == 503
        assert r.json()["error"] == "LLMUnavailableError"
    finally:
        app.dependency_overrides.pop(get_reply_llm_client, None)


def test_malformed_llm_response_returns_502(owned_email, fake_gmail):
    # only two options, and one is a near-duplicate of the other
    bad = {"suggestions": [
        {"label": "A", "body": "Yes I will attend."},
        {"label": "B", "body": "Yes, I will attend!"},
    ]}
    app.dependency_overrides[get_reply_llm_client] = lambda: FakeLLM(response=bad)
    try:
        r = client.post(f"/api/v1/emails/{owned_email}/reply-suggestions")
        assert r.status_code == 502
        assert r.json()["error"] == "LLMResponseError"
    finally:
        app.dependency_overrides.pop(get_reply_llm_client, None)


def test_three_identical_sentences_are_rejected(owned_email, fake_gmail):
    same = {"suggestions": [
        {"label": "1", "body": "Yes, I will attend the meeting."},
        {"label": "2", "body": "Yes, I will attend the meeting."},
        {"label": "3", "body": "Yes, I will attend the meeting."},
    ]}
    app.dependency_overrides[get_reply_llm_client] = lambda: FakeLLM(response=same)
    try:
        r = client.post(f"/api/v1/emails/{owned_email}/reply-suggestions")
        assert r.status_code == 502
    finally:
        app.dependency_overrides.pop(get_reply_llm_client, None)


# --- send reply -----------------------------------------------------

def _decode_sent(fake_gmail):
    assert fake_gmail.sent, "no message was sent"
    raw_b64 = fake_gmail.sent[-1]["raw"]
    return email_lib.message_from_bytes(
        base64.urlsafe_b64decode(raw_b64), policy=email_policy.default
    )


def test_owner_sends_reply_verbatim_and_threaded(owned_email, fake_gmail):
    text = "Yes, I will attend the project meeting tomorrow.\n\nThanks!"
    r = client.post(f"/api/v1/emails/{owned_email}/reply", json={"body": text})
    assert r.status_code == 200
    payload = r.json()
    assert payload["email_id"] == owned_email
    assert payload["gmail_message_id"] == "sent_1"
    assert payload["duplicate_suppressed"] is False

    # exactly one Gmail send, with the original threadId
    assert [c[0] for c in fake_gmail.calls].count("messages.send") == 1
    assert fake_gmail.sent[-1]["threadId"] == "thread-xyz"

    msg = _decode_sent(fake_gmail)
    # body sent unchanged (backend never re-generates it)
    assert msg.get_content().strip() == text.strip()
    # threading headers preserved
    assert msg["In-Reply-To"] == "<reply1@example.com>"
    assert "<root@college.edu>" in msg["References"]
    assert "<reply1@example.com>" in msg["References"]
    assert msg["Subject"] == "Re: Project meeting tomorrow"
    # From is NOT set by us — Gmail fills it with the authenticated account
    assert msg["From"] is None


def test_send_marks_the_reply_action_completed(owned_email, fake_gmail):
    r = client.post(f"/api/v1/emails/{owned_email}/reply", json={"body": "Confirmed, see you there."})
    assert r.status_code == 200
    assert r.json()["reply_action_completed"] is True
    detail = client.get(f"/api/v1/emails/{owned_email}").json()
    reply_actions = [a for a in detail["actions"] if a["action_type"] == "REPLY"]
    assert reply_actions and all(a["status"] == "COMPLETED" for a in reply_actions)


def test_send_marks_the_email_completed(owned_email, fake_gmail):
    r = client.post(f"/api/v1/emails/{owned_email}/reply", json={"body": "See you there."})
    assert r.status_code == 200
    assert r.json()["email_marked_completed"] is True
    detail = client.get(f"/api/v1/emails/{owned_email}").json()
    assert detail["is_completed"] is True


def test_send_requires_auth(owned_email, fake_gmail):
    app.dependency_overrides.pop(get_current_user, None)
    r = client.post(f"/api/v1/emails/{owned_email}/reply", json={"body": "hi"})
    assert r.status_code == 401


def test_other_user_cannot_send_from_someone_elses_email(owned_email, fake_gmail):
    from app.db.models import User

    with db_session.db_session() as db:
        other = User(google_sub="reply-send-other", google_email="o@example.com")
        db.add(other)
        db.commit()
        db.refresh(other)
        db.expunge(other)
    app.dependency_overrides[get_current_user] = lambda: other
    try:
        r = client.post(f"/api/v1/emails/{owned_email}/reply", json={"body": "sneaky"})
        assert r.status_code == 404
        assert fake_gmail.sent == []  # nothing sent
    finally:
        app.dependency_overrides[get_current_user] = lambda: _default()


def test_double_send_is_suppressed(owned_email, fake_gmail):
    body = {"body": "Yes, confirmed for tomorrow."}
    r1 = client.post(f"/api/v1/emails/{owned_email}/reply", json=body)
    r2 = client.post(f"/api/v1/emails/{owned_email}/reply", json=body)
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["duplicate_suppressed"] is False
    assert r2.json()["duplicate_suppressed"] is True
    # Gmail was hit exactly once despite two identical requests
    assert [c[0] for c in fake_gmail.calls].count("messages.send") == 1
    # a DIFFERENT body still sends
    client.post(f"/api/v1/emails/{owned_email}/reply", json={"body": "Actually, running 5 min late."})
    assert [c[0] for c in fake_gmail.calls].count("messages.send") == 2


def test_gmail_send_failure_is_clean_and_retryable(owned_email, fake_gmail):
    fake_gmail.send_error = make_http_error(500, "backend error")
    r = client.post(f"/api/v1/emails/{owned_email}/reply", json={"body": "please send"})
    assert r.status_code in (502, 400)
    assert "raw" not in r.text and "Bearer" not in r.text
    # the failed attempt did NOT lock out a retry
    fake_gmail.send_error = None
    r2 = client.post(f"/api/v1/emails/{owned_email}/reply", json={"body": "please send"})
    assert r2.status_code == 200


def test_send_without_gmail_send_scope_asks_for_reconnect(owned_email):
    """A user connected before the gmail.send scope existed must reconnect."""
    from types import SimpleNamespace

    creds = SimpleNamespace(scopes=["https://www.googleapis.com/auth/gmail.readonly"])
    svc = GmailService(service=FakeGmailResource(messages={"reply1": minimal_raw_message("reply1")}))
    svc._credentials = creds  # simulate a real (scoped) credential object
    app.dependency_overrides[get_gmail_service] = lambda: svc
    try:
        r = client.post(f"/api/v1/emails/{owned_email}/reply", json={"body": "hello"})
        assert r.status_code == 401
        assert r.json()["error"] == "GmailNotConnectedError"
    finally:
        app.dependency_overrides.pop(get_gmail_service, None)
