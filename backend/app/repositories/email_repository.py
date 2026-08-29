"""Email record data access."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.models import EmailRecord


class EmailRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_by_email_id(self, email_id: str, *, with_children: bool = False) -> EmailRecord | None:
        stmt = select(EmailRecord).where(EmailRecord.email_id == email_id)
        if with_children:
            stmt = stmt.options(
                selectinload(EmailRecord.actions),
                selectinload(EmailRecord.deadlines),
                selectinload(EmailRecord.processing_runs),
                selectinload(EmailRecord.notifications),
            )
        return self.session.execute(stmt).scalar_one_or_none()

    def add(self, record: EmailRecord) -> EmailRecord:
        self.session.add(record)
        self.session.flush()
        return record

    def list(
        self,
        *,
        category: str | None = None,
        priority_level: str | None = None,
        action_required: bool | None = None,
        needs_human_review: bool | None = None,
        viewed: bool | None = None,
        completed: bool | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[EmailRecord]:
        stmt = select(EmailRecord).options(
            selectinload(EmailRecord.actions), selectinload(EmailRecord.deadlines)
        )
        if category is not None:
            stmt = stmt.where(EmailRecord.final_category == category.upper())
        if priority_level is not None:
            stmt = stmt.where(EmailRecord.priority_level == priority_level.upper())
        if action_required is not None:
            stmt = stmt.where(EmailRecord.action_required.is_(action_required))
        if needs_human_review is not None:
            stmt = stmt.where(EmailRecord.needs_human_review.is_(needs_human_review))
        if viewed is not None:
            stmt = stmt.where(EmailRecord.is_viewed.is_(viewed))
        if completed is not None:
            stmt = stmt.where(EmailRecord.is_completed.is_(completed))
        stmt = stmt.order_by(EmailRecord.priority_score.desc(), EmailRecord.id.desc())
        stmt = stmt.limit(max(1, min(limit, 500))).offset(max(0, offset))
        return list(self.session.execute(stmt).scalars().all())

    def list_needing_human_review(self, *, limit: int = 100) -> list[EmailRecord]:
        return self.list(needs_human_review=True, limit=limit)
