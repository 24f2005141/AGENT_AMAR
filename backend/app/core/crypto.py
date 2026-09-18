"""Application-level authenticated encryption of sensitive data at rest.

Phase 14. AES-256-GCM (``cryptography`` library). Used transparently by the
:class:`~app.db.base.EncryptedString` SQLAlchemy column type — agents and API
responses keep seeing plaintext; only the stored bytes are ciphertext.

Envelope stored in the DB column::

    ENC1:<base64url(nonce[12] || ciphertext || gcm_tag[16])>

Design notes
------------
* Key comes ONLY from ``Settings.data_encryption_key`` (env). Never hardcoded.
* ``APP_ENV=production`` + ``DATA_ENCRYPTION_ENABLED=true`` + no/invalid key
  ⇒ :class:`EncryptionConfigError` at startup (fail safe).
* Development with no key ⇒ a built-in **insecure** deterministic key + a loud
  warning. Data is NOT confidential in that mode.
* A random key is **never** auto-generated at startup — that would silently
  orphan previously-encrypted rows. The dev fallback is deterministic for the
  same reason.
* Legacy plaintext (rows written before this phase) is read back unchanged;
  the next write re-persists it encrypted. See :func:`decrypt`.

Key rotation limitation
-----------------------
There is a single active key (no key-id in the envelope). Rotating
``DATA_ENCRYPTION_KEY`` makes existing ciphertext unreadable. To rotate:
run offline with the OLD key, call :func:`reencrypt_all` after swapping in the
NEW key is NOT enough (it can't read old data) — instead decrypt-then-store
while both keys are available, or restore from a plaintext export. This
prototype does not ship a KMS / multi-key envelope.
"""

from __future__ import annotations

import base64
import hashlib
import os
import threading
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import Settings, get_settings
from app.core.logging_setup import secure_logger

logger = secure_logger("agent_amar.crypto")

_ENVELOPE_PREFIX = "ENC1:"
_NONCE_BYTES = 12
_AAD = b"agent-amar/phase14/aes-256-gcm"

# Deterministic, well-known, NOT secret. Only used in non-production when no key
# is configured — so dev data stays readable across restarts without a real key.
_DEV_INSECURE_KEY = hashlib.sha256(
    b"agent-amar::insecure-development-key::do-not-use-in-production"
).digest()


class EncryptionError(Exception):
    """Base class for encryption failures."""


class EncryptionConfigError(EncryptionError):
    """Encryption is misconfigured (e.g. production without a valid key)."""


class DecryptionError(EncryptionError):
    """A value looks encrypted but could not be decrypted (wrong key / tamper)."""


@dataclass(frozen=True)
class _CryptoState:
    enabled: bool
    aesgcm: AESGCM | None
    insecure_dev_key: bool = False


_state: _CryptoState | None = None
_lock = threading.Lock()


# --- key handling --------------------------------------------------------

def decode_key(raw: str) -> bytes | None:
    """Decode a configured key to exactly 32 bytes, or ``None`` if invalid.

    Accepts base64url / standard base64 (with or without padding) and hex.
    """
    raw = (raw or "").strip()
    if not raw:
        return None
    # hex (64 chars)
    if len(raw) == 64:
        try:
            b = bytes.fromhex(raw)
            if len(b) == 32:
                return b
        except ValueError:
            pass
    # base64 / base64url
    for decoder in (base64.urlsafe_b64decode, base64.b64decode):
        try:
            b = decoder(raw + "=" * (-len(raw) % 4))
            if len(b) == 32:
                return b
        except (ValueError, base64.binascii.Error):  # type: ignore[attr-defined]
            continue
    return None


def generate_key() -> str:
    """A fresh base64url 32-byte key (for docs / the key-gen command)."""
    return base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")


# --- lifecycle ---------------------------------------------------------

def configure(settings: Settings | None = None) -> None:
    """(Re)build the cipher from settings. Call once at startup; validates key."""
    global _state
    settings = settings or get_settings()

    if not settings.data_encryption_enabled:
        with _lock:
            _state = _CryptoState(enabled=False, aesgcm=None)
        logger.info("data-at-rest encryption is DISABLED (DATA_ENCRYPTION_ENABLED=false)")
        return

    key = decode_key(settings.data_encryption_key)
    insecure = False
    if key is None:
        if settings.is_production:
            raise EncryptionConfigError(
                "APP_ENV=production with DATA_ENCRYPTION_ENABLED=true requires a valid "
                "DATA_ENCRYPTION_KEY (32 bytes, base64url or hex). Refusing to start."
            )
        insecure = True
        key = _DEV_INSECURE_KEY
        logger.warning(
            "SECURITY: DATA_ENCRYPTION_KEY is not set — using a built-in INSECURE "
            "development key. Stored data is NOT confidential. Set DATA_ENCRYPTION_KEY."
        )

    with _lock:
        _state = _CryptoState(enabled=True, aesgcm=AESGCM(key), insecure_dev_key=insecure)
    logger.info(
        "data-at-rest encryption ENABLED (AES-256-GCM%s)",
        ", INSECURE dev key" if insecure else "",
    )


