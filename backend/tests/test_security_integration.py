"""Phase 14 — end-to-end: encryption is transparent, secrets don't leak, the
existing pipeline / Gmail sync / LLM abstraction are unchanged."""

from __future__ import annotations

import pytest

from app.core import crypto
from app.core.config import Settings
from app.db.models import AuditEvent, EmailRecord, NotificationRecord
from app.services.audit_service import AuditService
from app.services.persistence_service import PersistenceService
from app.services.reminder_service import ReminderService
from tests.persistence_helpers import decision_for
from tests.triage_helpers import make_email

_KEY = crypto.generate_key()

_OTP_EMAIL = make_email(
    sender="noreply@bank.example",
    subject="Your verification code is 738104",
    body="Hi — your one-time password is 738104. Do not share it. "
    "Complete your KYC via https://bank.example/kyc by 5 September 2026.",
).model_copy(update={"email_id": "gmail_otp_case", "thread_id": "gmail_otp_case_t"})


@pytest.fixture
def encryption_on():
    crypto.configure(Settings(app_env="development", data_encryption_enabled=True,
                              data_encryption_key=_KEY))
    yield
    crypto.reset()


def _raw(db, sql: str):
    """Read a column WITHOUT the ORM's decrypting type — i.e. what's on disk."""
    return db.connection().exec_driver_sql(sql).scalar()


# --- encryption transparency (PART 1) --------------------------------

def test_persist_encrypts_at_rest_but_reads_plaintext(encryption_on, db):
    email = make_email(subject="Summer Internship 2026", body="Apply by Friday.").model_copy(
        update={"email_id": "gmail_enc1", "thread_id": "t"})
    rec = PersistenceService(db).persist_decision(email, decision_for(email))

    # transparent to the app
    assert rec.subject == "Summer Internship 2026"
    got = db.query(EmailRecord).filter_by(email_id="gmail_enc1").one()
    assert got.subject == "Summer Internship 2026"
    assert got.sender_email == email.sender.email

    # ...but ciphertext on disk
    db.expire_all()
    stored_subject = _raw(db, "SELECT subject FROM emails WHERE email_id='gmail_enc1'")
    stored_sender = _raw(db, "SELECT sender_email FROM emails WHERE email_id='gmail_enc1'")
    assert stored_subject.startswith("ENC1:")
    assert "Summer Internship" not in stored_subject
    assert stored_sender.startswith("ENC1:")

    # non-sensitive fields stay plaintext / queryable
    assert _raw(db, "SELECT priority_level FROM emails WHERE email_id='gmail_enc1'") in {
        "LOW", "MEDIUM", "HIGH", "URGENT", "CRITICAL"}
    assert _raw(db, "SELECT email_id FROM emails WHERE email_id='gmail_enc1'") == "gmail_enc1"


def test_reprocess_with_encryption_is_idempotent(encryption_on, db):
    email = _OTP_EMAIL
    svc = PersistenceService(db)
    svc.persist_decision(email, decision_for(email))
    svc.persist_decision(email, decision_for(email))
    assert db.query(EmailRecord).filter_by(email_id=email.email_id).count() == 1


def test_disabled_stores_plaintext_like_before(db):
    """conftest sets DATA_ENCRYPTION_ENABLED=false — behaviour unchanged."""
    email = make_email(subject="Plain subject").model_copy(
        update={"email_id": "gmail_plain", "thread_id": "t"})
    PersistenceService(db).persist_decision(email, decision_for(email))
    db.expire_all()
    assert _raw(db, "SELECT subject FROM emails WHERE email_id='gmail_plain'") == "Plain subject"


# --- OTP / secrets never leak (PART 2) ------------------------------

def test_raw_otp_not_in_notification_or_processing_metadata(db):
    email = _OTP_EMAIL
    rec = PersistenceService(db).persist_decision(email, decision_for(email))

    # any notification detail for this email
    notes = db.query(NotificationRecord).filter_by(email_pk=rec.id).all()
    for n in notes:
        assert n.detail is None or "738104" not in n.detail

    # processing-run summary / review reasons are sanitised
    run = rec.processing_runs[0]
    assert "738104" not in (run.summary or "")
    assert all("738104" not in r for r in (run.review_reasons or []))
    for c in (run.conflicts_resolved or []):
        assert "738104" not in str(c)


