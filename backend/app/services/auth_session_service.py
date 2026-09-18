"""Application identity + session tokens (Phase 15).

    Google ID token  ─►  upsert_user()   ─►  User (keyed by google_sub)
    User             ─►  create_session()─►  (raw bearer token, AppSession row)
    bearer token     ─►  resolve()       ─►  User  (or None)

The raw bearer token is returned to the client exactly once and **never stored** —
only ``sha256(raw_token)`` lives in the DB, so a database leak does not hand an
attacker usable sessions.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.base import utcnow
from app.db.models import AppSession, User
from app.services.google_identity import GoogleIdentity

_TOKEN_BYTES = 32


def hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class AuthSessionService:
    def __init__(self, session: Session, *, settings: Settings | None = None) -> None:
        self.session = session
        self.settings = settings or get_settings()

    # -- users --------------------------------------------------------

    def get_user(self, user_id: int) -> User | None:
        return self.session.get(User, user_id)

    def upsert_user(self, identity: GoogleIdentity) -> User:
        """Find the user by stable ``google_sub`` (falling back to email for a
        legacy row), refreshing the email / display name. Creates one if new."""
        user = self.session.execute(
            select(User).where(User.google_sub == identity.sub)
        ).scalar_one_or_none()

        if user is None and identity.email:
            legacy = self.session.execute(
                select(User).where(User.google_sub == f"legacy:{identity.email}")
            ).scalar_one_or_none()
            if legacy is not None:
                legacy.google_sub = identity.sub  # adopt the real sub
                user = legacy

        if user is None:
            user = User(google_sub=identity.sub)
            self.session.add(user)

        if identity.email:
            user.google_email = identity.email
        if identity.name:
            user.display_name = identity.name
        self.session.flush()
        return user

    # -- sessions ---------------------------------------------------

    def create_session(self, user: User) -> tuple[str, AppSession]:
        raw_token = secrets.token_urlsafe(_TOKEN_BYTES)
        now = utcnow()
        row = AppSession(
            user_pk=user.id,
            token_hash=hash_token(raw_token),
            last_used_at=now,
            expires_at=now + timedelta(days=max(1, self.settings.session_ttl_days)),
        )
        self.session.add(row)
        self.session.flush()
        return raw_token, row

    def resolve(self, raw_token: str | None) -> User | None:
        """Return the live user for a bearer token, or ``None`` if the token is
        unknown / revoked / expired. Bumps ``last_used_at``."""
        if not raw_token:
            return None
        row = self.session.execute(
            select(AppSession).where(AppSession.token_hash == hash_token(raw_token))
        ).scalar_one_or_none()
        if row is None or row.revoked:
            return None
        if _aware(row.expires_at) is not None and _aware(row.expires_at) <= utcnow():
            return None
        row.last_used_at = utcnow()
        self.session.flush()
        return self.session.get(User, row.user_pk)

    def revoke(self, raw_token: str | None) -> bool:
        if not raw_token:
            return False
        row = self.session.execute(
            select(AppSession).where(AppSession.token_hash == hash_token(raw_token))
        ).scalar_one_or_none()
        if row is None:
            return False
        row.revoked = True
        self.session.flush()
        return True

    def revoke_all_for_user(self, user_pk: int) -> None:
        for row in self.session.execute(
            select(AppSession).where(AppSession.user_pk == user_pk, AppSession.revoked.is_(False))
        ).scalars():
            row.revoked = True
        self.session.flush()
