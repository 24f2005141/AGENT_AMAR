"""Browser→app authentication handoff (Phase 17).

The Google OAuth callback runs in a system browser / Custom Tab; the Flutter app
needs the sign-in result back. Rather than parking the user on a backend page and
having the app poll, the callback mints a short-lived, single-use **handoff code**
and 302-redirects to the app's deep link (``agentamar://auth/callback?code=…``).

The code carries nothing sensitive — only a reference to the user who just
authenticated. The app exchanges it once at ``POST /api/v1/auth/session/exchange``
which mints the real opaque application session (reusing
:class:`~app.services.auth_session_service.AuthSessionService`).

Security properties:
  * cryptographically random (``secrets.token_urlsafe``)
  * only ``sha256(code)`` stored — a DB leak yields no usable codes
  * short TTL (``settings.auth_handoff_ttl_seconds``)
  * single use — consumed atomically; a replay finds it already consumed
  * bound to the OAuth ``state`` — a code cannot be redeemed with the wrong state
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.base import utcnow
from app.db.models import AuthHandoff

_CODE_BYTES = 32
_MIN_TTL_SECONDS = 30


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class AuthHandoffService:
    def __init__(self, session: Session, *, settings: Settings | None = None) -> None:
        self.session = session
        self.settings = settings or get_settings()

    def create(self, user_pk: int, *, oauth_state: str | None = None) -> str:
        """Mint a new handoff code for ``user_pk`` and return the raw value
        (given to the app once, via the deep-link URL)."""
        self._prune()
        raw = secrets.token_urlsafe(_CODE_BYTES)
        ttl = max(_MIN_TTL_SECONDS, int(self.settings.auth_handoff_ttl_seconds or 0))
        self.session.add(
            AuthHandoff(
                code_hash=_hash(raw),
                user_pk=user_pk,
                oauth_state=oauth_state,
                expires_at=utcnow() + timedelta(seconds=ttl),
            )
        )
        self.session.flush()
        return raw

    def redeem(self, raw_code: str | None, *, oauth_state: str | None = None) -> int | None:
        """Atomically consume ``raw_code``. Returns the authenticated user id, or
        ``None`` when the code is unknown / expired / already used / bound to a
        different OAuth state."""
        if not raw_code:
            return None
        row = self.session.execute(
            select(AuthHandoff).where(AuthHandoff.code_hash == _hash(raw_code))
        ).scalar_one_or_none()
        if row is None or row.consumed_at is not None:
            return None
        expires = _aware(row.expires_at)
        if expires is not None and expires <= utcnow():
            return None
        if (
            oauth_state is not None
            and row.oauth_state is not None
            and oauth_state != row.oauth_state
        ):
            return None
        # single-use claim — succeeds for exactly one caller; a concurrent replay
        # (or a second attempt) matches 0 rows.
        claimed = self.session.execute(
            update(AuthHandoff)
            .where(AuthHandoff.id == row.id, AuthHandoff.consumed_at.is_(None))
            .values(consumed_at=utcnow())
        )
        if claimed.rowcount != 1:
            return None
        self.session.flush()
        return row.user_pk

    def _prune(self) -> None:
        """Drop handoffs that have been expired for a while (housekeeping)."""
        self.session.execute(
            delete(AuthHandoff).where(
                AuthHandoff.expires_at < utcnow() - timedelta(hours=1)
            )
        )
