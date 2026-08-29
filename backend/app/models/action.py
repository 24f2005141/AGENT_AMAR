"""Action Agent output models.

1:1 encoding of ``04-Schemas/Action Schema.md``. The vault is the source of
truth for the action-type list and field names.

The Action Agent answers exactly one question — *"what does the user need to
do because of this email?"* — and returns this payload inside the common
:class:`~app.models.agent_output.AgentOutput` envelope.

Two fields extend the vault schema (the schema's JSON-Schema draft sets no
``additionalProperties: false``, so this is additive, not a change):
  * per-action ``evidence`` — the concise quote the action was detected from
    (STEP 11 of the phase brief);
  * payload ``detection_method`` — deterministic / llm / llm_fallback_deterministic.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from app.models.triage import ClassificationMethod  # reused: deterministic|llm|llm_fallback_deterministic


class ActionType(str, Enum):
    """The 9 action types from ``04-Schemas/Action Schema.md``."""

    FORM_SUBMISSION = "FORM_SUBMISSION"
    REPLY = "REPLY"
    REGISTRATION = "REGISTRATION"
    DOCUMENT_UPLOAD = "DOCUMENT_UPLOAD"
    PAYMENT = "PAYMENT"
    ATTEND_EVENT = "ATTEND_EVENT"
    COMPLETE_ASSIGNMENT = "COMPLETE_ASSIGNMENT"
    READ_AND_ACKNOWLEDGE = "READ_AND_ACKNOWLEDGE"
    OTHER = "OTHER"


class ActionStatus(str, Enum):
    """Lifecycle status. The Action Agent always emits ``OPEN``; the backend
    advances it later (``04-Schemas/Action Schema.md`` "Rules")."""

    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    DONE = "DONE"
    SKIPPED = "SKIPPED"


class ActionItem(BaseModel):
    """A single discrete action (``04-Schemas/Action Schema.md`` "Single action object")."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    action_id: str = Field(description="Unique within the email, e.g. act_001.")
    action_type: ActionType
    action_description: str = Field(description='One-line imperative ("Submit…", "Reply…").')
    target_link: str | None = Field(default=None, description="URL to act on, if present.")
    related_email: str = Field(description="email_id from Email Schema.")
    blocking: bool = Field(description="Must be done before a deadline / another action.")
    raw_deadline_hint: str | None = Field(
        default=None,
        description="Deadline phrase copied verbatim for context — never normalised "
        "(that is the Deadline Agent's job).",
    )
    confidence: float = Field(ge=0.0, le=1.0)
    status: ActionStatus = Field(default=ActionStatus.OPEN)
    # --- additive: evidence for this action ---
    evidence: str | None = Field(
        default=None, description="Concise quote from the email the action was detected from."
    )


class ActionData(BaseModel):
    """The ``data`` payload of the Action Agent's :class:`AgentOutput`."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    action_required: bool
    actions: list[ActionItem] = Field(default_factory=list)
    # Primary (summary) action — null when action_required is false.
    action_type: ActionType | None = None
    action_description: str | None = None
    related_email: str
    confidence: float = Field(ge=0.0, le=1.0)
    # --- additive: how the actions were detected ---
    detection_method: ClassificationMethod = ClassificationMethod.DETERMINISTIC


class LLMAction(BaseModel):
    """One action as the LLM is constrained to return it."""

    model_config = ConfigDict(extra="ignore")

    action_type: ActionType
    action_description: str = ""
    target_link: str | None = None
    raw_deadline_hint: str | None = None
    blocking: bool = True
    confidence: float = Field(default=0.6, ge=0.0, le=1.0)
    evidence: str = ""


class LLMActionResult(BaseModel):
    """The full object the LLM must return (validated before use)."""

    model_config = ConfigDict(extra="ignore")

    action_required: bool
    actions: list[LLMAction] = Field(default_factory=list)
