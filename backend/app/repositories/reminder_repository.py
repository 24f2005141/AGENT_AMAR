"""User-scheduled reminder data access (Phase 10)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import EmailRecord, ReminderRecord


class ReminderRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, reminder: ReminderRecord) -> ReminderRecord:
        self.session.add(reminder)
        self.session.flush()
        return reminder

    def get(self, reminder_id: int) -> ReminderRecord | None:
        return self.session.get(ReminderRecord, reminder_id)

    def list_by_email(self, email_pk: int) -> list[ReminderRecord]:
        stmt = (
            select(ReminderRecord)
            .where(ReminderRecord.email_pk == email_pk)
            .order_by(ReminderRecord.reminder_at)
        )
        return list(self.session.execute(stmt).scalars().all())

    def list_all(
        self, *, status: str | None = None, limit: int = 100, offset: int = 0
    ) -> list[tuple[ReminderRecord, str]]:
        """Every reminder + its email_id, newest scheduled first. Frontend
        Reminders screen (there is no per-email context here)."""
        stmt = (
            select(ReminderRecord, EmailRecord.email_id)
            .join(EmailRecord, ReminderRecord.email_pk == EmailRecord.id)
            .order_by(ReminderRecord.reminder_at.desc(), ReminderRecord.id.desc())
        )
        if status is not None:
            stmt = stmt.where(ReminderRecord.status == status.upper())
        stmt = stmt.limit(max(1, min(limit, 500))).offset(max(0, offset))
        return [tuple(row) for row in self.session.execute(stmt).all()]

    def list_due(self, now: datetime, *, limit: int = 200) -> list[ReminderRecord]:
        """PENDING reminders whose time has arrived."""
        stmt = (
            select(ReminderRecord)
            .where(
                ReminderRecord.status == "PENDING",
                ReminderRecord.reminder_at <= now,
            )
            .order_by(ReminderRecord.reminder_at)
            .limit(limit)
        )
        return list(self.session.execute(stmt).scalars().all())
