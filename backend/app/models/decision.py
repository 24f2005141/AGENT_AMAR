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

``agent_trace`` is enriched from the vault's bare string list to structured
entries (STEP 10 needs status / confidence / method / errors for observability).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.models.priority import PriorityLevel, ProximityBucket


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
