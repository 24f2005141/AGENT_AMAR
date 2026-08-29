"""Persistent state endpoints (Phase 9).

Read the DB and let the user act on their emails' state. No Gmail calls here.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_persistence_service
from app.db.models import EmailRecord
from app.models.persistence import (
    ActionStateOut,
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
from app.services.persistence_service import PersistenceService

router = APIRouter(prefix="/api/v1", tags=["state"])


def _email_detail(record: EmailRecord, db: Session) -> EmailStateDetailOut:
    out = EmailStateDetailOut.model_validate(record)  # nested children via from_attributes
    latest = ProcessingRepository(db).latest_for(record.id)
    out.latest_processing = ProcessingRunOut.model_validate(latest) if latest else None
    out.reasoning_summary = latest.summary if latest else None
    out.processing_run_count = len(record.processing_runs)
    return out


# --- emails -------------------------------------------------------------

@router.get("/emails", response_model=list[EmailStateOut])
def list_emails(
    priority: str | None = Query(default=None, description="LOW/MEDIUM/HIGH/URGENT/CRITICAL"),
    category: str | None = None,
    action_required: bool | None = None,
    needs_human_review: bool | None = None,
    viewed: bool | None = None,
    completed: bool | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> list[EmailRecord]:
    return EmailRepository(db).list(
        category=category,
        priority_level=priority,
        action_required=action_required,
        needs_human_review=needs_human_review,
        viewed=viewed,
        completed=completed,
        limit=limit,
        offset=offset,
    )


@router.get("/emails/human-review", response_model=list[EmailStateOut])
def list_human_review(
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[EmailRecord]:
    return EmailRepository(db).list_needing_human_review(limit=limit)


@router.get("/emails/{email_id}", response_model=EmailStateDetailOut)
def get_email(email_id: str, db: Session = Depends(get_db)) -> EmailStateDetailOut:
    record = EmailRepository(db).get_by_email_id(email_id, with_children=True)
    if record is None:
        raise HTTPException(status_code=404, detail="email not found")
    return _email_detail(record, db)


@router.get("/emails/{email_id}/processing", response_model=list[ProcessingRunOut])
def get_email_processing(email_id: str, db: Session = Depends(get_db)) -> list:
    record = EmailRepository(db).get_by_email_id(email_id)
    if record is None:
        raise HTTPException(status_code=404, detail="email not found")
    return ProcessingRepository(db).list_by_email(record.id)


# --- user-state mutations --------------------------------------------

@router.patch("/emails/{email_id}/viewed", response_model=EmailStateDetailOut)
def mark_viewed(
    email_id: str,
    svc: PersistenceService = Depends(get_persistence_service),
    db: Session = Depends(get_db),
) -> EmailStateDetailOut:
    record = svc.mark_viewed(email_id)
    if record is None:
        raise HTTPException(status_code=404, detail="email not found")
    return get_email(email_id, db)


@router.patch("/emails/{email_id}/snooze", response_model=EmailStateDetailOut)
def snooze_email(
    email_id: str,
    body: SnoozeRequest,
    svc: PersistenceService = Depends(get_persistence_service),
    db: Session = Depends(get_db),
) -> EmailStateDetailOut:
    record = svc.snooze(email_id, body.snoozed_until)
    if record is None:
        raise HTTPException(status_code=404, detail="email not found")
    return get_email(email_id, db)


@router.delete("/emails/{email_id}/snooze", response_model=EmailStateDetailOut)
def clear_snooze(
    email_id: str,
    svc: PersistenceService = Depends(get_persistence_service),
    db: Session = Depends(get_db),
) -> EmailStateDetailOut:
    """Remove an active snooze (idempotent — 200 even if not snoozed)."""
    record = svc.clear_snooze(email_id)
    if record is None:
        raise HTTPException(status_code=404, detail="email not found")
    return get_email(email_id, db)


@router.patch("/emails/{email_id}/actions/{action_ref}/complete", response_model=EmailStateDetailOut)
def complete_action(
    email_id: str,
    action_ref: str,
    svc: PersistenceService = Depends(get_persistence_service),
    db: Session = Depends(get_db),
) -> EmailStateDetailOut:
    result = svc.set_action_status(email_id, action_ref, "COMPLETED")
    if result is None:
        raise HTTPException(status_code=404, detail="email or action not found")
    return get_email(email_id, db)


@router.patch("/emails/{email_id}/actions/{action_ref}/dismiss", response_model=EmailStateDetailOut)
def dismiss_action(
    email_id: str,
    action_ref: str,
    svc: PersistenceService = Depends(get_persistence_service),
    db: Session = Depends(get_db),
) -> EmailStateDetailOut:
    result = svc.set_action_status(email_id, action_ref, "DISMISSED")
    if result is None:
        raise HTTPException(status_code=404, detail="email or action not found")
    return get_email(email_id, db)


# --- cross-cutting lists ---------------------------------------------

@router.get("/actions/pending", response_model=list[PendingActionOut])
def list_pending_actions(
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[PendingActionOut]:
    rows = ActionRepository(db).list_pending(limit=limit)
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
) -> list[UpcomingDeadlineOut]:
    rows = DeadlineRepository(db).list_upcoming(within_hours=within_hours, limit=limit)
    return [
        UpcomingDeadlineOut(
            **DeadlineStateOut.model_validate(dl).model_dump(),
            email_id=email.email_id,
            subject=email.subject,
            priority_level=email.priority_level,
        )
        for dl, email in rows
    ]
