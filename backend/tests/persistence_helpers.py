"""Helpers for Phase 9 persistence tests."""

from __future__ import annotations

from datetime import datetime, timezone

from app.agents.action_agent import ActionAgent
from app.agents.amar_orchestrator import AMAROrchestrator
from app.agents.deadline_agent import DeadlineAgent
from app.agents.priority_agent import PriorityAgent
from app.agents.triage_agent import TriageAgent
from app.core.config import Settings
from app.models.agent_output import AgentOutput
from app.models.email import NormalizedEmail
from tests.triage_helpers import make_email

_NOW = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)


def orchestrator() -> AMAROrchestrator:
    s = Settings()
    return AMAROrchestrator(
        TriageAgent(settings=s), ActionAgent(settings=s),
        DeadlineAgent(settings=s), PriorityAgent(settings=s), settings=s,
    )


def decision_for(email: NormalizedEmail, *, orch: AMAROrchestrator | None = None,
                 now: datetime | None = None) -> AgentOutput:
    return (orch or orchestrator()).process(email, now=now or _NOW)


def internship_email(email_id: str = "gmail_intern1") -> NormalizedEmail:
    e = make_email(
        sender="placement@college.edu",
        subject="Summer Internship 2026 - application open",
        body="Apply via the form https://forms.gle/x and upload your resume "
        "by 5 September 2026, 6:00 PM.",
    )
    e = e.model_copy(update={"email_id": email_id, "thread_id": f"{email_id}_t"})
    return e


def promo_email(email_id: str = "gmail_promo1") -> NormalizedEmail:
    e = make_email(sender="offers@shopdeals.com", subject="50% OFF",
                   body="Limited time sale, buy now!")
    return e.model_copy(update={"email_id": email_id, "thread_id": f"{email_id}_t"})
