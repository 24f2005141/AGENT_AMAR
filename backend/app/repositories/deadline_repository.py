"""Deadline record data access."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.models import DeadlineRecord, EmailRecord


class DeadlineRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, email_pk: int, deadline_ref: str) -> DeadlineRecord | None:
        stmt = select(DeadlineRecord).where(
            DeadlineRecord.email_pk == email_pk, DeadlineRecord.deadline_ref == deadline_ref
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def list_by_email(self, email_pk: int) -> list[DeadlineRecord]:
        stmt = select(DeadlineRecord).where(DeadlineRecord.email_pk == email_pk).order_by(DeadlineRecord.id)
        return list(self.session.execute(stmt).scalars().all())

    def list_upcoming(
        self, *, within_hours: int | None = None, limit: int = 100
    ) -> list[tuple[DeadlineRecord, EmailRecord]]:
        now = datetime.now(timezone.utc)
        stmt = (
            select(DeadlineRecord, EmailRecord)
            .join(EmailRecord, DeadlineRecord.email_pk == EmailRecord.id)
            .where(
                DeadlineRecord.deadline_datetime.is_not(None),
                DeadlineRecord.is_past.is_(False),
                EmailRecord.is_completed.is_(False),
            )
            .order_by(DeadlineRecord.deadline_datetime)
            .limit(max(1, min(limit, 500)))
        )
        if within_hours is not None:
            horizon = now + timedelta(hours=within_hours)
            stmt = stmt.where(DeadlineRecord.deadline_datetime <= horizon)
        return [tuple(row) for row in self.session.execute(stmt).all()]

    def list_monitoring(self) -> list[DeadlineRecord]:
        stmt = select(DeadlineRecord).where(DeadlineRecord.is_monitoring.is_(True))
        return list(self.session.execute(stmt).scalars().all())

    def list_monitored_with_email(self) -> list[tuple[DeadlineRecord, EmailRecord]]:
        """Every actively-monitored deadline + its email (actions eager-loaded)."""
        stmt = (
            select(DeadlineRecord, EmailRecord)
            .join(EmailRecord, DeadlineRecord.email_pk == EmailRecord.id)
            .where(DeadlineRecord.is_monitoring.is_(True))
            .options(selectinload(EmailRecord.actions))
            .order_by(DeadlineRecord.deadline_datetime, DeadlineRecord.id)
        )
        return [tuple(row) for row in self.session.execute(stmt).all()]

    def list_auto_monitor_candidates(self) -> list[tuple[DeadlineRecord, EmailRecord]]:
        """Deadlines that *should* be monitored but aren't yet:
        ``routing.monitor`` was set, the deadline is still open, and monitoring
        was never explicitly stopped. A concrete datetime OR an ambiguous
        deadline both qualify (an ambiguous one gets a single heads-up)."""
        stmt = (
            select(DeadlineRecord, EmailRecord)
            .join(EmailRecord, DeadlineRecord.email_pk == EmailRecord.id)
            .where(
                EmailRecord.should_monitor.is_(True),
                DeadlineRecord.is_monitoring.is_(False),
                DeadlineRecord.monitoring_stopped_at.is_(None),
                EmailRecord.is_completed.is_(False),
                DeadlineRecord.is_past.is_(False),
            )
        )
        rows = [tuple(row) for row in self.session.execute(stmt).all()]
        return [
            (d, e) for d, e in rows
            if d.deadline_datetime is not None or d.is_ambiguous
        ]

    def start_monitoring(self, deadline: DeadlineRecord) -> DeadlineRecord:
        if not deadline.is_monitoring:
            deadline.is_monitoring = True
            deadline.monitoring_started_at = datetime.now(timezone.utc)
            deadline.monitoring_stopped_at = None
            self.session.flush()
        return deadline

    def stop_monitoring(self, deadline: DeadlineRecord) -> DeadlineRecord:
        if deadline.is_monitoring:
            deadline.is_monitoring = False
            deadline.monitoring_stopped_at = datetime.now(timezone.utc)
            self.session.flush()
        return deadline
