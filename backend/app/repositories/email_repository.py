"""Email record data access. All reads are scoped to one owning user (Phase 15)."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import and_, not_, or_, select
from sqlalchemy.orm import Session, selectinload

from app.db.models import EmailRecord

#: Buckets that stay on the attention feed until explicitly resolved — opening
#: the email is not enough (mirrors ``EmailRecord.is_active``).
_ACTIONABLE_BUCKETS = ("REPLY_REQUIRED", "ACTION_REQUIRED")


def active_attention_filter(now: datetime | None = None):
    """SQL predicate for "this email still needs the user's attention".

    Single source of truth for the homepage / attention-dashboard query — the
    ORM-level twin of :pyattr:`app.db.models.EmailRecord.is_active`:

      * not Gmail spam
      * not resolved / completed
      * not currently snoozed
      * REPLY_REQUIRED / ACTION_REQUIRED → until resolved
      * IMPORTANT / LOW_PRIORITY → only until opened / acknowledged (is_viewed)
    """
    now = now or datetime.now(timezone.utc)
    return and_(
        EmailRecord.is_spam.is_(False),
        EmailRecord.is_completed.is_(False),
        or_(EmailRecord.snoozed_until.is_(None), EmailRecord.snoozed_until <= now),
        or_(
            EmailRecord.primary_category.in_(_ACTIONABLE_BUCKETS),
            EmailRecord.is_viewed.is_(False),
        ),
    )


class EmailRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_by_email_id(
        self, email_id: str, *, user_pk: int | None = None, with_children: bool = False
    ) -> EmailRecord | None:
        stmt = select(EmailRecord).where(EmailRecord.email_id == email_id)
        if user_pk is not None:
            stmt = stmt.where(EmailRecord.user_pk == user_pk)
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
        user_pk: int | None = None,
        category: str | None = None,
        primary_category: str | None = None,
        priority_level: str | None = None,
        action_required: bool | None = None,
        needs_human_review: bool | None = None,
        viewed: bool | None = None,
        completed: bool | None = None,
        active: bool | None = None,
        limit: int = 100,
        offset: int = 0,
        now: datetime | None = None,
    ) -> list[EmailRecord]:
        stmt = select(EmailRecord).options(
            selectinload(EmailRecord.actions), selectinload(EmailRecord.deadlines)
        )
        # Gmail spam is never listed anywhere in the app — unconditional, not
        # tied to the `active` filter (a spam row is never "history" either).
        stmt = stmt.where(EmailRecord.is_spam.is_(False))
        if user_pk is not None:
            stmt = stmt.where(EmailRecord.user_pk == user_pk)
        if category is not None:
            stmt = stmt.where(EmailRecord.final_category == category.upper())
        if primary_category is not None:
            stmt = stmt.where(EmailRecord.primary_category == primary_category.upper())
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
        if active is not None:
            expr = active_attention_filter(now)
            stmt = stmt.where(expr if active else not_(expr))
        stmt = stmt.order_by(EmailRecord.priority_score.desc(), EmailRecord.id.desc())
        stmt = stmt.limit(max(1, min(limit, 500))).offset(max(0, offset))
        return list(self.session.execute(stmt).scalars().all())

    def list_needing_human_review(
        self, *, user_pk: int | None = None, limit: int = 100
    ) -> list[EmailRecord]:
        return self.list(user_pk=user_pk, needs_human_review=True, limit=limit)
