"""Triage Agent output models.

Encodes the ``data`` payload from ``01-Agents/Triage Agent.md`` /
``04-Schemas/Agent Output Schema.md``. The vault is the source of truth for the
category list and field names.

The Triage Agent answers exactly one question — *"What kind of email is this?"* —
and returns this payload inside the common :class:`~app.models.agent_output.AgentOutput`
envelope.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class TriageCategory(str, Enum):
    """The 15 categories defined in ``03-Memory/Classification Rules.md``."""

    INTERNSHIP = "INTERNSHIP"
    PLACEMENT = "PLACEMENT"
    JOB_OPPORTUNITY = "JOB_OPPORTUNITY"
    ASSIGNMENT = "ASSIGNMENT"
    EXAM = "EXAM"
    FACULTY_ANNOUNCEMENT = "FACULTY_ANNOUNCEMENT"
    REPLY_REQUIRED = "REPLY_REQUIRED"
    ACADEMIC_INFORMATION = "ACADEMIC_INFORMATION"
    PROJECT_UPDATE = "PROJECT_UPDATE"
    EVENT = "EVENT"
    PROMOTIONAL = "PROMOTIONAL"
    NEWSLETTER = "NEWSLETTER"
    SPAM = "SPAM"
    SOCIAL = "SOCIAL"
    OTHER = "OTHER"


class ImportanceEstimate(str, Enum):
    """First-guess importance band — NOT the final priority (that is the Priority Agent)."""

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class ClassificationMethod(str, Enum):
    DETERMINISTIC = "deterministic"
    LLM = "llm"
    LLM_FALLBACK_DETERMINISTIC = "llm_fallback_deterministic"


#: Categories whose typical priority band is LOW — the Orchestrator skips
#: Action/Deadline analysis for these (``01-Agents/AMAR Orchestrator.md``).
LOW_BAND_CATEGORIES: frozenset[TriageCategory] = frozenset(
    {
        TriageCategory.PROMOTIONAL,
        TriageCategory.NEWSLETTER,
        TriageCategory.SPAM,
        TriageCategory.SOCIAL,
    }
)

#: The "opportunity" categories that win precedence over EVENT / NEWSLETTER /
#: ACADEMIC_INFORMATION (Classification Rules, precedence rule 2).
OPPORTUNITY_CATEGORIES: frozenset[TriageCategory] = frozenset(
    {
        TriageCategory.INTERNSHIP,
        TriageCategory.PLACEMENT,
        TriageCategory.JOB_OPPORTUNITY,
    }
)

#: Typical priority band per category (Triage Agent.md "Categories" table).
CATEGORY_PRIORITY_BAND: dict[TriageCategory, ImportanceEstimate] = {
    TriageCategory.INTERNSHIP: ImportanceEstimate.HIGH,
    TriageCategory.PLACEMENT: ImportanceEstimate.HIGH,
    TriageCategory.JOB_OPPORTUNITY: ImportanceEstimate.HIGH,
    TriageCategory.ASSIGNMENT: ImportanceEstimate.HIGH,
    TriageCategory.EXAM: ImportanceEstimate.HIGH,
    TriageCategory.FACULTY_ANNOUNCEMENT: ImportanceEstimate.HIGH,
    TriageCategory.REPLY_REQUIRED: ImportanceEstimate.HIGH,
    TriageCategory.ACADEMIC_INFORMATION: ImportanceEstimate.MEDIUM,
    TriageCategory.PROJECT_UPDATE: ImportanceEstimate.MEDIUM,
    TriageCategory.EVENT: ImportanceEstimate.MEDIUM,
    TriageCategory.OTHER: ImportanceEstimate.MEDIUM,
    TriageCategory.PROMOTIONAL: ImportanceEstimate.LOW,
    TriageCategory.NEWSLETTER: ImportanceEstimate.LOW,
    TriageCategory.SPAM: ImportanceEstimate.LOW,
    TriageCategory.SOCIAL: ImportanceEstimate.LOW,
}


class TriageSignals(BaseModel):
    """Structured evidence used for the decision.

    Open-ended by design (``extra="allow"``): the Triage Agent may attach any
    additional evidence keys it finds useful.
    """

    model_config = ConfigDict(extra="allow")

    keywords: list[str] = Field(default_factory=list)
    sender_in_important_list: bool = False
    sender_importance: str | None = None
    has_form_link: bool = False
    classification_method: ClassificationMethod = ClassificationMethod.DETERMINISTIC
    category_scores: dict[str, float] = Field(default_factory=dict)
    precedence_applied: list[str] = Field(default_factory=list)
    conflicting_signals: bool = False


class TriageData(BaseModel):
    """The ``data`` payload of the Triage Agent's :class:`AgentOutput`."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    category: TriageCategory
    subcategory: str | None = None
    importance_estimate: ImportanceEstimate
    further_analysis_required: bool
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning_summary: str
    signals: TriageSignals = Field(default_factory=TriageSignals)


class LLMClassification(BaseModel):
    """Shape the LLM is constrained to return (validated before use)."""

    model_config = ConfigDict(extra="ignore")

    category: TriageCategory
    subcategory: str | None = None
    importance_estimate: ImportanceEstimate | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str = ""