def reset() -> None:
    """Drop the cached cipher (tests / a config change)."""
    global _state
    with _lock:
        _state = None


def _get() -> _CryptoState:
    if _state is None:
        configure(get_settings())
    assert _state is not None
    return _state


def is_enabled() -> bool:
    return _get().enabled


def using_insecure_dev_key() -> bool:
    return _get().insecure_dev_key


# --- string encryption (used by EncryptedString) ---------------------

def _looks_encrypted(value: str) -> bool:
    return value.startswith(_ENVELOPE_PREFIX)


def encrypt(plaintext: str | None) -> str | None:
    """Return the DB-storable value for ``plaintext``.

    ``None`` / ``""`` pass through. When encryption is disabled the plaintext is
    returned unchanged. Idempotent: an already-encrypted envelope is returned
    as-is.
    """
    if plaintext is None or plaintext == "":
        return plaintext
    state = _get()
    if not state.enabled or state.aesgcm is None:
        return plaintext
    if _looks_encrypted(plaintext):
        return plaintext
    nonce = os.urandom(_NONCE_BYTES)
    ct = state.aesgcm.encrypt(nonce, plaintext.encode("utf-8"), _AAD)
    return _ENVELOPE_PREFIX + base64.urlsafe_b64encode(nonce + ct).decode("ascii")


def decrypt(stored: str | None) -> str | None:
    """Inverse of :func:`encrypt`.

    * ``None`` / ``""`` pass through.
    * A value without the envelope prefix is treated as **legacy plaintext** and
      returned unchanged (backward compatibility).
    * A value with the prefix that fails authentication raises
      :class:`DecryptionError` (wrong key or tampering) — it is never silently
      returned.
    """
    if stored is None or stored == "":
        return stored
    if not _looks_encrypted(stored):
        return stored  # legacy plaintext
    state = _get()
    if state.aesgcm is None:
        raise DecryptionError(
            "encountered an encrypted value but encryption is not configured "
            "(DATA_ENCRYPTION_ENABLED / DATA_ENCRYPTION_KEY)"
        )
    try:
        blob = base64.urlsafe_b64decode(stored[len(_ENVELOPE_PREFIX):])
        nonce, ct = blob[:_NONCE_BYTES], blob[_NONCE_BYTES:]
        return state.aesgcm.decrypt(nonce, ct, _AAD).decode("utf-8")
    except (InvalidTag, ValueError) as exc:
        raise DecryptionError("could not decrypt value (wrong key or tampered ciphertext)") from exc


# --- maintenance -----------------------------------------------------

def reencrypt_all(session) -> dict:
    """Re-write every ``EncryptedString`` column so legacy plaintext rows become
    ciphertext under the current key. Safe to run repeatedly; a no-op when
    encryption is disabled. Returns a per-table row count.

    This is the migration helper for enabling encryption on an existing DB — see
    ``docs/SECURITY.md``. It is NOT a key-rotation tool.
    """
    from app.db.base import EncryptedString  # local import: avoid load-time cycle
    from app.db.models import (
        ActionRecord,
        DeadlineRecord,
        EmailRecord,
        GmailSyncState,
        NotificationRecord,
        ReminderRecord,
    )

    if not is_enabled():
        return {"skipped": "encryption disabled"}

    counts: dict[str, int] = {}
    for model in (EmailRecord, ActionRecord, DeadlineRecord, ReminderRecord,
                  NotificationRecord, GmailSyncState):
        enc_cols = [c.key for c in model.__table__.columns
                    if isinstance(c.type, EncryptedString)]
        rows = session.query(model).all()
        for row in rows:
            for name in enc_cols:
                val = getattr(row, name, None)
                if isinstance(val, str) and val and not val.startswith(_ENVELOPE_PREFIX):
                    setattr(row, name, val)  # dirty → re-emitted → encrypted on flush
        counts[model.__tablename__] = len(rows)
    session.commit()
    return counts