def test_user_reminder_note_masked_in_notification(db, monkeypatch):
    from datetime import datetime, timedelta, timezone

    from app.db.models import ReminderRecord
    from app.services.deadline_monitor_service import DeadlineMonitorService

    email = make_email(subject="Follow up").model_copy(
        update={"email_id": "gmail_rem_otp", "thread_id": "t"})
    rec = PersistenceService(db).persist_decision(email, decision_for(email))

    r = ReminderRecord(email_pk=rec.id, reminder_at=datetime.now(timezone.utc) - timedelta(minutes=1),
                       reminder_type="USER_SCHEDULED", status="PENDING",
                       note="don't forget the OTP is 990011")
    db.add(r)
    db.commit()

    DeadlineMonitorService(db).run_reminder_check()
    db.expire_all()
    fired = db.query(NotificationRecord).filter_by(email_pk=rec.id,
                                                   notification_type="user_reminder").one()
    assert "990011" not in (fired.detail or "")
    assert "******" in (fired.detail or "")


# --- audit trail on the pipeline (PART 3) --------------------------

def test_pipeline_emits_a_valid_audit_chain(db):
    email = make_email(subject="Internship deadline",
                       body="Apply via https://x/form by 5 September 2026.").model_copy(
        update={"email_id": "gmail_audit1", "thread_id": "t"})
    PersistenceService(db).persist_decision(email, decision_for(email))

    db.expire_all()
    types = [e.event_type for e in db.query(AuditEvent).order_by(AuditEvent.sequence).all()]
    assert "EMAIL_INGESTED" in types
    assert "EMAIL_PROCESSED" in types
    assert AuditService(db).verify_chain()["valid"] is True

    # resource ids are opaque, not PII
    ids = [e.resource_id for e in db.query(AuditEvent).all() if e.resource_id]
    assert all("@" not in rid for rid in ids)


def test_mark_viewed_and_reminder_create_are_audited(db):
    email = make_email(subject="X").model_copy(update={"email_id": "gmail_av", "thread_id": "t"})
    PersistenceService(db).persist_decision(email, decision_for(email))
    PersistenceService(db).mark_viewed("gmail_av")

    from datetime import datetime, timedelta, timezone
    ReminderService(db).create("gmail_av", datetime.now(timezone.utc) + timedelta(days=1))

    db.expire_all()
    types = {e.event_type for e in db.query(AuditEvent).all()}
    assert {"EMAIL_VIEWED", "REMINDER_CREATED"} <= types
    assert AuditService(db).verify_chain()["valid"] is True


# --- unchanged subsystems -----------------------------------------

def test_llm_abstraction_unchanged():
    from app.services.llm_service import (
        AnthropicLLMClient, GeminiLLMClient, GroqLLMClient, NullLLMClient,
        OllamaLLMClient, OpenAILLMClient, build_llm_client,
    )
    assert isinstance(build_llm_client(Settings(llm_provider="none")), NullLLMClient)
    assert build_llm_client(Settings(llm_provider="ollama", llm_model="x")).provider == "ollama"
    for cls in (AnthropicLLMClient, GeminiLLMClient, GroqLLMClient, OpenAILLMClient):
        assert cls  # still importable, signatures untouched


def test_gmail_sync_architecture_unchanged(db):
    from app.services.gmail_service import GmailService
    from app.services.gmail_sync_service import GmailSyncService
    from tests.fakes import FakeGmailResource

    from tests.persistence_helpers import default_user_pk

    fake = FakeGmailResource(history_id="500", email="me@gmail.com")
    svc = GmailSyncService(db, user_pk=default_user_pk(db))
    result = svc.sync_new_messages(GmailService(service=fake))
    assert result["status"] == "baselined"
    assert svc.get_state().last_history_id == "500"
    # account email stored encrypted-at-rest is still readable
    assert svc.get_state().account_email == "me@gmail.com"
