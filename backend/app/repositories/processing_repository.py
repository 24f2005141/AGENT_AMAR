"""Processing-run (history) data access. Runs are append-only."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ProcessingRun


class ProcessingRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, run: ProcessingRun) -> ProcessingRun:
        self.session.add(run)
        self.session.flush()
        return run

    def list_by_email(self, email_pk: int, *, limit: int = 50) -> list[ProcessingRun]:
        stmt = (
            select(ProcessingRun)
            .where(ProcessingRun.email_pk == email_pk)
            .order_by(ProcessingRun.id.desc())
            .limit(limit)
        )
        return list(self.session.execute(stmt).scalars().all())

    def latest_for(self, email_pk: int) -> ProcessingRun | None:
        stmt = (
            select(ProcessingRun)
            .where(ProcessingRun.email_pk == email_pk)
            .order_by(ProcessingRun.id.desc())
            .limit(1)
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def count_for(self, email_pk: int) -> int:
        return len(self.list_by_email(email_pk, limit=1000))
