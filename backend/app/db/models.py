"""ORM models — persistent operational state.

Consumes the Final Decision Object (``app/models/decision.py``). The raw Gmail
payload is **not** stored; only the normalised identity + the analysis result +
user-interaction state.

Tables
------
emails            one row per Gmail message (idempotency key: ``email_id``)
 ├─ actions       0..N — one per detected action; carries the user's status
 ├─ deadlines     0..N — extracted deadline + its (separate) monitoring state
 ├─ processing_runs  1..N — one per pipeline pass (history; never overwritten)
 ├─ reminders     0..N — user-scheduled reminders (Phase 10; NOT snooze)
 └─ notifications 0..N — intended alert events the Flutter layer consumes
                         (no sender here; Phase 10 populates escalation events)

gmail_sync_state  one row per connected account — the incremental-sync baseline
                  + progress (Phase 12); resumes from ``last_history_id``.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.db.base import Base, TZDateTime, utcnow

# --- status vocabularies -------------------------------------------------
ACTION_STATUSES = ("PENDING", "COMPLETED", "DISMISSED")
NOTIFICATION_STATUSES = ("PENDING", "SENT", "FAILED", "SKIPPED")
PROCESSING_STATUSES = ("ok", "partial", "error")

# Phase 10 — reminder escalation
REMINDER_STATUSES = ("PENDING", "TRIGGERED", "CANCELLED", "SKIPPED")
REMINDER_TYPES = ("USER_SCHEDULED",)
# escalation levels used on notifications.reminder_level / .severity
ESCALATION_LEVELS = ("NORMAL", "REMINDER", "URGENT", "ALARM")
# notification_type values
NOTIFICATION_TYPES = (
    "new_priority_email",   # Phase 9 — initial "important email" alert
    "deadline_escalation",  # Phase 10 — a rung on the escalation ladder
    "deadline_passed",      # Phase 10 — one-time "deadline has passed" notice
    "ambiguous_deadline",   # Phase 10 — one-time "deadline unclear" notice
    "user_reminder",        # Phase 10 — a user-scheduled reminder fired
)


class EmailRecord(Base):
    __tablename__ = "emails"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # --- identity (idempotency key = email_id) ---
    email_id: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    thread_id: Mapped[str | None] = mapped_column(String(128))
    source: Mapped[str] = mapped_column(String(32), default="gmail")

    # --- email metadata ---
    sender_name: Mapped[str | None] = mapped_column(String(320))
    sender_email: Mapped[str] = mapped_column(String(320), default="")
    subject: Mapped[str] = mapped_column(Text, default="")
    # short preview line (Gmail's own snippet, else a body head) — NOT the full
    # body, which is deliberately never persisted. Frontend inbox preview.
    snippet: Mapped[str | None] = mapped_column(Text)
    received_at: Mapped[datetime | None] = mapped_column(TZDateTime)

    # --- system analysis (overwritten on every reprocess) ---
    final_category: Mapped[str] = mapped_column(String(48), index=True, default="OTHER")
    category_confidence: Mapped[float | None] = mapped_column(Float)
    priority_level: Mapped[str] = mapped_column(String(16), index=True, default="LOW")
    priority_score: Mapped[int] = mapped_column(Integer, default=0)
    proximity_bucket: Mapped[str] = mapped_column(String(16), default="NONE")
    deadline_is_past: Mapped[bool] = mapped_column(Boolean, default=False)

    # --- state: Gmail-derived ---
    is_unread: Mapped[bool] = mapped_column(Boolean, default=True)

    # --- state: user-generated (PRESERVED across reprocessing) ---
    is_viewed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    viewed_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    is_completed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    snoozed_until: Mapped[datetime | None] = mapped_column(TZDateTime)

    # --- system flags ---
    action_required: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    needs_human_review: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    # --- routing (system) ---
    folder_label: Mapped[str] = mapped_column(String(64), default="AMAR/Other")
    should_notify: Mapped[bool] = mapped_column(Boolean, default=False)
    should_monitor: Mapped[bool] = mapped_column(Boolean, default=False)

    # --- timestamps ---
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, onupdate=utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(TZDateTime)

    actions: Mapped[list["ActionRecord"]] = relationship(
        back_populates="email", cascade="all, delete-orphan", order_by="ActionRecord.id"
    )
    deadlines: Mapped[list["DeadlineRecord"]] = relationship(
        back_populates="email", cascade="all, delete-orphan", order_by="DeadlineRecord.id"
    )
    processing_runs: Mapped[list["ProcessingRun"]] = relationship(
        back_populates="email", cascade="all, delete-orphan",
        order_by="ProcessingRun.id.desc()",
    )
    reminders: Mapped[list["ReminderRecord"]] = relationship(
        back_populates="email", cascade="all, delete-orphan",
        order_by="ReminderRecord.reminder_at",
    )
    notifications: Mapped[list["NotificationRecord"]] = relationship(
        back_populates="email", cascade="all, delete-orphan", order_by="NotificationRecord.id"
    )

    # --- read-only convenience projections (frontend inbox rows) ---
    # Cheap: the relationships are eager-loaded wherever these are serialised.
    @property
    def primary_action_type(self) -> str | None:
        """The action_type the user should act on first (blocking, else first)."""
        if not self.actions:
            return None
        blocking = [a for a in self.actions if a.blocking]
        return (blocking[0] if blocking else self.actions[0]).action_type

    @property
    def next_deadline_at(self) -> datetime | None:
        """Earliest concrete deadline datetime across this email's deadlines."""
        dts = [d.deadline_datetime for d in self.deadlines if d.deadline_datetime is not None]
        return min(dts) if dts else None


