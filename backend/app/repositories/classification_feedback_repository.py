"""Data access for user classification-feedback records (Phase 18).

All reads are scoped to one owning user. No business logic here.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ClassificationFeedback, EmailRecord


class ClassificationFeedbackRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, row: ClassificationFeedback) -> ClassificationFeedback:
        self.session.add(row)
        self.session.flush()
        return row

    def latest_for_email(
        self, email_pk: int, *, user_pk: int | None = None
    ) -> ClassificationFeedback | None:
        stmt = (
            select(ClassificationFeedback)
            .where(ClassificationFeedback.email_pk == email_pk)
            .order_by(ClassificationFeedback.id.desc())
        )
        if user_pk is not None:
            stmt = stmt.where(ClassificationFeedback.user_pk == user_pk)
        return self.session.execute(stmt.limit(1)).scalar_one_or_none()

    def list_for_user(
        self, user_pk: int, *, limit: int = 200
    ) -> list[ClassificationFeedback]:
        stmt = (
            select(ClassificationFeedback)
            .where(ClassificationFeedback.user_pk == user_pk)
            .order_by(ClassificationFeedback.id.desc())
            .limit(max(1, min(limit, 1000)))
        )
        return list(self.session.execute(stmt).scalars().all())

    def count_for_email(self, email_pk: int) -> int:
        return len(
            self.session.execute(
                select(ClassificationFeedback.id).where(
                    ClassificationFeedback.email_pk == email_pk
                )
            ).all()
        )

    def latest_valid_per_email(self) -> list[ClassificationFeedback]:
        """One row per email — the most recent feedback — but only for emails
        that still exist. Used by the training-data export."""
        rows = self.session.execute(
            select(ClassificationFeedback)
            .join(EmailRecord, EmailRecord.id == ClassificationFeedback.email_pk)
            .order_by(ClassificationFeedback.email_pk, ClassificationFeedback.id.desc())
        ).scalars().all()
        seen: set[int] = set()
        latest: list[ClassificationFeedback] = []
        for row in rows:
            if row.email_pk in seen:
                continue
            seen.add(row.email_pk)
            latest.append(row)
        return latest
