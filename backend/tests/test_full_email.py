"""Phase 18 — GET /api/v1/emails/{email_id}/full (complete email viewing)."""

from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_user, get_gmail_service
from app.db import session as db_session
from app.main import app
from app.services.gmail_service import GmailService
from app.services.persistence_service import PersistenceService
from tests.auth_helpers import auth_headers, issue_token, seed_user
from tests.fakes import FakeGmailResource, minimal_raw_message
from tests.persistence_helpers import decision_for, default_user_pk
from tests.triage_helpers import make_email

client = TestClient(app)


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def _html_raw(msg_id: str, html: str, subject: str = "Formatted mail") -> dict:
    return {
        "id": msg_id,
        "threadId": msg_id,
        "labelIds": ["INBOX"],
        "snippet": "formatted",
        "internalDate": "1787888662000",
        "payload": {
            "mimeType": "text/html",
            "headers": [
                {"name": "From", "value": f"News <news.{msg_id}@example.com>"},
                {"name": "Subject", "value": subject},
                {"name": "Date", "value": "Thu, 28 Aug 2026 09:14:22 +0530"},
                {"name": "Message-ID", "value": f"<{msg_id}@example.com>"},
            ],
            "body": {"size": len(html), "data": _b64(html)},
        },
    }


def _persist(email_id: str, *, subject="Hello", body="Some text body here."):
    e = make_email(sender="x@example.com", subject=subject, body=body).model_copy(
        update={"email_id": email_id, "thread_id": f"{email_id}_t"}
    )
    with db_session.db_session() as db:
        upk = default_user_pk(db)
        return PersistenceService(db, user_pk=upk).persist_decision(
            e, decision_for(e)
        ).email_id


@pytest.fixture
def fake_gmail():
    fake = FakeGmailResource(
        messages={
            "plainmsg": minimal_raw_message(
                "plainmsg", subject="Plain note",
                body="Line one.\nLine two.\nRegards, Team.",
            ),
            "htmlmsg": _html_raw(
                "htmlmsg",
                "<html><head><style>b{color:red}</style></head><body>"
                "<script>alert('xss')</script>"
                "<h1>Newsletter</h1><p>Hello <b>reader</b>, visit "
                "<a href='https://example.com/x'>our site</a>.</p></body></html>",
            ),
        }
    )
    app.dependency_overrides[get_gmail_service] = lambda: GmailService(service=fake)
    yield fake
    app.dependency_overrides.pop(get_gmail_service, None)


def test_owner_retrieves_plain_text_full_email(fake_gmail):
    eid = _persist("gmail_plainmsg", subject="Plain note")
    r = client.get(f"/api/v1/emails/{eid}/full")
    assert r.status_code == 200
    body = r.json()
    assert body["subject"] == "Plain note"
    assert body["sender_email"] == "sender.plainmsg@example.com"
    assert "Line one." in body["body"] and "Line two." in body["body"]
    assert body["body_format"] == "text"
    # classification passthrough
    assert body["primary_category"] in {"REPLY_REQUIRED", "ACTION_REQUIRED", "IMPORTANT", "LOW_PRIORITY"}


def test_html_email_is_flattened_and_script_free(fake_gmail):
    eid = _persist("gmail_htmlmsg", subject="Formatted mail")
    r = client.get(f"/api/v1/emails/{eid}/full")
    assert r.status_code == 200
    body = r.json()
    assert body["body_format"] == "html_converted"
    text = body["body"].lower()
    assert "<script" not in text and "alert(" not in text
    assert "<h1" not in text and "<style" not in text
    assert "newsletter" in text and "reader" in text           # visible text kept
    assert "https://example.com/x" in body["body"]             # link preserved inline
    # never leaks the raw Gmail object / headers
    assert set(body) == {
        "email_id", "thread_id", "subject", "sender_name", "sender_email",
        "received_at", "body", "body_format", "is_truncated",
        "primary_category", "final_category", "priority_level", "action_required",
    }


def test_missing_message_in_gmail_is_404(fake_gmail):
    eid = _persist("gmail_ghost")  # persisted, but Gmail has no such message
    r = client.get(f"/api/v1/emails/{eid}/full")
    assert r.status_code == 404


def test_unknown_email_is_404(fake_gmail):
    assert client.get("/api/v1/emails/gmail_nope/full").status_code == 404


def test_unauthenticated_is_401(fake_gmail):
    eid = _persist("gmail_plainmsg")
    saved = app.dependency_overrides.pop(get_current_user, None)
    try:
        assert client.get(f"/api/v1/emails/{eid}/full").status_code == 401
    finally:
        if saved is not None:
            app.dependency_overrides[get_current_user] = saved


def test_another_user_cannot_read_your_full_email(fake_gmail):
    eid = _persist("gmail_plainmsg")
    other = seed_user(sub="full-other")
    tok = issue_token(other)
    saved = app.dependency_overrides.pop(get_current_user, None)
    try:
        assert client.get(f"/api/v1/emails/{eid}/full",
                          headers=auth_headers(tok)).status_code == 404
    finally:
        if saved is not None:
            app.dependency_overrides[get_current_user] = saved