class ActionRecord(Base):
    __tablename__ = "actions"
    __table_args__ = (UniqueConstraint("email_pk", "action_ref"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email_pk: Mapped[int] = mapped_column(
        ForeignKey("emails.id", ondelete="CASCADE"), index=True, nullable=False
    )
    action_ref: Mapped[str] = mapped_column(String(16), default="act_001")  # agent's action_id

    action_type: Mapped[str] = mapped_column(String(32), default="OTHER")
    description: Mapped[str | None] = mapped_column(Text)
    blocking: Mapped[bool] = mapped_column(Boolean, default=False)
    target_link: Mapped[str | None] = mapped_column(Text)
    raw_deadline_hint: Mapped[str | None] = mapped_column(String(120))
    confidence: Mapped[float] = mapped_column(Float, default=0.0)

    # user-generated
    status: Mapped[str] = mapped_column(String(16), default="PENDING", index=True)

    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, onupdate=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(TZDateTime)

    email: Mapped[EmailRecord] = relationship(back_populates="actions")


class DeadlineRecord(Base):
    __tablename__ = "deadlines"
    __table_args__ = (UniqueConstraint("email_pk", "deadline_ref"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email_pk: Mapped[int] = mapped_column(
        ForeignKey("emails.id", ondelete="CASCADE"), index=True, nullable=False
    )
    deadline_ref: Mapped[str] = mapped_column(String(16), default="dl_001")  # agent's deadline_id

    # --- extraction (system) ---
    deadline_datetime: Mapped[datetime | None] = mapped_column(TZDateTime, index=True)
    source_text: Mapped[str | None] = mapped_column(Text)
    timezone: Mapped[str] = mapped_column(String(48), default="UTC")
    date_only: Mapped[bool] = mapped_column(Boolean, default=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    is_ambiguous: Mapped[bool] = mapped_column(Boolean, default=False)
    ambiguity_reason: Mapped[str | None] = mapped_column(Text)
    is_past: Mapped[bool] = mapped_column(Boolean, default=False)
    action_context: Mapped[str | None] = mapped_column(String(32))
    related_action_ref: Mapped[str | None] = mapped_column(String(16))

    # --- monitoring state (PRESERVED across reprocessing; Phase 10 drives it) ---
    is_monitoring: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    monitoring_started_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    monitoring_stopped_at: Mapped[datetime | None] = mapped_column(TZDateTime)

    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, onupdate=utcnow)

    email: Mapped[EmailRecord] = relationship(back_populates="deadlines")


class ProcessingRun(Base):
    __tablename__ = "processing_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email_pk: Mapped[int] = mapped_column(
        ForeignKey("emails.id", ondelete="CASCADE"), index=True, nullable=False
    )

    run_id: Mapped[str] = mapped_column(String(64), index=True)
    processed_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    status: Mapped[str] = mapped_column(String(16), default="ok")
    pipeline_version: Mapped[str] = mapped_column(String(32), default="")

    final_category: Mapped[str] = mapped_column(String(48), default="OTHER")
    priority_level: Mapped[str] = mapped_column(String(16), default="LOW")
    priority_score: Mapped[int] = mapped_column(Integer, default=0)
    needs_human_review: Mapped[bool] = mapped_column(Boolean, default=False)
    # the orchestrator envelope's one-line human-readable reasoning for this pass
    summary: Mapped[str | None] = mapped_column(Text)

    # JSON only where it adds value (structured, rarely queried by field)
    agent_trace: Mapped[list] = mapped_column(JSON, default=list)
    conflicts_resolved: Mapped[list] = mapped_column(JSON, default=list)
    review_reasons: Mapped[list] = mapped_column(JSON, default=list)
    errors: Mapped[list] = mapped_column(JSON, default=list)

    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)

    email: Mapped[EmailRecord] = relationship(back_populates="processing_runs")


class ReminderRecord(Base):
    """A **user-scheduled** reminder: "remind me about this at <time>".

    Distinct from snooze (``EmailRecord.snoozed_until`` = *suppress* until a time)
    and from system escalation (``notifications`` rows). Multiple per email are
    allowed; an optional ``action_ref`` ties one to a specific action.
    """

    __tablename__ = "reminders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email_pk: Mapped[int] = mapped_column(
        ForeignKey("emails.id", ondelete="CASCADE"), index=True, nullable=False
    )
    action_ref: Mapped[str | None] = mapped_column(String(16))  # optional link to an action

    reminder_at: Mapped[datetime] = mapped_column(TZDateTime, index=True, nullable=False)
    reminder_type: Mapped[str] = mapped_column(String(24), default="USER_SCHEDULED")
    status: Mapped[str] = mapped_column(String(16), default="PENDING", index=True)
    timezone: Mapped[str] = mapped_column(String(48), default="UTC")
    note: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    triggered_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    cancelled_at: Mapped[datetime | None] = mapped_column(TZDateTime)

    email: Mapped[EmailRecord] = relationship(back_populates="reminders")


class NotificationRecord(Base):
    """An *intended* alert event. Phase 10 decides WHAT/how-urgent; the future
    Flutter layer decides HOW the user experiences it. Nothing is sent here."""

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email_pk: Mapped[int] = mapped_column(
        ForeignKey("emails.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # optional links — an escalation event points at its deadline; a fired
    # user reminder points back at the ReminderRecord.
    deadline_pk: Mapped[int | None] = mapped_column(
        ForeignKey("deadlines.id", ondelete="CASCADE"), index=True
    )
    reminder_pk: Mapped[int | None] = mapped_column(ForeignKey("reminders.id", ondelete="SET NULL"))

    notification_type: Mapped[str] = mapped_column(String(48), default="new_priority_email")
    # escalation rung: NORMAL | REMINDER | URGENT | ALARM
    reminder_level: Mapped[str | None] = mapped_column(String(16))
    severity: Mapped[str] = mapped_column(String(16), default="NORMAL")
    requires_alarm: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16), default="PENDING", index=True)
    detail: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(TZDateTime)

    email: Mapped[EmailRecord] = relationship(back_populates="notifications")

    # API-facing aliases for the FK columns (frontend links a notification to
    # its deadline / reminder without exposing the raw *_pk name).
    @property
    def deadline_id(self) -> int | None:
        return self.deadline_pk

    @property
    def reminder_id(self) -> int | None:
        return self.reminder_pk


# Phase 12 — incremental Gmail sync
SYNC_DEFAULT_USER = "default"  # single-user prototype; one row keyed by user_id


class GmailSyncState(Base):
    """Persistent Gmail synchronisation baseline + progress.

    One row per connected account (``user_id``). Survives restart / crash /
    scheduler reload — the incremental sync resumes from ``last_history_id``,
    never from process start time.
    """

    __tablename__ = "gmail_sync_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, default=SYNC_DEFAULT_USER, nullable=False
    )
    account_email: Mapped[str | None] = mapped_column(String(320))

    # when AGENT AMAR started watching this mailbox (the historical unread inbox
    # from before this instant is deliberately NOT ingested)
    monitoring_started_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    # last successful incremental sync
    last_sync_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    # Gmail mailbox historyId processed up to (the resume point)
    last_history_id: Mapped[str | None] = mapped_column(String(32))

    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, onupdate=utcnow)
