"""The Final Decision Object.

Merges ``01-Agents/AMAR Orchestrator.md`` (Final Decision Output) and the
"AMAR Orchestrator (final decision)" section of ``04-Schemas/Agent Output
Schema.md``. The vault is the source of truth for the field names.

Vault contract fields kept as-is:
    email_id, final_category, action_required, primary_action_type, deadline,
    deadline_ambiguous, priority_level, priority_score,
    routing{store, notify, monitor, folder_label},
    conflicts_resolved[], agent_trace[], needs_human_review

Additive (the Agent Output Schema draft sets no ``additionalProperties: false``):
    actions[]  — projection of the Action Agent's actions (types + blocking)
    deadline_is_past, proximity_bucket  — from the Priority Agent
    review_reasons[]  — why review is / isn't needed (STEP 9 "make clear WHY")
    primary_category  — the ONE mutually-exclusive inbox bucket for the UI
                        (reply_required > action_required > important > low_priority);
                        derived deterministically from the agent outputs above so a
                        given email never shows in two primary inbox sections.

``agent_trace`` is enriched from the vault's bare string list to structured
entries (STEP 10 needs status / confidence / method / errors for observability).
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from app.models.priority import PriorityLevel, ProximityBucket


class PrimaryCategory(str, Enum):
    """The single, mutually-exclusive inbox bucket an email belongs to.

    Priority order (first match wins): a reply the sender is waiting on beats a
    non-reply action, which beats a merely high-priority email, which beats
    everything else. Every email maps to exactly one of these — the Flutter
    inbox sections filter on it directly, so an email never appears twice.

    The 15-value ``final_category`` (Triage) and the ``action_required`` /
    ``priority_level`` signals are all still present as secondary metadata.
    """

    REPLY_REQUIRED = "REPLY_REQUIRED"
    ACTION_REQUIRED = "ACTION_REQUIRED"
    IMPORTANT = "IMPORTANT"
    LOW_PRIORITY = "LOW_PRIORITY"


#: Priority levels that make an otherwise-unactionable email "important".
IMPORTANT_PRIORITY_LEVELS = frozenset({"HIGH", "URGENT", "CRITICAL"})


def derive_primary_category(
    *,
    final_category: str | None,
    action_required: bool,
    primary_action_type: str | None = None,
    action_types: Iterable[str] | None = None,
    priority_level: str | None = None,
) -> PrimaryCategory:
    """Collapse the agent signals into the ONE mutually-exclusive inbox bucket.

    Priority order (first match wins) so an email is never in two sections:

      1. ``REPLY_REQUIRED`` — the sender is waiting on the user's reply
         (Triage said ``REPLY_REQUIRED``, OR there is a ``REPLY`` action).
      2. ``ACTION_REQUIRED`` — a non-reply action the user must take.
      3. ``IMPORTANT`` — high/urgent/critical priority, nothing actionable.
      4. ``LOW_PRIORITY`` — everything else.

    This is the single source of truth for the bucket: the orchestrator calls it
    on a live decision, and the DB backfill calls it on stored columns.
    """
    types = {(t or "").upper() for t in (action_types or [])}
    is_reply = (
        (final_category or "").upper() == "REPLY_REQUIRED"
        or (primary_action_type or "").upper() == "REPLY"
        or "REPLY" in types
    )
    if is_reply:
        return PrimaryCategory.REPLY_REQUIRED
    if action_required:
        return PrimaryCategory.ACTION_REQUIRED
    if (priority_level or "").upper() in IMPORTANT_PRIORITY_LEVELS:
        return PrimaryCategory.IMPORTANT
    return PrimaryCategory.LOW_PRIORITY


class RoutingDecision(BaseModel):
    """What should happen next — the orchestrator decides, it does not act."""

    model_config = ConfigDict(extra="forbid")

    store: bool
    notify: bool
    monitor: bool
    folder_label: str


class DecisionAction(BaseModel):
    """A projection of one Action Agent action (references Action Schema types)."""

    model_config = ConfigDict(extra="forbid")

    action_id: str = "act_001"
    action_type: str
    action_description: str | None = None
    blocking: bool = False
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    target_link: str | None = None
    raw_deadline_hint: str | None = None


class DecisionDeadline(BaseModel):
    """A projection of one Deadline Agent deadline (for persistence — STEP 6)."""

    model_config = ConfigDict(extra="forbid")

    deadline_id: str = "dl_001"
    raw_deadline_text: str | None = None
    normalized_deadline: str | None = None
    timezone: str = "UTC"
    date_only: bool = False
    ambiguity_flag: bool = False
    ambiguity_reason: str | None = None
    is_past: bool = False
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    action_context: str | None = None
    related_action_id: str | None = None


class ConflictResolution(BaseModel):
    """One cross-agent conflict the orchestrator resolved."""

    model_config = ConfigDict(extra="forbid")

    rule: str
    detail: str


class TraceEntry(BaseModel):
    """One agent's line in the execution trace (no PII, no email bodies)."""

    model_config = ConfigDict(extra="forbid")

    agent: str
    status: str                      # ok | partial | error | skipped
    confidence: float | None = None
    method: str | None = None        # deterministic | llm | *_fallback_deterministic | ...
    fallback_used: bool = False
    duration_ms: int | None = None
    error_codes: list[str] = Field(default_factory=list)


class FinalDecision(BaseModel):
    """The AMAR Orchestrator's ``data`` payload."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    email_id: str
    thread_id: str | None = None
    source: str = "gmail"
    final_category: str
    category_confidence: float | None = None
    #: The ONE inbox bucket for the UI (see :class:`PrimaryCategory`). Derived,
    #: mutually exclusive — Flutter filters on this so nothing is double-listed.
    primary_category: PrimaryCategory = PrimaryCategory.LOW_PRIORITY
    action_required: bool
    primary_action_type: str | None = None
    actions: list[DecisionAction] = Field(default_factory=list)
    deadline: str | None = None                     # primary normalised ISO 8601
    deadline_ambiguous: bool = False
    deadline_is_past: bool = False
    deadlines: list[DecisionDeadline] = Field(default_factory=list)
    proximity_bucket: ProximityBucket = ProximityBucket.NONE
    priority_level: PriorityLevel
    priority_score: int = Field(ge=0, le=100)
    routing: RoutingDecision
    needs_human_review: bool
    review_reasons: list[str] = Field(default_factory=list)
    conflicts_resolved: list[ConflictResolution] = Field(default_factory=list)
    agent_trace: list[TraceEntry] = Field(default_factory=list)
