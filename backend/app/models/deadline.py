"""Deadline Agent output models.

Encodes the ``data`` payload from ``01-Agents/Deadline Agent.md``.

The vault contract is **singular** (one deadline — "the one attached to the
primary action"). Phase 6 requires **multiple** deadlines linked to actions, so
this payload keeps the vault's singular top-level fields (they describe the
*primary* deadline, which is exactly what the Priority Agent consumes:
``normalized_deadline`` + ``ambiguity_flag``) and **adds** a ``deadlines[]``
array for the full list — the same additive pattern used for the Action Agent.
See ``Deadline Agent.md`` "Backend implementation notes".
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from app.models.triage import ClassificationMethod  # deterministic|llm|llm_fallback_deterministic


class DeadlineKind(str, Enum):
    """A due-date/cutoff vs a scheduled event date (which is NOT a deadline)."""

    DEADLINE = "DEADLINE"
    EVENT_DATE = "EVENT_DATE"


class DeadlineItem(BaseModel):
    """One extracted deadline (``Deadline Agent.md`` output fields, per-item)."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    deadline_id: str = Field(description="Unique within the email, e.g. dl_001.")
    raw_deadline_text: str = Field(description="Verbatim phrase copied from the email.")
    normalized_deadline: str | None = Field(
        default=None, description="ISO 8601 (offset-aware), or null if unresolvable."
    )
    timezone: str = Field(description="IANA tz name used for normalisation.")
    date_only: bool = Field(
        default=False, description="True when the email gave a date but no time."
    )
    ambiguity_flag: bool = False
    ambiguity_reason: str | None = None
    is_past: bool = Field(
        default=False, description="normalized_deadline < reference_time_used."
    )
    confidence: float = Field(ge=0.0, le=1.0)
    action_context: str | None = Field(
        default=None, description="ActionType this deadline belongs to, if linked."
    )
    related_action_id: str | None = Field(
        default=None, description="Action Agent action_id this deadline belongs to."
    )
    source: ClassificationMethod = ClassificationMethod.DETERMINISTIC
    evidence: str | None = Field(default=None, description="Sentence the phrase came from.")


class EventDate(BaseModel):
    """A date that was detected but classified as an event, not a deadline
    (kept for transparency / manual validation — additive)."""

    model_config = ConfigDict(extra="forbid")

    raw_text: str
    normalized: str | None = None
    reason: str = Field(description="Why this date is an event, not a deadline.")


class DeadlineData(BaseModel):
    """The ``data`` payload of the Deadline Agent's :class:`AgentOutput`."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    # --- vault singular contract (describes the PRIMARY deadline) ---
    deadline_detected: bool
    raw_deadline_text: str | None = None
    normalized_deadline: str | None = None
    timezone: str
    ambiguity_flag: bool = False
    ambiguity_reason: str | None = None
    monitoring_required: bool = False
    confidence: float = Field(ge=0.0, le=1.0)
    reference_time_used: str = Field(description="ISO 8601 instant used to resolve relatives.")
    is_past: bool = False  # additive — primary deadline is already overdue

    # --- additive: the full picture ---
    deadlines: list[DeadlineItem] = Field(default_factory=list)
    event_dates: list[EventDate] = Field(default_factory=list)
    detection_method: ClassificationMethod = ClassificationMethod.DETERMINISTIC


class LLMDeadline(BaseModel):
    """One deadline as the LLM is constrained to return it."""

    model_config = ConfigDict(extra="ignore")

    raw_deadline_text: str
    normalized_deadline: str | None = None
    kind: DeadlineKind = DeadlineKind.DEADLINE
    date_only: bool = False
    is_ambiguous: bool = False
    ambiguity_reason: str | None = None
    action_context: str | None = None
    confidence: float = Field(default=0.6, ge=0.0, le=1.0)
    evidence: str = ""


class LLMDeadlineResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    has_deadline: bool
    deadlines: list[LLMDeadline] = Field(default_factory=list)
