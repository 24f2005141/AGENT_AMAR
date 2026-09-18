"""Phase 14 — tamper-evident hash-chained audit ledger."""

from __future__ import annotations

from app.db.models import GENESIS_HASH, AuditEvent
from app.services.audit_service import AuditService, audit_record, audit_record_many


def _record(**kw):
    return audit_record(kw.pop("event_type", "EMAIL_PROCESSED"),
                        kw.pop("resource_type", "email"), **kw)


# --- linking -----------------------------------------------------------

def test_records_form_a_linked_chain(db):
    _record(resource_id="gmail_1")
    _record(resource_id="gmail_2")
    _record(resource_id="gmail_3")

    db.expire_all()
    events = AuditService(db).recent(limit=100)
    events = sorted(events, key=lambda e: e.sequence)

    assert [e.sequence for e in events] == [1, 2, 3]
    assert events[0].previous_hash == GENESIS_HASH
    assert events[1].previous_hash == events[0].record_hash
    assert events[2].previous_hash == events[1].record_hash
    assert len({e.record_hash for e in events}) == 3  # all distinct


def test_batch_append_is_still_a_valid_chain(db):
    audit_record_many([
        {"event_type": "EMAIL_INGESTED", "resource_type": "email", "resource_id": "gmail_x"},
        {"event_type": "ACTION_CREATED", "resource_type": "action", "resource_id": "gmail_x/act_001"},
        {"event_type": "DEADLINE_CREATED", "resource_type": "deadline", "resource_id": "gmail_x/dl_001"},
    ])
    db.expire_all()
    result = AuditService(db).verify_chain()
    assert result == {"valid": True, "records_checked": 3,
                      "first_invalid_record": None, "reason": None}


# --- verification: valid --------------------------------------------

def test_verify_succeeds_for_an_untouched_chain(db):
    for i in range(6):
        _record(resource_id=f"gmail_{i}")
    db.expire_all()
    result = AuditService(db).verify_chain()
    assert result["valid"] is True
    assert result["records_checked"] == 6
    assert result["first_invalid_record"] is None


def test_verify_empty_chain_is_valid(db):
    result = AuditService(db).verify_chain()
    assert result["valid"] is True and result["records_checked"] == 0


# --- verification: tamper detection --------------------------------

def test_modifying_a_record_breaks_verification(db):
    _record(resource_id="gmail_a")
    aid = _record(resource_id="gmail_b")
    _record(resource_id="gmail_c")

    db.expire_all()
    target = db.query(AuditEvent).filter_by(audit_id=aid).one()
    target.event_type = "EMAIL_VIEWED"          # silently change a field
    target.detail = {"tampered": True}
    db.commit()

    result = AuditService(db).verify_chain()
    assert result["valid"] is False
    assert result["first_invalid_record"] == aid
    assert "record_hash mismatch" in result["reason"]


def test_breaking_previous_hash_linkage_is_detected(db):
    _record(resource_id="gmail_a")
    aid = _record(resource_id="gmail_b")

    db.expire_all()
    target = db.query(AuditEvent).filter_by(audit_id=aid).one()
    target.previous_hash = "f" * 64
    db.commit()

    result = AuditService(db).verify_chain()
    assert result["valid"] is False
    assert result["first_invalid_record"] == aid
    assert "previous_hash" in result["reason"]


def test_deleting_a_record_creates_a_sequence_gap(db):
    ids = [_record(resource_id=f"gmail_{i}") for i in range(4)]

    db.expire_all()
    db.query(AuditEvent).filter_by(audit_id=ids[1]).delete()
    db.commit()

    result = AuditService(db).verify_chain()
    assert result["valid"] is False
    assert "sequence gap" in result["reason"]


def test_swapping_a_record_hash_is_detected(db):
    _record(resource_id="gmail_a")
    aid = _record(resource_id="gmail_b")
    db.expire_all()
    target = db.query(AuditEvent).filter_by(audit_id=aid).one()
    target.record_hash = "0" * 64
    db.commit()
    assert AuditService(db).verify_chain()["valid"] is False


# --- no sensitive data (PART 3) -----------------------------------

def test_audit_records_never_contain_email_content_or_secrets(db):
    # even if a caller mistakenly passes secrets in `detail`, they are sanitised
    audit_record("EMAIL_PROCESSED", "email", resource_id="gmail_secretcheck",
                 detail={"subject": "Your OTP is 224466", "api_key": "sk-abc123def456ghi",
                         "password": "hunter2", "category": "EXAM"})
    db.expire_all()
    ev = db.query(AuditEvent).filter_by(resource_id="gmail_secretcheck").one()
    blob = f"{ev.detail} {ev.event_type} {ev.resource_type} {ev.resource_id}"
    for leak in ("224466", "sk-abc123def456ghi", "hunter2"):
        assert leak not in blob
    assert ev.detail["category"] == "EXAM"  # benign metadata kept
    # the model has no column for body/subject/sender/token
    cols = {c.key for c in AuditEvent.__table__.columns}
    assert cols.isdisjoint({"body", "subject", "sender", "sender_email", "token",
                            "access_token", "refresh_token", "otp"})


def test_verify_result_exposes_no_sensitive_fields(db):
    _record(resource_id="gmail_1")
    result = AuditService(db).verify_chain()
    assert set(result) == {"valid", "records_checked", "first_invalid_record", "reason"}
