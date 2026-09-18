"""Phase 15 — AuthSessionService: user upsert + opaque session tokens."""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.config import Settings
from app.db import session as db_session
from app.db.base import utcnow
from app.db.models import AppSession, User
from app.services.auth_session_service import AuthSessionService, hash_token
from app.services.google_identity import GoogleIdentity


@pytest.fixture
def svc(db):
    return AuthSessionService(db, settings=Settings(session_ttl_days=7))


def test_upsert_creates_then_updates(db, svc):
    u1 = svc.upsert_user(GoogleIdentity(sub="sub-1", email="a@x.com", name="A"))
    db.commit()
    assert u1.id is not None and u1.google_email == "a@x.com"

    # same sub, changed email/name -> same row, updated fields
    u2 = svc.upsert_user(GoogleIdentity(sub="sub-1", email="a2@x.com", name="A2"))
    db.commit()
    assert u2.id == u1.id
    assert u2.google_email == "a2@x.com" and u2.display_name == "A2"
    assert db.query(User).filter(User.google_sub == "sub-1").count() == 1


def test_adopts_legacy_row_by_email(db, svc):
    legacy = User(google_sub="legacy:person@x.com", google_email="person@x.com")
    db.add(legacy)
    db.commit()

    adopted = svc.upsert_user(GoogleIdentity(sub="real-sub-9", email="person@x.com"))
    db.commit()
    assert adopted.id == legacy.id
    assert adopted.google_sub == "real-sub-9"


def test_session_roundtrip_and_hashing(db, svc):
    user = svc.upsert_user(GoogleIdentity(sub="s", email="s@x.com"))
    db.commit()
    raw, row = svc.create_session(user)
    db.commit()

    # raw token is never stored
    assert db.query(AppSession).filter(AppSession.token_hash == raw).count() == 0
    assert row.token_hash == hash_token(raw)

    resolved = svc.resolve(raw)
    assert resolved is not None and resolved.id == user.id
    assert svc.resolve("nonsense") is None
    assert svc.resolve(None) is None


def test_expired_and_revoked_tokens_do_not_resolve(db, svc):
    user = svc.upsert_user(GoogleIdentity(sub="s2", email="s2@x.com"))
    db.commit()
    raw, row = svc.create_session(user)
    row.expires_at = utcnow() - timedelta(seconds=1)
    db.commit()
    assert svc.resolve(raw) is None

    raw2, _ = svc.create_session(user)
    db.commit()
    assert svc.resolve(raw2) is not None
    svc.revoke(raw2)
    db.commit()
    assert svc.resolve(raw2) is None


def test_revoke_all_for_user(db, svc):
    user = svc.upsert_user(GoogleIdentity(sub="s3", email="s3@x.com"))
    db.commit()
    a, _ = svc.create_session(user)
    b, _ = svc.create_session(user)
    db.commit()
    svc.revoke_all_for_user(user.id)
    db.commit()
    assert svc.resolve(a) is None and svc.resolve(b) is None
