"""API request / response models for Phase 10 (deadline monitoring + reminders).

SQLAlchemy models are never returned directly — these are read-only projections
(``from_attributes=True``) or ``extra="forbid"`` request bodies.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


# --- user-scheduled reminders --------------------------------------------

class ReminderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reminder_at: datetime
    action_ref: str | None = Field(default=None, description="tie the reminder to one action")
    note: str | None = None


class ReminderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email_id: str | None = None
    action_ref: str | None = None
    reminder_at: datetime
    reminder_type: str
    status: str
    timezone: str
    note: str | None = None
    created_at: datetime
    triggered_at: datetime | None = None
    cancelled_at: datetime | None = None


# --- notification events -----------------------------------------------

class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email_id: str | None = None
    notification_type: str
    severity: str
    reminder_level: str | None = None
    requires_alarm: bool = False
    status: str
    detail: str | None = None
    deadline_id: int | None = None
    reminder_id: int | None = None
    created_at: datetime
    sent_at: datetime | None = None


# --- monitor run ------------------------------------------------------

class MonitorCheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    now: datetime | None = Field(
        default=None, description="override 'current time' (testing / replay)"
    )


class MonitorDecisionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    email_id: str
    deadline_ref: str | None = None
    decision: str
    reason: str
    notification_id: int | None = None
    requires_alarm: bool = False


class MonitorCheckResult(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    checked_at: datetime
    deadlines_evaluated: int
    reminders_evaluated: int
    notifications_created: int
    results: list[MonitorDecisionOut] = Field(default_factory=list)
