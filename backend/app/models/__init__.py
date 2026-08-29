"""Pydantic models that encode the Obsidian schema contracts."""

from app.models.agent_output import AgentError, AgentOutput, AgentStatus
from app.models.email import (
    AttachmentMetadata,
    BodyFormat,
    NormalizedEmail,
    SenderInfo,
)
from app.models.action import (
    ActionData,
    ActionItem,
    ActionStatus,
    ActionType,
)
from app.models.deadline import (
    DeadlineData,
    DeadlineItem,
    DeadlineKind,
    EventDate,
)
from app.models.decision import (
    ConflictResolution,
    DecisionAction,
    FinalDecision,
    RoutingDecision,
    TraceEntry,
)
from app.models.priority import (
    PriorityData,
    PriorityLevel,
    ProximityBucket,
    ScoreFactor,
    ScoringMethod,
)
from app.models.triage import (
    ClassificationMethod,
    ImportanceEstimate,
    LLMClassification,
    TriageCategory,
    TriageData,
    TriageSignals,
)

__all__ = [
    "AttachmentMetadata",
    "BodyFormat",
    "NormalizedEmail",
    "SenderInfo",
    "AgentError",
    "AgentOutput",
    "AgentStatus",
    "ClassificationMethod",
    "ImportanceEstimate",
    "LLMClassification",
    "TriageCategory",
    "TriageData",
    "TriageSignals",
    "ActionData",
    "ActionItem",
    "ActionStatus",
    "ActionType",
    "DeadlineData",
    "DeadlineItem",
    "DeadlineKind",
    "EventDate",
    "PriorityData",
    "PriorityLevel",
    "ProximityBucket",
    "ScoreFactor",
    "ScoringMethod",
    "ConflictResolution",
    "DecisionAction",
    "FinalDecision",
    "RoutingDecision",
    "TraceEntry",
]
