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

from app.db.base import Base, EncryptedString, TZDateTime, utcnow

# --- Phase 15 — multi-user identity ------------------------------------
AUTH_PROVIDERS = ("gmail",)


class User(Base):
    """An application user. One row per real person who signed in with Google.

    Keyed externally by the stable Google subject id (``google_sub``) — the
    email can change, the ``sub`` does not. All operational data (emails,
    reminders, notifications, deadlines, Gmail credentials, sync state) is
    scoped to ``User.id``.
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # stable external identity — NOT PII, safe as a lookup key / index
    google_sub: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    # Phase 14: PII — encrypted at rest. Never a lookup key.
    google_email: Mapped[str] = mapped_column(EncryptedString, default="")
    display_name: Mapped[str | None] = mapped_column(EncryptedString)

    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, onupdate=utcnow)

    emails: Mapped[list["EmailRecord"]] = relationship(back_populates="user")
    sessions: Mapped[list["AppSession"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    oauth_credential: Mapped["OAuthCredential | None"] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False
    )
    sync_state: Mapped["GmailSyncState | None"] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False
    )


class AppSession(Base):
    """An application session (bearer token) for one user.

    The raw token is NEVER stored — only ``sha256(raw_token)``. Logout /
    expiry / revocation are all just row state.
    """

    __tablename__ = "app_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_pk: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    expires_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)

    user: Mapped[User] = relationship(back_populates="sessions")


class AuthHandoff(Base):
    """A short-lived, single-use code that hands a completed Google sign-in from
    the browser back to the Flutter app (Phase 17).

    The Google OAuth callback runs in a system browser; the app needs the
    result. Instead of leaving the user on a backend page (and polling), the
    callback mints one of these and 302-redirects to the app's deep link with
    ``?code=<raw>&state=<oauth_state>``. The row carries **nothing sensitive** —
    only the id of the user who just authenticated and the OAuth ``state`` for a
    CSRF/session-fixation cross-check. ``POST /api/v1/auth/session/exchange``
    redeems it exactly once and only then mints the real application session.

    Only ``sha256(raw_code)`` is stored — a DB leak yields no usable codes.
    """

    __tablename__ = "auth_handoffs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    user_pk: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # the OAuth ``state`` nonce this handoff belongs to — the app echoes it back
    # in the deep link and the exchange verifies it matches.
    oauth_state: Mapped[str | None] = mapped_column(String(128))

    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    # set exactly once, atomically, when the code is redeemed — replay-proof.
    consumed_at: Mapped[datetime | None] = mapped_column(TZDateTime)


class DeviceRegistration(Base):
    """A user's device that can receive Firebase Cloud Messaging pushes (Phase 16).

    One row per FCM token. A token is globally unique — if it re-registers under a
    different user (a shared phone), ``user_pk`` is reassigned so the previous
    user stops receiving that device's pushes.
    """

    __tablename__ = "device_registrations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_pk: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    fcm_token: Mapped[str] = mapped_column(String(512), unique=True, index=True, nullable=False)
    platform: Mapped[str] = mapped_column(String(16), default="android")
    # user-chosen device name — mild PII, encrypted at rest (Phase 14).
    device_label: Mapped[str | None] = mapped_column(EncryptedString)
    app_version: Mapped[str | None] = mapped_column(String(32))
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, onupdate=utcnow)
    last_seen_at: Mapped[datetime | None] = mapped_column(TZDateTime)


class OAuthCredential(Base):
    """A user's Google OAuth credential blob (access + refresh token, etc.).

    Phase 14: the whole blob is stored via :class:`EncryptedString` — AES-256-GCM
    at rest. One row per user (the app only connects one Gmail account per user).
    """

    __tablename__ = "oauth_credentials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_pk: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True, nullable=False
    )
    provider: Mapped[str] = mapped_column(String(16), default="gmail")
    # JSON string of the credential blob — encrypted at rest.
    credentials_json: Mapped[str | None] = mapped_column(EncryptedString)
    # PII — the connected Gmail address, encrypted at rest.
    account_email: Mapped[str | None] = mapped_column(EncryptedString)

    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, onupdate=utcnow)

    user: Mapped[User] = relationship(back_populates="oauth_credential")


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

    # --- Phase 15: owning user (nullable — legacy single-user rows have none
    # and are simply invisible to any authenticated user; see MIGRATION_MULTIUSER.md)
    user_pk: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    # --- email metadata (Phase 14: encrypted at rest) ---
    sender_name: Mapped[str | None] = mapped_column(EncryptedString)
    sender_email: Mapped[str] = mapped_column(EncryptedString, default="")
    subject: Mapped[str] = mapped_column(EncryptedString, default="")
    # short preview line (Gmail's own snippet, else a body head) — NOT the full
    # body, which is deliberately never persisted. Frontend inbox preview.
    snippet: Mapped[str | None] = mapped_column(EncryptedString)
    received_at: Mapped[datetime | None] = mapped_column(TZDateTime)

    # --- system analysis (overwritten on every reprocess) ---
    final_category: Mapped[str] = mapped_column(String(48), index=True, default="OTHER")
    # The ONE mutually-exclusive inbox bucket for the UI:
    # REPLY_REQUIRED | ACTION_REQUIRED | IMPORTANT | LOW_PRIORITY. Derived from
    # final_category + action_required + priority_level by the orchestrator so a
    # given email is never listed under two primary sections.
    primary_category: Mapped[str] = mapped_column(
        String(24), index=True, default="LOW_PRIORITY"
    )
    # Phase 18 — user classification feedback. ``primary_category`` above is the
    # CANONICAL / current bucket every query + the UI use. When the user manually
    # corrects it, ``primary_category_source`` becomes ``"user"`` and the correction
    # is preserved across reprocessing. ``auto_primary_category`` always holds the
    # latest AUTOMATED derivation (the original model prediction) for training/audit.
    auto_primary_category: Mapped[str] = mapped_column(
        String(24), index=True, default="LOW_PRIORITY"
    )
    primary_category_source: Mapped[str] = mapped_column(String(8), default="auto")
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
    # "auto"  — is_completed is derived from the action statuses (every blocking
    #           action COMPLETED/DISMISSED) and may still be recomputed.
    # "user"  — the user explicitly resolved the email; a reprocess / Gmail sync
    #           must NEVER un-complete it (same "user state wins" rule as
    #           primary_category_source).
    completion_source: Mapped[str] = mapped_column(String(8), default="auto")
    snoozed_until: Mapped[datetime | None] = mapped_column(TZDateTime)

    # Gmail's own SPAM system label (never our own detection). Set when an
    # already-ingested message is later moved to Spam in Gmail — the row is
    # kept (never deleted) but excluded from every listing/detail view and
    # from further notifications. Cleared if the message leaves Spam again.
    is_spam: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

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
    user: Mapped["User | None"] = relationship(back_populates="emails")

    # --- read-only convenience projections (frontend inbox rows) ---
    # Cheap: the relationships are eager-loaded wherever these are serialised.
    @property
    def is_active(self) -> bool:
        """Whether this email still needs the user's attention *right now* —
        the single rule the attention-dashboard homepage is built on.

        * flagged as Gmail spam → never active
        * resolved / completed  → not active
        * currently snoozed      → not active
        * REPLY_REQUIRED / ACTION_REQUIRED → active until resolved (opening it
          does **not** clear it)
        * IMPORTANT / LOW_PRIORITY (nothing actionable) → active only until the
          user opens / acknowledges it (``is_viewed``)

        The email is never deleted — once inactive it simply drops off the
        homepage feed and lives on in history / detail views (spam is the one
        exception: it is excluded from every view, not just the active one —
        see ``EmailRepository.list``).
        """
        if self.is_spam:
            return False
        if self.is_completed:
            return False
        if self.snoozed_until is not None and self.snoozed_until > utcnow():
            return False
        if self.primary_category in ("REPLY_REQUIRED", "ACTION_REQUIRED"):
            return True
        return not self.is_viewed

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
    # Phase 14: free text derived from the email — encrypted at rest.
    description: Mapped[str | None] = mapped_column(EncryptedString)
    blocking: Mapped[bool] = mapped_column(Boolean, default=False)
    target_link: Mapped[str | None] = mapped_column(EncryptedString)
    raw_deadline_hint: Mapped[str | None] = mapped_column(EncryptedString)
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
    # Phase 14: verbatim phrase copied from the email — encrypted at rest.
    source_text: Mapped[str | None] = mapped_column(EncryptedString)
    timezone: Mapped[str] = mapped_column(String(48), default="UTC")
    date_only: Mapped[bool] = mapped_column(Boolean, default=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    is_ambiguous: Mapped[bool] = mapped_column(Boolean, default=False)
    ambiguity_reason: Mapped[str | None] = mapped_column(EncryptedString)
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


class ClassificationFeedback(Base):
    """A user's manual correction of an email's primary classification (Phase 18).

    Groundwork for a controlled retraining loop: every correction is recorded
    (append-only history), the email's canonical ``primary_category`` is updated
    to the latest valid correction, and the original automated prediction is
    kept on :class:`EmailRecord` (``auto_primary_category``) + snapshotted here.

    **No email body is duplicated** — the row references the ``EmailRecord`` and
    carries only the classification metadata needed for training / audit.
    """

    __tablename__ = "classification_feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_pk: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    email_pk: Mapped[int] = mapped_column(
        ForeignKey("emails.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # stable string id — convenient cross-reference; the FK above is authoritative
    email_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)

    # classification at the moment of feedback
    original_primary_category: Mapped[str] = mapped_column(String(24), nullable=False)
    corrected_primary_category: Mapped[str] = mapped_column(String(24), nullable=False)
    # the 15-value Triage label the ML/LLM produced (snapshot for training)
    original_final_category: Mapped[str | None] = mapped_column(String(48))

    # classifier provenance (from the email's latest ProcessingRun)
    classifier_source: Mapped[str | None] = mapped_column(String(32))
    ml_confidence: Mapped[float | None] = mapped_column(Float)
    llm_used: Mapped[bool] = mapped_column(Boolean, default=False)

    email_classified_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)


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
    # Phase 14: user-authored free text — encrypted at rest.
    note: Mapped[str | None] = mapped_column(EncryptedString)

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
    # Phase 14: may be derived from a reminder note / email text — encrypted at
    # rest AND sanitised (raw OTPs/secrets are masked before it is written).
    detail: Mapped[str | None] = mapped_column(EncryptedString)

    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(TZDateTime)

    # Phase 16 — FCM push dispatch state (dedup: a row is pushed at most once).
    pushed_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    push_status: Mapped[str | None] = mapped_column(String(24))

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
class GmailSyncState(Base):
    """Persistent Gmail synchronisation baseline + progress.

    One row per connected user (Phase 15). Survives restart / crash /
    scheduler reload — the incremental sync resumes from ``last_history_id``,
    never from process start time.
    """

    __tablename__ = "gmail_sync_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_pk: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True, nullable=False
    )
    # Phase 14: PII — the connected Gmail address, encrypted at rest.
    account_email: Mapped[str | None] = mapped_column(EncryptedString)

    # when AGENT AMAR started watching this mailbox (the historical unread inbox
    # from before this instant is deliberately NOT ingested)
    monitoring_started_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    # last successful incremental sync
    last_sync_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    # Gmail mailbox historyId processed up to (the resume point)
    last_history_id: Mapped[str | None] = mapped_column(String(32))

    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, onupdate=utcnow)

    user: Mapped["User"] = relationship(back_populates="sync_state")


# Phase 14 — tamper-evident audit chain
AUDIT_EVENT_TYPES = (
    "EMAIL_INGESTED",
    "EMAIL_PROCESSED",
    "EMAIL_VIEWED",
    "EMAIL_RESOLVED",       # user explicitly marked the email done / resolved
    "EMAIL_RECLASSIFIED",   # Phase 18 — user corrected the primary classification
    "ACTION_CREATED",
    "DEADLINE_CREATED",
    "REMINDER_CREATED",
    "NOTIFICATION_SENT",
    "GMAIL_CONNECTED",
    "GMAIL_SYNCED",
)
GENESIS_HASH = "0" * 64


class AuditEvent(Base):
    """One link in an append-only, hash-chained audit ledger.

    **Only non-sensitive metadata.** Never email body / subject / snippet, never
    an OTP, never a sender/recipient address, never an OAuth token or API key.
    ``resource_id`` is an opaque internal id (``gmail_<id>`` / ``act_001`` / a
    row pk), not PII.

    ``record_hash = sha256( canonical_json(metadata) + previous_hash )``. A
    change to any stored field, a broken ``previous_hash`` link, or a gap in
    ``sequence`` is detected by :func:`app.services.audit_service.verify_chain`.
    """

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    audit_id: Mapped[str] = mapped_column(String(36), unique=True, index=True, nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, unique=True, index=True, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)

    event_type: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    resource_type: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(128), index=True)
    # Phase 15: the owning user (nullable — system-level events have none).
    # Part of the hash payload, so tampering with ownership is detectable.
    user_pk: Mapped[int | None] = mapped_column(Integer, index=True)
    # optional, tiny, non-sensitive counters/flags (e.g. {"processed": 2})
    detail: Mapped[dict] = mapped_column(JSON, default=dict)

    previous_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    record_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
