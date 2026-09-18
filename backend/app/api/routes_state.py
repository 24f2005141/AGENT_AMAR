"""Persistent state endpoints (Phase 9; per-user since Phase 15).

Read the DB and let the user act on their own emails' state. No Gmail calls here.
Every route requires a valid application session and is scoped to that user.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import (
    get_classification_feedback_service,
    get_current_user,
    get_db,
    get_persistence_service,
)
from app.db.models import EmailRecord, User
from app.models.feedback import ClassificationFeedbackOut, ClassificationFeedbackRequest
from app.models.persistence import (
    ActionStateOut,
    ClearAcknowledgedResult,
    DeadlineStateOut,
    EmailStateDetailOut,
    EmailStateOut,
    PendingActionOut,
    ProcessingRunOut,
    SnoozeRequest,
    UpcomingDeadlineOut,
)
from app.repositories import (
    ActionRepository,
    DeadlineRepository,
    EmailRepository,
    ProcessingRepository,
)
from app.services.classification_feedback_service import (
    ClassificationFeedbackService,
    EmailNotFoundError,
    InvalidClassificationCategoryError,
)
from app.services.persistence_service import PersistenceService

router = APIRouter(prefix="/api/v1", tags=["state"])


def _email_detail(record: EmailRecord, db: Session) -> EmailStateDetailOut:
    out = EmailStateDetailOut.model_validate(record)  # nested children via from_attributes
    latest = ProcessingRepository(db).latest_for(record.id)
    out.latest_processing = ProcessingRunOut.model_validate(latest) if latest else None
    out.reasoning_summary = latest.summary if latest else None
    out.processing_run_count = len(record.processing_runs)
    return out


def _load_owned(db: Session, email_id: str, user: User, *, with_children: bool = False) -> EmailRecord:
    record = EmailRepository(db).get_by_email_id(
        email_id, user_pk=user.id, with_children=with_children
    )
    # Gmail spam is never shown or acted on anywhere in the app — treat it
    # exactly like "not found" rather than exposing that a spam row exists.
    if record is None or record.is_spam:
        raise HTTPException(status_code=404, detail="email not found")
    return record


# --- emails -------------------------------------------------------------

@router.get("/emails", response_model=list[EmailStateOut])
def list_emails(
    priority: str | None = Query(default=None, description="LOW/MEDIUM/HIGH/URGENT/CRITICAL"),
    category: str | None = None,
    primary_category: str | None = Query(
        default=None,
        description="Canonical inbox bucket (mutually exclusive): "
        "REPLY_REQUIRED / ACTION_REQUIRED / IMPORTANT / LOW_PRIORITY",
    ),
    action_required: bool | None = None,
    needs_human_review: bool | None = None,
    viewed: bool | None = None,
    completed: bool | None = None,
    active: bool | None = Query(
        default=None,
        description="Attention-dashboard filter. true = only emails that still "
        "need the user's attention (unresolved reply/action, or an "
        "unacknowledged important/low-priority email; excludes completed and "
        "snoozed). false = the complement (resolved / acknowledged / history). "
        "Omit for the full list. Nothing is ever deleted — this only filters.",
    ),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[EmailRecord]:
    return EmailRepository(db).list(
        user_pk=user.id,
        category=category,
        primary_category=primary_category,
        priority_level=priority,
        action_required=action_required,
        needs_human_review=needs_human_review,
        viewed=viewed,
        completed=completed,
        active=active,
        limit=limit,
        offset=offset,
    )


@router.get("/emails/human-review", response_model=list[EmailStateOut])
def list_human_review(
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[EmailRecord]:
    return EmailRepository(db).list_needing_human_review(user_pk=user.id, limit=limit)


@router.post("/emails/clear-acknowledged", response_model=ClearAcknowledgedResult)
def clear_acknowledged(
    svc: PersistenceService = Depends(get_persistence_service),
) -> ClearAcknowledgedResult:
    """"Clear Resolved" — tidy the active dashboard.

    Marks every **active, non-actionable** email (`IMPORTANT` / `LOW_PRIORITY`,
    not completed, not snoozed) as acknowledged (`is_viewed`), so it leaves
    ``GET /api/v1/emails?active=true``. **Never** touches `REPLY_REQUIRED` /
    `ACTION_REQUIRED` — an unresolved task is never silently completed. Nothing
    is deleted, no Gmail call is made, your Gmail messages are untouched.
    Idempotent."""
    return ClearAcknowledgedResult(acknowledged=svc.clear_acknowledged())


@router.get("/emails/{email_id}", response_model=EmailStateDetailOut)
def get_email(
    email_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> EmailStateDetailOut:
    record = _load_owned(db, email_id, user, with_children=True)
    return _email_detail(record, db)


@router.get("/emails/{email_id}/processing", response_model=list[ProcessingRunOut])
def get_email_processing(
    email_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list:
    record = _load_owned(db, email_id, user)
    return ProcessingRepository(db).list_by_email(record.id)


# --- user-state mutations --------------------------------------------

@router.patch("/emails/{email_id}/viewed", response_model=EmailStateDetailOut)
def mark_viewed(
    email_id: str,
    svc: PersistenceService = Depends(get_persistence_service),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> EmailStateDetailOut:
    record = svc.mark_viewed(email_id)
    if record is None:
        raise HTTPException(status_code=404, detail="email not found")
    return get_email(email_id, db, user)


@router.patch("/emails/{email_id}/snooze", response_model=EmailStateDetailOut)
def snooze_email(
    email_id: str,
    body: SnoozeRequest,
    svc: PersistenceService = Depends(get_persistence_service),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> EmailStateDetailOut:
    record = svc.snooze(email_id, body.snoozed_until)
    if record is None:
        raise HTTPException(status_code=404, detail="email not found")
    return get_email(email_id, db, user)


@router.delete("/emails/{email_id}/snooze", response_model=EmailStateDetailOut)
def clear_snooze(
    email_id: str,
    svc: PersistenceService = Depends(get_persistence_service),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> EmailStateDetailOut:
    """Remove an active snooze (idempotent — 200 even if not snoozed)."""
    record = svc.clear_snooze(email_id)
    if record is None:
        raise HTTPException(status_code=404, detail="email not found")
    return get_email(email_id, db, user)


@router.patch("/emails/{email_id}/complete", response_model=EmailStateDetailOut)
def complete_email(
    email_id: str,
    svc: PersistenceService = Depends(get_persistence_service),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> EmailStateDetailOut:
    """Explicitly resolve the whole email — the "mark done" / tick action.

    Completes **every** pending action, sets ``is_completed`` and
    ``completion_source = "user"`` (a later Gmail sync / reprocess can never
    revert it), and drops the email from the active homepage
    (``GET /api/v1/emails?active=true``). Idempotent. Nothing is deleted."""
    record = svc.mark_complete(email_id)
    if record is None:
        raise HTTPException(status_code=404, detail="email not found")
    return get_email(email_id, db, user)


@router.patch("/emails/{email_id}/reopen", response_model=EmailStateDetailOut)
def reopen_email(
    email_id: str,
    svc: PersistenceService = Depends(get_persistence_service),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> EmailStateDetailOut:
    """Undo a completion (an "undo" affordance). Hands the email back to
    auto-derivation and returns it to the active homepage if still unresolved."""
    record = svc.reopen(email_id)
    if record is None:
        raise HTTPException(status_code=404, detail="email not found")
    return get_email(email_id, db, user)


@router.patch("/emails/{email_id}/actions/{action_ref}/complete", response_model=EmailStateDetailOut)
def complete_action(
    email_id: str,
    action_ref: str,
    svc: PersistenceService = Depends(get_persistence_service),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> EmailStateDetailOut:
    result = svc.set_action_status(email_id, action_ref, "COMPLETED")
    if result is None:
        raise HTTPException(status_code=404, detail="email or action not found")
    return get_email(email_id, db, user)


@router.patch("/emails/{email_id}/actions/{action_ref}/dismiss", response_model=EmailStateDetailOut)
def dismiss_action(
    email_id: str,
    action_ref: str,
    svc: PersistenceService = Depends(get_persistence_service),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> EmailStateDetailOut:
    result = svc.set_action_status(email_id, action_ref, "DISMISSED")
    if result is None:
        raise HTTPException(status_code=404, detail="email or action not found")
    return get_email(email_id, db, user)


# --- user classification feedback (Phase 18) ------------------------

@router.post(
    "/emails/{email_id}/classification-feedback",
    response_model=EmailStateDetailOut,
)
def submit_classification_feedback(
    email_id: str,
    body: ClassificationFeedbackRequest,
    svc: ClassificationFeedbackService = Depends(get_classification_feedback_service),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> EmailStateDetailOut:
    """Manually correct an email's canonical primary category.

    The correction becomes the current ``primary_category`` (mutually exclusive —
    the email leaves its old section immediately); the automated prediction is
    kept in ``auto_primary_category``. ``category`` must be one of
    ``REPLY_REQUIRED`` / ``ACTION_REQUIRED`` / ``IMPORTANT`` / ``LOW_PRIORITY``
    (any other value → 422). 404 for an unknown / other-user email. Re-submitting
    the value the email already has is a no-op (200, no new history row).
    """
    try:
        svc.submit(email_id, body.category)
    except EmailNotFoundError:
        raise HTTPException(status_code=404, detail="email not found")
    except InvalidClassificationCategoryError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return get_email(email_id, db, user)


@router.get(
    "/emails/{email_id}/classification-feedback",
    response_model=list[ClassificationFeedbackOut],
)
def list_classification_feedback(
    email_id: str,
    svc: ClassificationFeedbackService = Depends(get_classification_feedback_service),
) -> list:
    """The correction history for one owned email (most recent first)."""
    try:
        return svc.history(email_id)
    except EmailNotFoundError:
        raise HTTPException(status_code=404, detail="email not found")


# --- cross-cutting lists ---------------------------------------------

@router.get("/actions/pending", response_model=list[PendingActionOut])
def list_pending_actions(
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[PendingActionOut]:
    rows = ActionRepository(db).list_pending(user_pk=user.id, limit=limit)
    return [
        PendingActionOut(
            **ActionStateOut.model_validate(action).model_dump(),
            email_id=email.email_id,
            subject=email.subject,
            priority_level=email.priority_level,
        )
        for action, email in rows
    ]


@router.get("/deadlines/upcoming", response_model=list[UpcomingDeadlineOut])
def list_upcoming_deadlines(
    within_hours: int | None = Query(default=None, ge=1, le=8760),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[UpcomingDeadlineOut]:
    rows = DeadlineRepository(db).list_upcoming(
        user_pk=user.id, within_hours=within_hours, limit=limit
    )
    return [
        UpcomingDeadlineOut(
            **DeadlineStateOut.model_validate(dl).model_dump(),
            email_id=email.email_id,
            subject=email.subject,
            priority_level=email.priority_level,
        )
        for dl, email in rows
    ]
