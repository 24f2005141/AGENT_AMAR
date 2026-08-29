"""Priority Agent output models.

Encodes the ``data`` payload from ``01-Agents/Priority Agent.md`` +
``03-Memory/Priority Rules.md``. The vault is the source of truth for the
priority levels, the proximity buckets, and the scoring factors/weights.

The vault payload is:
    priority_score, priority_level, score_breakdown[], notify, monitor,
    reasoning_summary, confidence

This model keeps all of those and **adds** (additive — the Agent Output
Schema draft sets no ``additionalProperties: false``):
    proximity_bucket, time_remaining_seconds, deadline_is_past,
    factors{}, overrides_applied[], scoring_method, reference_time_used
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class PriorityLevel(str, Enum):
    """The 5 levels from ``Priority Rules.md`` — no others."""

    CRITICAL = "CRITICAL"   # 90-100
    URGENT = "URGENT"       # 75-89
    HIGH = "HIGH"           # 55-74
    MEDIUM = "MEDIUM"       # 30-54
    LOW = "LOW"             # 0-29


class ProximityBucket(str, Enum):
    """Deadline proximity buckets from ``Priority Rules.md`` §4."""

    OVERDUE = "OVERDUE"
    WITHIN_1H = "WITHIN_1H"
    WITHIN_24H = "WITHIN_24H"
    WITHIN_72H = "WITHIN_72H"
    LATER = "LATER"
    NONE = "NONE"


class ScoringMethod(str, Enum):
    DETERMINISTIC = "deterministic"
    LLM_ADJUSTED = "deterministic+llm_adjustment"
    LLM_UNAVAILABLE = "deterministic+llm_unavailable"


class ScoreFactor(BaseModel):
    """One line of the ``score_breakdown`` (vault: ``{factor, points}``)."""

    model_config = ConfigDict(extra="forbid")

    factor: str
    points: float
    detail: str | None = None


class PriorityData(BaseModel):
    """The ``data`` payload of the Priority Agent's :class:`AgentOutput`."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    # --- vault contract ---
    priority_score: int = Field(ge=0, le=100)
    priority_level: PriorityLevel
    score_breakdown: list[ScoreFactor] = Field(default_factory=list)
    notify: bool
    monitor: bool
    reasoning_summary: str
    confidence: float = Field(ge=0.0, le=1.0)

    # --- additive ---
    proximity_bucket: ProximityBucket = ProximityBucket.NONE
    time_remaining_seconds: int | None = Field(
        default=None, description="deadline - reference_time; negative if overdue."
    )
    deadline_is_past: bool = False
    factors: dict[str, Any] = Field(default_factory=dict)
    overrides_applied: list[str] = Field(default_factory=list)
    scoring_method: ScoringMethod = ScoringMethod.DETERMINISTIC
    reference_time_used: str = Field(description="ISO 8601 instant used for proximity.")


class LLMPriorityAdjustment(BaseModel):
    """The nudge the LLM may return. The agent clamps ``score_adjustment`` to
    the configured cap (Priority Rules §3: -10..+10) after validation."""

    model_config = ConfigDict(extra="ignore")

    score_adjustment: int = 0
    reasoning: str = ""
