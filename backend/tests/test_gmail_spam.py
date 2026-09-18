"""Gmail SPAM exclusion — every ingestion path must ignore messages Gmail has
labeled SPAM, using Gmail's own label metadata (never our own detection).

Covers: initial baseline, incremental sync (new mail + label transitions),
the legacy manual/bulk endpoints, and that spam never reaches the ML
classifier / LLM / persistence / notifications / the homepage.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.agents.amar_orchestrator import build_default_orchestrator
from app.agents.intake_agent import MailIntakeAgent
from app.core.config import Settings
from app.db.models import EmailRecord, NotificationRecord
from app.main import app
from app.services import amar_pipeline, gmail_pipeline
from app.services.gmail_service import GmailService, is_spam_message
from app.services.gmail_sync_service import GmailSyncService
from tests.fakes import FakeGmailResource, minimal_raw_message
from tests.persistence_helpers import default_user_pk

client = TestClient(app)


def _gmail(**kw) -> GmailService:
    return GmailService(service=FakeGmailResource(**kw))


def _svc(db, **kw) -> GmailSyncService:
    kw.setdefault("user_pk", default_user_pk(db))
    return GmailSyncService(db, **kw)


def _raw(mid: str, *, spam: bool = False) -> dict:
    msg = minimal_raw_message(mid, subject=f"Subject {mid}", body=f"Body for {mid}")
    if spam:
        msg["labelIds"] = ["SPAM"]
    return msg


def _intake() -> MailIntakeAgent:
    return MailIntakeAgent(Settings())


# --- is_spam_message helper -------------------------------------------

def test_is_spam_message_reads_gmail_label_metadata():
    assert is_spam_message({"labelIds": ["INBOX", "SPAM"]}) is True
    assert is_spam_message({"labelIds": ["INBOX", "UNREAD"]}) is False
    assert is_spam_message({"labelIds": []}) is False
    assert is_spam_message({}) is False
    assert is_spam_message(None) is False
    assert is_spam_message(["SPAM"]) is True  # bare label list also accepted


# --- excluded from incremental sync (new mail) -------------------------

def test_spam_excluded_from_incremental_sync(db):
    svc = _svc(db)
    svc.ensure_baseline(_gmail(history_id="100"))
    # A single history entry applies one label set to every message it lists,
    # so the spam message gets its own entry carrying SPAM in its labelIds.
    gmail = _gmail(
        history_id="120",
        history=[
            {"id": 110, "added_message_ids": ["good"], "labels": ["INBOX", "UNREAD"]},
            {"id": 111, "added_message_ids": ["junk"], "labels": ["INBOX", "UNREAD", "SPAM"]},
        ],
        messages={"good": _raw("good"), "junk": _raw("junk", spam=True)},
    )
    result = svc.sync_new_messages(gmail)

    assert result["new_message_ids"] == ["good"]  # excluded before any fetch
    assert result["processed"] == 1
    assert {e.email_id for e in db.query(EmailRecord).all()} == {"gmail_good"}
    # the spam message was never fetched at all (early exclusion)
    fake = gmail.service
    assert ("messages.get", {"id": "junk", "format": "full"}) not in fake.calls


def test_spam_still_excluded_even_if_fetched(db):
    """Defense in depth: even if a spam-labeled message id slips through the
    history scan, the sync loop itself refuses to process it once fetched."""
    svc = _svc(db)
    svc.ensure_baseline(_gmail(history_id="100"))
    # UNREAD-only label filter lets it through the id scan; the raw message
    # itself carries SPAM — the per-message fetch-time guard must catch it.
    gmail = _gmail(
        history_id="120",
        history=[{"id": 110, "added_message_ids": ["junk"], "labels": ["UNREAD"]}],
        messages={"junk": _raw("junk", spam=True)},
    )
    result = svc.sync_new_messages(gmail)
    assert result["processed"] == 0
    assert db.query(EmailRecord).count() == 0


def test_repeated_sync_does_not_accidentally_ingest_spam(db):
    svc = _svc(db)
    svc.ensure_baseline(_gmail(history_id="100"))
    gmail = _gmail(
        history_id="120",
        history=[{"id": 110, "added_message_ids": ["junk"], "labels": ["INBOX", "UNREAD", "SPAM"]}],
        messages={"junk": _raw("junk", spam=True)},
    )
    for _ in range(3):
        result = svc.sync_new_messages(gmail)
        assert result["processed"] == 0
    assert db.query(EmailRecord).count() == 0
    assert db.query(NotificationRecord).count() == 0


def test_normal_messages_continue_processing_correctly_alongside_spam(db):
    svc = _svc(db)
    svc.ensure_baseline(_gmail(history_id="100"))
    gmail = _gmail(
        history_id="130",
        history=[
            {"id": 110, "added_message_ids": ["good1"], "labels": ["INBOX", "UNREAD"]},
            {"id": 111, "added_message_ids": ["junk"], "labels": ["INBOX", "UNREAD", "SPAM"]},
            {"id": 112, "added_message_ids": ["good2"], "labels": ["INBOX", "UNREAD"]},
        ],
        messages={"good1": _raw("good1"), "junk": _raw("junk", spam=True), "good2": _raw("good2")},
    )
    result = svc.sync_new_messages(gmail)
    assert result["processed"] == 2
    assert {e.email_id for e in db.query(EmailRecord).all()} == {"gmail_good1", "gmail_good2"}


# --- an already-ingested message later marked spam ----------------------

def test_message_moved_to_spam_after_ingestion_is_removed_from_active_view(db):
    uid = default_user_pk(db)
    svc = _svc(db, user_pk=uid)
    svc.ensure_baseline(_gmail(history_id="100"))
    gmail = _gmail(
        history_id="120",
        history=[{"id": 110, "added_message_ids": ["m1"], "labels": ["INBOX", "UNREAD"]}],
        messages={"m1": _raw("m1")},
    )
    svc.sync_new_messages(gmail)
    assert db.query(EmailRecord).filter_by(email_id="gmail_m1").one().is_spam is False

    # the user later moves it to Spam in Gmail — a labelAdded history event,
    # not a messageAdded one
    gmail2 = _gmail(
        history_id="140",
        history=[{"id": 130, "labels_added": [{"message_id": "m1", "label_ids": ["SPAM"]}]}],
    )
    result = svc.sync_new_messages(gmail2)
    assert result["spam_marked"] == 1

    row = db.query(EmailRecord).filter_by(email_id="gmail_m1").one()
    assert row.is_spam is True
    assert row.is_active is False  # gone from the active dashboard
    # the Gmail message itself is never "deleted" locally — the row remains
    assert db.query(EmailRecord).count() == 1


def test_spam_marked_email_generates_no_further_notifications(db):
    uid = default_user_pk(db)
    svc = _svc(db, user_pk=uid)
    svc.ensure_baseline(_gmail(history_id="100"))
    gmail = _gmail(
        history_id="120",
        history=[{"id": 110, "added_message_ids": ["m1"], "labels": ["INBOX", "UNREAD"]}],
        messages={"m1": _raw("m1")},
    )
    svc.sync_new_messages(gmail)

    gmail2 = _gmail(
        history_id="140",
        history=[{"id": 130, "labels_added": [{"message_id": "m1", "label_ids": ["SPAM"]}]}],
    )
    before = db.query(NotificationRecord).count()
    svc.sync_new_messages(gmail2)
    after = db.query(NotificationRecord).count()
    assert after == before


def test_message_removed_from_spam_is_reprocessed_without_duplicating(db):
    uid = default_user_pk(db)
    svc = _svc(db, user_pk=uid)
    svc.ensure_baseline(_gmail(history_id="100"))
    gmail = _gmail(
        history_id="120",
        history=[{"id": 110, "added_message_ids": ["m1"], "labels": ["INBOX", "UNREAD"]}],
        messages={"m1": _raw("m1")},
    )
    svc.sync_new_messages(gmail)
    gmail2 = _gmail(
        history_id="140",
        history=[{"id": 130, "labels_added": [{"message_id": "m1", "label_ids": ["SPAM"]}]}],
    )
    svc.sync_new_messages(gmail2)
    assert db.query(EmailRecord).filter_by(email_id="gmail_m1").one().is_spam is True

    # ... then the user (or Gmail) moves it back out of Spam
    gmail3 = _gmail(
        history_id="160",
        history=[{"id": 150, "labels_removed": [{"message_id": "m1", "label_ids": ["SPAM"]}]}],
        messages={"m1": _raw("m1")},
    )
    result = svc.sync_new_messages(gmail3)
    assert result["spam_restored"] == 1

    rows = db.query(EmailRecord).filter_by(email_id="gmail_m1").all()
    assert len(rows) == 1  # never duplicated
    assert rows[0].is_spam is False
    assert rows[0].is_active is True


# --- never reaches ML / LLM / persistence (legacy manual endpoints) -----

def test_spam_never_reaches_pipeline_via_legacy_bulk_endpoint(db):
    fake = FakeGmailResource(
        unread_ids=["junk"],
        messages={"junk": _raw("junk", spam=True)},
    )
    gmail = GmailService(service=fake)
    result = amar_pipeline.process_unread(
        gmail, _intake(), build_default_orchestrator(Settings()), persistence=None,
    )
    assert result["count"] == 0
    assert result["unread_ids_seen"] == 1
    # the spam message id was listed but never fetched-and-processed into an
    # emitted email — and definitely never persisted
    assert db.query(EmailRecord).count() == 0


def test_spam_never_normalized_via_legacy_fetch_endpoint():
    fake = FakeGmailResource(unread_ids=["junk"], messages={"junk": _raw("junk", spam=True)})
    result = gmail_pipeline.fetch_unread_normalized(GmailService(service=fake), _intake())
    assert result["count"] == 0
    assert result["emails"] == []


# --- homepage / API visibility ------------------------------------------

def test_spam_does_not_appear_on_the_homepage_or_full_list(db):
    uid = default_user_pk(db)
    svc = _svc(db, user_pk=uid)
    svc.ensure_baseline(_gmail(history_id="100"))
    gmail = _gmail(
        history_id="120",
        history=[
            {"id": 110, "added_message_ids": ["good"], "labels": ["INBOX", "UNREAD"]},
        ],
        messages={"good": _raw("good")},
    )
    svc.sync_new_messages(gmail)
    # mark it spam directly (simulating a transition) to test the read path
    row = db.query(EmailRecord).filter_by(email_id="gmail_good").one()
    row.is_spam = True
    db.commit()

    assert client.get("/api/v1/emails").json() == []
    assert client.get("/api/v1/emails", params={"active": "true"}).json() == []
    assert client.get("/api/v1/emails", params={"active": "false"}).json() == []
    assert client.get("/api/v1/emails/gmail_good").status_code == 404


def test_non_spam_email_still_appears_normally(db):
    uid = default_user_pk(db)
    svc = _svc(db, user_pk=uid)
    svc.ensure_baseline(_gmail(history_id="100"))
    gmail = _gmail(
        history_id="120",
        history=[{"id": 110, "added_message_ids": ["good"], "labels": ["INBOX", "UNREAD"]}],
        messages={"good": _raw("good")},
    )
    svc.sync_new_messages(gmail)

    listed = client.get("/api/v1/emails").json()
    assert [e["email_id"] for e in listed] == ["gmail_good"]
    assert client.get("/api/v1/emails/gmail_good").status_code == 200
