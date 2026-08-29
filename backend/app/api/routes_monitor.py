"""Deadline-monitoring, reminder and notification endpoints (Phase 10).

    POST   /api/v1/monitor/deadlines/check          run the Deadline Monitor (manual)
    GET    /api/v1/monitor/status                   background scheduler status
    POST   /api/v1/emails/{email_id}/reminders      create a user-scheduled reminder
    GET    /api/v1/emails/{email_id}/reminders      list this email's reminders
    DELETE /api/v1/emails/{email_id}/reminders/{id} cancel a pending reminder
    GET    /api/v1/notifications                    query generated notification events
    GET    /api/v1/notifications/{id}               one notification event

No delivery happens here — endpoints only read/produce persistent state.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_deadline_monitor_service, get_reminder_service
from app.models.monitoring import (
    MonitorCheckRequest,
    MonitorCheckResult,
    MonitorDecisionOut,
    NotificationOut,
    ReminderCreate,
    ReminderOut,
)
from app.repositories import NotificationRepository, ReminderRepository
from app.services.deadline_monitor_service import DeadlineMonitorService
from app.services.reminder_service import ReminderService, ReminderValidationError
from app.services.scheduler import get_scheduler

router = APIRouter(prefix="/api/v1", tags=["monitoring"])


# --- deadline monitor ------------------------------------------------

@router.post("/monitor/deadlines/check", response_model=MonitorCheckResult)
def run_deadline_check(
    body: MonitorCheckRequest | None = None,
    monitor: DeadlineMonitorService = Depends(get_deadline_monitor_service),
) -> MonitorCheckResult:
    now = body.now if body is not None else None
    result = monitor.run_deadline_check(now)
    return MonitorCheckResult(
        checked_at=result.checked_at,
        deadlines_evaluated=result.deadlines_evaluated,
        reminders_evaluated=result.reminders_evaluated,
        notifications_created=result.notifications_created,
        results=[MonitorDecisionOut.model_validate(d) for d in result.results],
    )


@router.get("/monitor/status")
def monitor_status() -> dict:
    """Background scheduler state (Phase 11B.1). Read-only, no auth.

    ``scheduler`` is ``"running"`` / ``"stopped"``; ``last_*_check`` are ISO
    8601 UTC or ``null`` before the first cycle.
    """
    return get_scheduler().status()


# --- user-scheduled reminders --------------------------------------

@router.post(
    "/emails/{email_id}/reminders",
    response_model=ReminderOut,
    status_code=201,
)
def create_reminder(
    email_id: str,
    body: ReminderCreate,
    svc: ReminderService = Depends(get_reminder_service),
) -> ReminderOut:
    try:
        reminder = svc.create(
            email_id, body.reminder_at, action_ref=body.action_ref, note=body.note
        )
    except ReminderValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if reminder is None:
        raise HTTPException(status_code=404, detail="email not found")
    return _reminder_out(reminder, email_id)


@router.get("/reminders", response_model=list[ReminderOut])
def list_all_reminders(
    status: str | None = Query(default=None, description="PENDING/TRIGGERED/CANCELLED/SKIPPED"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> list[ReminderOut]:
    """Every reminder across all emails (frontend Reminders screen)."""
    rows = ReminderRepository(db).list_all(status=status, limit=limit, offset=offset)
    return [_reminder_out(r, eid) for r, eid in rows]


@router.get("/emails/{email_id}/reminders", response_model=list[ReminderOut])
def list_reminders(
    email_id: str,
    svc: ReminderService = Depends(get_reminder_service),
) -> list[ReminderOut]:
    reminders = svc.list_for_email(email_id)
    if reminders is None:
        raise HTTPException(status_code=404, detail="email not found")
    return [_reminder_out(r, email_id) for r in reminders]


@router.delete("/emails/{email_id}/reminders/{reminder_id}", response_model=ReminderOut)
def cancel_reminder(
    email_id: str,
    reminder_id: int,
    svc: ReminderService = Depends(get_reminder_service),
) -> ReminderOut:
    reminder = svc.cancel(email_id, reminder_id)
    if reminder is None:
        raise HTTPException(status_code=404, detail="reminder not found")
    return _reminder_out(reminder, email_id)


def _reminder_out(reminder, email_id: str) -> ReminderOut:
    out = ReminderOut.model_validate(reminder)
    out.email_id = email_id
    return out


# --- notification events -----------------------------------------

@router.get("/notifications", response_model=list[NotificationOut])
def list_notifications(
    status: str | None = Query(default=None, description="PENDING/SENT/FAILED/SKIPPED"),
    severity: str | None = Query(default=None, description="NORMAL/REMINDER/URGENT/ALARM"),
    type: str | None = Query(default=None, alias="type"),
    email_id: str | None = None,
    requires_alarm: bool | None = None,
    created_after: datetime | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> list[NotificationOut]:
    rows = NotificationRepository(db).list(
        status=status,
        severity=severity,
        notification_type=type,
        email_id=email_id,
        requires_alarm=requires_alarm,
        created_after=created_after,
        limit=limit,
        offset=offset,
    )
    return [_to_out(n) for n in rows]


@router.get("/notifications/{notification_id}", response_model=NotificationOut)
def get_notification(
    notification_id: int,
    db: Session = Depends(get_db),
) -> NotificationOut:
    note = NotificationRepository(db).get(notification_id)
    if note is None:
        raise HTTPException(status_code=404, detail="notification not found")
    return _to_out(note)


def _to_out(note) -> NotificationOut:
    out = NotificationOut.model_validate(note)
    out.email_id = note.email.email_id if note.email is not None else None
    return out
