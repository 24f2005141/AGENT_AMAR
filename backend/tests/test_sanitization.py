"""Phase 14 — sensitive-data detection, masking, and log redaction."""

from __future__ import annotations

import logging

import pytest

from app.core.logging_setup import secure_logger
from app.core.sanitization import contains_sensitive, mask_sensitive, redact_mapping

MASK = "******"


# --- OTP / verification codes -------------------------------------------

@pytest.mark.parametrize(
    "text",
    [
        "Your verification code is 483921",
        "OTP: 8391",
        "Your one-time password is 12345",
        "Enter code 90-2214 to continue",
        "security code = 552104",
        "Your login code is 483921. It expires in 10 minutes.",
    ],
)
def test_masks_otp(text):
    out = mask_sensitive(text)
    assert MASK in out
    # no run of 4+ consecutive digits survives
    import re
    assert re.search(r"\d{4,}", out.replace(MASK, "")) is None, out
    assert contains_sensitive(text) is True


def test_otp_example_from_the_brief():
    assert mask_sensitive("Your verification code is 483921") == "Your verification code is ******"


def test_keeps_ordinary_numbers():
    for benign in ("The meeting is at 5 PM in room 214",
                   "Assignment 3 is due on 12 October 2026",
                   "Priority score 75"):
        assert mask_sensitive(benign) == benign
        assert contains_sensitive(benign) is False


# --- passwords / tokens / keys -----------------------------------------

@pytest.mark.parametrize(
    "text",
    [
        "password: hunter2yz",
        'pwd = "S3cr3tP@ss"',
        "Authorization: Bearer abcdef1234567890ABCDEF",
        "api_key=sk-proj-abc123def456ghi789jkl",
        "here is my token: ghp_1234567890abcdefABCDEF1234",
        "refresh_token=1//0eXaMpLeReFrEsHtOkEnValueHere123",
        "AKIAIOSFODNN7EXAMPLE is the access key id",
        "jwt eyJhbGciOi.eyJzdWIiOiIxMjM0.SflKxwRJSMeKKF2QT4",
    ],
)
def test_masks_tokens_and_secrets(text):
    out = mask_sensitive(text)
    assert MASK in out
    assert contains_sensitive(text) is True
    # the secret span itself is gone
    for leak in ("hunter2yz", "S3cr3tP@ss", "abcdef1234567890ABCDEF",
                 "sk-proj-abc123def456ghi789jkl", "ghp_1234567890abcdefABCDEF1234",
                 "1//0eXaMpLeReFrEsHtOkEnValueHere123", "AKIAIOSFODNN7EXAMPLE",
                 "SflKxwRJSMeKKF2QT4"):
        assert leak not in out


def test_google_oauth_tokens_masked():
    assert "ya29.a0Ae4lvC" not in mask_sensitive("token ya29.a0Ae4lvC1234567890abcdefghij")


# --- redact_mapping ---------------------------------------------------

def test_redact_mapping_hides_sensitive_keys_and_values():
    d = {
        "email_id": "gmail_123",
        "password": "letmein99",
        "nested": {"access_token": "abc", "count": 3},
        "note": "Your OTP is 224466",
        "items": [{"api_key": "sk-xyz"}, "code 998877"],
    }
    r = redact_mapping(d)
    assert r["email_id"] == "gmail_123"
    assert r["password"] == MASK
    assert r["nested"]["access_token"] == MASK
    assert r["nested"]["count"] == 3
    assert MASK in r["note"] and "224466" not in r["note"]
    assert r["items"][0]["api_key"] == MASK
    assert "998877" not in r["items"][1]


def test_none_passthrough():
    assert mask_sensitive(None) is None
    assert mask_sensitive(123) == 123  # type: ignore[arg-type]


# --- log redaction filter (PART 5) ----------------------------------

def test_secure_logger_redacts_otp(caplog):
    log = secure_logger("agent_amar.test_sanitization")
    with caplog.at_level(logging.INFO, logger="agent_amar.test_sanitization"):
        log.info("Delivering: Your verification code is 483921")
        log.warning("token=%s failed", "ghp_verysecrettokenvalue1234567")
    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert "483921" not in joined
    assert "ghp_verysecrettokenvalue1234567" not in joined
    assert MASK in joined


def test_secure_logger_keeps_useful_text(caplog):
    log = secure_logger("agent_amar.test_sanitization2")
    with caplog.at_level(logging.INFO, logger="agent_amar.test_sanitization2"):
        log.info("gmail sync completed: new=2 processed=2 (history 100 -> 130)")
    assert "gmail sync completed: new=2 processed=2 (history 100 -> 130)" in caplog.text
