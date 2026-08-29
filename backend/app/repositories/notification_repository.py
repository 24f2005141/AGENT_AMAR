"""Notification-event data access. No sending happens here (Phase 10 decides
*what* to alert; the future Flutter layer decides *how*)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.models import EmailRecord, NotificationRecord

_ACTIVE = ("PENDING", "SENT")
# escalation rungs, least → most urgent (mirror of escalation_policy.EscalationLevel)
_ESCALATION_RANK = {"NONE": 0, "NORMAL": 1, "REMINDER": 2, "URGENT": 3, "ALARM": 4}


class NotificationRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    # -- create ---------------------------------------------------------

    def create(
        self,
        *,
        email_pk: int,
        notification_type: str,
        deadline_pk: int | None = None,
        reminder_pk: int | None = None,
        reminder_level: str | None = None,
        severity: str | None = None,
        status: str = "PENDING",
        detail: str | None = None,
        requires_alarm: bool = False,
        sent_at: datetime | None = None,
    ) -> NotificationRecord:
        record = NotificationRecord(
            email_pk=email_pk,
            deadline_pk=deadline_pk,
            reminder_pk=reminder_pk,
            notification_type=notification_type,
            reminder_level=reminder_level,
            severity=severity or reminder_level or "NORMAL",
            status=status,
            detail=detail,
            requires_alarm=requires_alarm,
            sent_at=sent_at,
        )
        self.session.add(record)
        self.session.flush()
        return record

    def create_pending(
        self,
        email_pk: int,
        notification_type: str = "new_priority_email",
        *,
        reminder_level: str | None = None,
        detail: str | None = None,
    ) -> NotificationRecord:
        """Back-compat helper used by the Phase 9 PersistenceService."""
        return self.create(
            email_pk=email_pk,
            notification_type=notification_type,
            reminder_level=reminder_level,
            detail=detail,
        )

    # -- dedup / escalation-state queries ----------------------------

    def exists_for(
        self,
        email_pk: int,
        notification_type: str,
        *,
        statuses: tuple[str, ...] = _ACTIVE,
    ) -> bool:
        stmt = select(NotificationRecord.id).where(
            NotificationRecord.email_pk == email_pk,
            NotificationRecord.notification_type == notification_type,
            NotificationRecord.status.in_(statuses),
        )
        return self.session.execute(stmt).first() is not None

    def exists_for_deadline(
        self,
        deadline_pk: int,
        notification_type: str,
        *,
        reminder_level: str | None = None,
        statuses: tuple[str, ...] = _ACTIVE,
    ) -> bool:
        stmt = select(NotificationRecord.id).where(
            NotificationRecord.deadline_pk == deadline_pk,
            NotificationRecord.notification_type == notification_type,
            NotificationRecord.status.in_(statuses),
        )
        if reminder_level is not None:
            stmt = stmt.where(NotificationRecord.reminder_level == reminder_level)
        return self.session.execute(stmt).first() is not None

    def highest_escalation_for(self, deadline_pk: int) -> str:
        """The most-urgent escalation rung already issued for this deadline
        (PENDING or SENT ``deadline_escalation`` events). ``"NONE"`` if none."""
        stmt = select(NotificationRecord.reminder_level).where(
            NotificationRecord.deadline_pk == deadline_pk,
            NotificationRecord.notification_type == "deadline_escalation",
            NotificationRecord.status.in_(_ACTIVE),
        )
        levels = [r for (r,) in self.session.execute(stmt).all() if r]
        return max(levels, key=lambda lv: _ESCALATION_RANK.get(lv, 0), default="NONE")

    # -- listing ----------------------------------------------------

    def get(self, notification_id: int) -> NotificationRecord | None:
        return self.session.get(NotificationRecord, notification_id)

    def list_by_email(self, email_pk: int) -> list[NotificationRecord]:
        stmt = (
            select(NotificationRecord)
            .where(NotificationRecord.email_pk == email_pk)
            .order_by(NotificationRecord.id)
        )
        return list(self.session.execute(stmt).scalars().all())

    def list_unsent(self, *, limit: int = 200) -> list[NotificationRecord]:
        stmt = (
            select(NotificationRecord)
            .where(NotificationRecord.status == "PENDING")
            .order_by(NotificationRecord.id)
            .limit(limit)
        )
        return list(self.session.execute(stmt).scalars().all())

    def list(
        self,
        *,
        status: str | None = None,
        severity: str | None = None,
        notification_type: str | None = None,
        email_id: str | None = None,
        created_after: datetime | None = None,
        requires_alarm: bool | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[NotificationRecord]:
        stmt = select(NotificationRecord).options(selectinload(NotificationRecord.email))
        if email_id is not None:
            stmt = stmt.join(EmailRecord, NotificationRecord.email_pk == EmailRecord.id).where(
                EmailRecord.email_id == email_id
            )
        if status is not None:
            stmt = stmt.where(NotificationRecord.status == status.upper())
        if severity is not None:
            stmt = stmt.where(NotificationRecord.severity == severity.upper())
        if notification_type is not None:
            stmt = stmt.where(NotificationRecord.notification_type == notification_type)
        if created_after is not None:
            stmt = stmt.where(NotificationRecord.created_at >= created_after)
        if requires_alarm is not None:
            stmt = stmt.where(NotificationRecord.requires_alarm.is_(requires_alarm))
        stmt = stmt.order_by(NotificationRecord.id.desc())
        stmt = stmt.limit(max(1, min(limit, 500))).offset(max(0, offset))
        return list(self.session.execute(stmt).scalars().all())
