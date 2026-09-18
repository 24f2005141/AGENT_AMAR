"""Helpers for the Phase 15 multi-user tests."""

from __future__ import annotations

from app.db import session as db_session
from app.db.models import User
from app.services.auth_session_service import AuthSessionService


def seed_user(*, sub: str, email: str | None = None, name: str | None = None) -> int:
    """Insert a User and return its id."""
    with db_session.db_session() as s:
        user = User(google_sub=sub, google_email=email or f"{sub}@example.com",
                    display_name=name)
        s.add(user)
        s.commit()
        s.refresh(user)
        return user.id


def issue_token(user_id: int) -> str:
    """Create a real application session for ``user_id`` and return the bearer."""
    with db_session.db_session() as s:
        user = s.get(User, user_id)
        raw, _ = AuthSessionService(s).create_session(user)
        s.commit()
        return raw


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
