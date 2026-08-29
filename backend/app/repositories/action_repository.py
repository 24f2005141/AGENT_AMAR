"""Action record data access."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ActionRecord, EmailRecord


class ActionRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, email_pk: int, action_ref: str) -> ActionRecord | None:
        stmt = select(ActionRecord).where(
            ActionRecord.email_pk == email_pk, ActionRecord.action_ref == action_ref
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def list_by_email(self, email_pk: int) -> list[ActionRecord]:
        stmt = select(ActionRecord).where(ActionRecord.email_pk == email_pk).order_by(ActionRecord.id)
        return list(self.session.execute(stmt).scalars().all())

    def list_pending(self, *, limit: int = 100) -> list[tuple[ActionRecord, EmailRecord]]:
        stmt = (
            select(ActionRecord, EmailRecord)
            .join(EmailRecord, ActionRecord.email_pk == EmailRecord.id)
            .where(ActionRecord.status == "PENDING")
            .order_by(EmailRecord.priority_score.desc(), ActionRecord.id)
            .limit(max(1, min(limit, 500)))
        )
        return [tuple(row) for row in self.session.execute(stmt).all()]

    def set_status(self, action: ActionRecord, status: str) -> ActionRecord:
        action.status = status
        action.completed_at = (
            datetime.now(timezone.utc) if status == "COMPLETED" else None
        )
        self.session.flush()
        return action
