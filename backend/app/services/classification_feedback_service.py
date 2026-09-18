"""User classification feedback — manual primary-category correction (Phase 18).

The automated pipeline classifies each email into ONE canonical primary bucket
(``REPLY_REQUIRED`` / ``ACTION_REQUIRED`` / ``IMPORTANT`` / ``LOW_PRIORITY``,
derived by :func:`app.models.decision.derive_primary_category`). Occasionally the
user disagrees. :meth:`ClassificationFeedbackService.submit`:

  * validates ownership + the corrected category,
  * records the correction (append-only history in ``classification_feedback``),
  * updates the email's canonical ``primary_category`` to the correction and marks
    ``primary_category_source = "user"`` so later reprocessing does not move it,
  * keeps the original automated prediction on ``EmailRecord.auto_primary_category``
    (and snapshots the classifier provenance onto the feedback row) for a future
    controlled retraining loop.

Nothing here retrains a model. The ML model is untouched.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.db.models import ClassificationFeedback, EmailRecord
from app.models.decision import PrimaryCategory
from app.repositories import ClassificationFeedbackRepository, EmailRepository
from app.services.audit_service import audit_record

_VALID = {c.value for c in PrimaryCategory}


class EmailNotFoundError(LookupError):
    """The email id is unknown or not owned by the requesting user."""


class InvalidClassificationCategoryError(ValueError):
    """The supplied category is not one of the four primary buckets."""


def normalize_category(value: object) -> str:
    raw = getattr(value, "value", value)
    text = str(raw or "").strip().upper()
    if text not in _VALID:
        raise InvalidClassificationCategoryError(
            f"'{raw}' is not a valid primary category. "
            f"Expected one of: {sorted(_VALID)}"
        )
    return text


class ClassificationFeedbackService:
    def __init__(self, session: Session, *, user_pk: int) -> None:
        self.session = session
        self.user_pk = user_pk
        self.emails = EmailRepository(session)
        self.feedback = ClassificationFeedbackRepository(session)

    def submit(self, email_id: str, category: object) -> EmailRecord:
        """Record a correction and update the email's canonical primary category.

        Idempotent: re-submitting the value the email already has (a double tap,
        a retry) records nothing new and just returns the current record.
        """
        corrected = normalize_category(category)  # -> InvalidClassificationCategoryError

        record = self.emails.get_by_email_id(
            email_id, user_pk=self.user_pk, with_children=True
        )
        if record is None or record.is_spam:
            raise EmailNotFoundError(email_id)

        already_user_set = record.primary_category_source == "user"
        # Duplicate / repeated submission: same value the email already shows and
        # already user-owned -> no new history row, no-op update.
        if corrected == record.primary_category and already_user_set:
            return record

        auto_now = record.auto_primary_category or record.primary_category
        source, confidence, llm_used = self._provenance(record)

        self.feedback.add(
            ClassificationFeedback(
                user_pk=self.user_pk,
                email_pk=record.id,
                email_id=record.email_id,
                original_primary_category=auto_now,
                corrected_primary_category=corrected,
                original_final_category=record.final_category,
                classifier_source=source,
                ml_confidence=confidence,
                llm_used=llm_used,
                email_classified_at=record.processed_at,
            )
        )

        # atomic (single transaction): the correction becomes the current bucket
        record.primary_category = corrected
        record.primary_category_source = "user"
        self.session.commit()
        self.session.refresh(record)

        audit_record(
            "EMAIL_RECLASSIFIED",
            "email",
            resource_id=record.email_id,
            user_pk=self.user_pk,
            detail={"from": auto_now, "to": corrected},
        )
        return record

    def history(self, email_id: str) -> list[ClassificationFeedback]:
        record = self.emails.get_by_email_id(email_id, user_pk=self.user_pk)
        if record is None or record.is_spam:
            raise EmailNotFoundError(email_id)
        rows = self.feedback.list_for_user(self.user_pk, limit=1000)
        return [r for r in rows if r.email_pk == record.id]

    @staticmethod
    def _provenance(record: EmailRecord) -> tuple[str | None, float | None, bool]:
        """Read the classifier source / ML confidence / LLM-used flag from the
        email's most recent processing run's agent trace."""
        runs = list(record.processing_runs)
        if not runs:
            return None, None, False
        latest = runs[0]  # relationship order_by = ProcessingRun.id.desc()
        trace = latest.agent_trace or []
        triage = next(
            (t for t in trace if isinstance(t, dict) and t.get("agent") == "Triage Agent"),
            None,
        )
        if triage is None:
            return None, None, False
        method = triage.get("method")
        conf = triage.get("confidence")
        llm_used = bool(method and "llm" in str(method))
        ml_conf = float(conf) if (method == "ml" and conf is not None) else None
        return (str(method) if method else None), ml_conf, llm_used


def _now() -> datetime:
    return datetime.now(timezone.utc)
