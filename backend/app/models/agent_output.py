"""The common agent-output envelope.

Encodes the envelope from ``04-Schemas/Agent Output Schema.md``. Every AGENT
AMAR agent returns this shape; only ``data`` differs per agent.

For the Mail Intake Agent, ``data`` is the full normalized email object
(see the "Mail Intake Agent" section added to that vault document).
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AgentStatus(str, Enum):
    """Envelope ``status`` field."""

    OK = "ok"
    PARTIAL = "partial"
    ERROR = "error"


class AgentError(BaseModel):
    """One entry in the envelope ``errors`` array."""

    model_config = ConfigDict(extra="forbid")

    code: str
    message: str


class AgentOutput(BaseModel):
    """Uniform wrapper the AMAR Orchestrator consumes from any agent."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    agent: str = Field(description='Agent name, e.g. "Mail Intake Agent".')
    agent_version: str = Field(description="Semver of the agent logic.")
    email_id: str = Field(description="Email id this run refers to.")
    run_id: str = Field(description="Unique id for this invocation.")
    status: AgentStatus
    confidence: float = Field(ge=0.0, le=1.0)
    needs_human_review: bool
    reasoning_summary: str
    data: dict[str, Any] = Field(default_factory=dict)
    errors: list[AgentError] = Field(default_factory=list)
    started_at: datetime
    finished_at: datetime

    @field_validator("started_at", "finished_at")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware (ISO 8601 with offset)")
        return value

    def to_wire(self) -> dict:
        """JSON-serialisable dict."""
        return self.model_dump(mode="json")
