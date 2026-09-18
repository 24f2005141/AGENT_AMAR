"""Phase 14 — application-level AES-256-GCM encryption (app.core.crypto)."""

from __future__ import annotations

import base64

import pytest

from app.core import crypto
from app.core.config import Settings

KEY = crypto.generate_key()  # a fresh base64url 32-byte key


def _enable(key: str = KEY, *, env: str = "development") -> None:
    crypto.configure(Settings(app_env=env, data_encryption_enabled=True, data_encryption_key=key))


def _disable() -> None:
    crypto.configure(Settings(data_encryption_enabled=False))


# --- key handling ----------------------------------------------------------

def test_generate_key_is_32_bytes_base64url():
    raw = base64.urlsafe_b64decode(crypto.generate_key())
    assert len(raw) == 32


@pytest.mark.parametrize("bad", ["", "too-short", "x" * 100, base64.urlsafe_b64encode(b"only16bytes......").decode()])
def test_decode_key_rejects_bad(bad):
    assert crypto.decode_key(bad) is None


def test_decode_key_accepts_hex_and_base64():
    import os
    raw = os.urandom(32)
    assert crypto.decode_key(raw.hex()) == raw
    assert crypto.decode_key(base64.urlsafe_b64encode(raw).decode()) == raw
    assert crypto.decode_key(base64.b64encode(raw).decode()) == raw


# --- roundtrip -----------------------------------------------------------

def test_encrypt_decrypt_roundtrip():
    _enable()
    for pt in ("Your verification code is 483921", "über — unicode ✓", "x" * 5000, "a"):
        ct = crypto.encrypt(pt)
        assert ct != pt
        assert ct.startswith("ENC1:")
        assert crypto.decrypt(ct) == pt


def test_none_and_empty_pass_through():
    _enable()
    assert crypto.encrypt(None) is None
    assert crypto.encrypt("") == ""
    assert crypto.decrypt(None) is None
    assert crypto.decrypt("") == ""


def test_same_plaintext_encrypts_to_different_ciphertext():
    _enable()
    a = crypto.encrypt("Summer Internship 2026")
    b = crypto.encrypt("Summer Internship 2026")
    assert a != b                       # random nonce per call
    assert crypto.decrypt(a) == crypto.decrypt(b) == "Summer Internship 2026"


def test_encrypt_is_idempotent_on_an_envelope():
    _enable()
    once = crypto.encrypt("hello")
    assert crypto.encrypt(once) == once  # not double-wrapped


# --- tamper / wrong key ------------------------------------------------

def test_tampered_ciphertext_fails_closed():
    _enable()
    ct = crypto.encrypt("sensitive")
    tampered = ct[:-4] + ("AAAA" if ct[-4:] != "AAAA" else "BBBB")
    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt(tampered)


def test_wrong_key_fails_closed():
    _enable()
    ct = crypto.encrypt("sensitive")
    _enable(crypto.generate_key())      # rotate to a different key
    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt(ct)


def test_garbage_envelope_fails_closed():
    _enable()
    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt("ENC1:not-valid-base64!!!")


# --- disabled / legacy plaintext -------------------------------------

def test_disabled_is_passthrough():
    _disable()
    assert crypto.is_enabled() is False
    assert crypto.encrypt("plain subject") == "plain subject"
    assert crypto.decrypt("plain subject") == "plain subject"


def test_legacy_plaintext_is_read_back_unchanged():
    """Rows written before encryption was enabled have no envelope prefix."""
    _enable()
    assert crypto.decrypt("a legacy plaintext subject") == "a legacy plaintext subject"


# --- config validation (PART 4) -------------------------------------

def test_production_requires_a_valid_key():
    with pytest.raises(crypto.EncryptionConfigError):
        crypto.configure(Settings(app_env="production", data_encryption_enabled=True,
                                  data_encryption_key=""))
    with pytest.raises(crypto.EncryptionConfigError):
        crypto.configure(Settings(app_env="production", data_encryption_enabled=True,
                                  data_encryption_key="not-a-real-key"))


def test_production_with_valid_key_configures():
    crypto.configure(Settings(app_env="production", data_encryption_enabled=True,
                              data_encryption_key=KEY))
    assert crypto.is_enabled() is True
    assert crypto.using_insecure_dev_key() is False


def test_development_without_key_uses_insecure_fallback_with_warning(caplog):
    with caplog.at_level("WARNING", logger="agent_amar.crypto"):
        crypto.configure(Settings(app_env="development", data_encryption_enabled=True,
                                  data_encryption_key=""))
    assert crypto.is_enabled() is True
    assert crypto.using_insecure_dev_key() is True
    assert any("INSECURE" in r.message for r in caplog.records)


def test_dev_fallback_key_is_deterministic():
    """Deterministic → dev data stays readable across restarts without a key.
    (A random key would silently orphan previously-encrypted rows.)"""
    crypto.configure(Settings(app_env="development", data_encryption_enabled=True))
    ct = crypto.encrypt("dev data")
    crypto.reset()
    crypto.configure(Settings(app_env="development", data_encryption_enabled=True))
    assert crypto.decrypt(ct) == "dev data"
