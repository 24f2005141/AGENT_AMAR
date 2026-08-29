"""Gmail sync-state data access (Phase 12)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import SYNC_DEFAULT_USER, GmailSyncState


class GmailSyncRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, user_id: str = SYNC_DEFAULT_USER) -> GmailSyncState | None:
        stmt = select(GmailSyncState).where(GmailSyncState.user_id == user_id)
        return self.session.execute(stmt).scalar_one_or_none()

    def get_or_create(self, user_id: str = SYNC_DEFAULT_USER) -> GmailSyncState:
        state = self.get(user_id)
        if state is None:
            state = GmailSyncState(user_id=user_id)
            self.session.add(state)
            self.session.flush()
        return state
