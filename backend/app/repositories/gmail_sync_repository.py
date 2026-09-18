"""Gmail sync-state data access (Phase 12; per-user since Phase 15)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import GmailSyncState


class GmailSyncRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, user_pk: int) -> GmailSyncState | None:
        stmt = select(GmailSyncState).where(GmailSyncState.user_pk == user_pk)
        return self.session.execute(stmt).scalar_one_or_none()

    def get_or_create(self, user_pk: int) -> GmailSyncState:
        state = self.get(user_pk)
        if state is None:
            state = GmailSyncState(user_pk=user_pk)
            self.session.add(state)
            self.session.flush()
        return state

    def all_states(self) -> list[GmailSyncState]:
        return list(self.session.execute(select(GmailSyncState)).scalars().all())
