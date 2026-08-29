"""API response / request models for the persistence layer.

SQLAlchemy models are never returned directly (STEP 15). These are read-only
projections built with ``from_attributes=True``.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ActionStateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    action_ref: str
    action_type: str
    description: str | None = None
    blocking: bool = False
    target_link: str | None = None
    confidence: float = 0.0
    status: str = "PENDING"
    created_at: datetime | None = None
    completed_at: datetime | None = None


class DeadlineStateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    deadline_ref: str
    deadline_datetime: datetime | None = None
    source_text: str | None = None
    timezone: str = "UTC"
    date_only: bool = False
    confidence: float = 0.0
    is_ambiguous: bool = False
    ambiguity_reason: str | None = None
    is_past: bool = False
    action_context: str | None = None
    related_action_ref: str | None = None
    is_monitoring: bool = False
    monitoring_started_at: datetime | None = None
    monitoring_stopped_at: datetime | None = None


class ProcessingRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    run_id: str
    processed_at: datetime
    status: str
    pipeline_version: str = ""
    final_category: str
    priority_level: str
    priority_score: int
    needs_human_review: bool
    summary: str | None = None
    review_reasons: list = Field(default_factory=list)
    conflicts_resolved: list = Field(default_factory=list)
    agent_trace: list = Field(default_factory=list)
    errors: list = Field(default_factory=list)


class NotificationStateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    notification_type: str
    severity: str = "NORMAL"
    reminder_level: str | None = None
    requires_alarm: bool = False
    status: str
    detail: str | None = None
    deadline_id: int | None = None
    reminder_id: int | None = None
    created_at: datetime
    sent_at: datetime | None = None


class EmailStateOut(BaseModel):
    """List view — flat."""

    model_config = ConfigDict(from_attributes=True)

    email_id: str
    thread_id: str | None = None
    source: str = "gmail"
    sender_name: str | None = None
    sender_email: str
    subject: str
    snippet: str | None = None
    received_at: datetime | None = None

    final_category: str
    category_confidence: float | None = None
    priority_level: str
    priority_score: int
    proximity_bucket: str = "NONE"
    deadline_is_past: bool = False

    # read-only convenience projections for inbox rows (see EmailRecord)
    primary_action_type: str | None = None
    next_deadline_at: datetime | None = None

    is_unread: bool = True
    is_viewed: bool = False
    viewed_at: datetime | None = None
    action_required: bool = False
    is_completed: bool = False
    completed_at: datetime | None = None
    snoozed_until: datetime | None = None
    needs_human_review: bool = False

    folder_label: str
    should_notify: bool = False
    should_monitor: bool = False

    created_at: datetime | None = None
    updated_at: datetime | None = None
    processed_at: datetime | None = None


class EmailStateDetailOut(EmailStateOut):
    """Detail view — with children."""

    reasoning_summary: str | None = None  # = latest_processing.summary, hoisted for convenience
    actions: list[ActionStateOut] = Field(default_factory=list)
    deadlines: list[DeadlineStateOut] = Field(default_factory=list)
    notifications: list[NotificationStateOut] = Field(default_factory=list)
    latest_processing: ProcessingRunOut | None = None
    processing_run_count: int = 0


class PendingActionOut(ActionStateOut):
    email_id: str
    subject: str
    priority_level: str


class UpcomingDeadlineOut(DeadlineStateOut):
    email_id: str
    subject: str
    priority_level: str


class SnoozeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snoozed_until: datetime


class PersistedRef(BaseModel):
    """Compact persistence result attached to the /process response."""

    model_config = ConfigDict(from_attributes=True)

    email_id: str
    created: bool
    is_viewed: bool
    is_completed: bool
    snoozed_until: datetime | None = None
    processing_run_count: int
    notification_created: bool = False
