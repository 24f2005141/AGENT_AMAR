"""Audit-chain data access (Phase 14). Append-only; never UPDATE/DELETE."""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import AuditEvent


class AuditRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def last(self) -> AuditEvent | None:
        stmt = select(AuditEvent).order_by(AuditEvent.sequence.desc()).limit(1)
        return self.session.execute(stmt).scalar_one_or_none()

    def max_sequence(self) -> int:
        return int(self.session.execute(select(func.max(AuditEvent.sequence))).scalar() or 0)

    def count(self) -> int:
        return int(self.session.execute(select(func.count(AuditEvent.id))).scalar() or 0)

    def add(self, event: AuditEvent) -> AuditEvent:
        self.session.add(event)
        self.session.flush()
        return event

    def iter_chain(self) -> Iterator[AuditEvent]:
        """Every event, ascending by sequence (the chain order)."""
        stmt = select(AuditEvent).order_by(AuditEvent.sequence.asc())
        yield from self.session.execute(stmt).scalars()

    def list_recent(
        self, *, limit: int = 100, offset: int = 0, user_pk: int | None = None
    ) -> list[AuditEvent]:
        stmt = select(AuditEvent).order_by(AuditEvent.sequence.desc())
        if user_pk is not None:
            stmt = stmt.where(AuditEvent.user_pk == user_pk)
        stmt = stmt.limit(max(1, min(limit, 500))).offset(max(0, offset))
        return list(self.session.execute(stmt).scalars().all())

    def count_for_user(self, user_pk: int) -> int:
        return int(
            self.session.execute(
                select(func.count(AuditEvent.id)).where(AuditEvent.user_pk == user_pk)
            ).scalar()
            or 0
        )
