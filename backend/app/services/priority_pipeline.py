"""Full pipeline: intake → triage → action → deadline → priority.

No notifications, no reminder scheduling, no persistence, no orchestrator.
Each agent's output is validated and never mutated by the next.
"""

from __future__ import annotations

from typing import Any

from app.agents.action_agent import ActionAgent
from app.agents.deadline_agent import DeadlineAgent
from app.agents.intake_agent import MailIntakeAgent
from app.agents.priority_agent import PriorityAgent
from app.agents.triage_agent import TriageAgent
from app.core.errors import GmailIntegrationError, MessageNotFoundError
from app.models.email import NormalizedEmail
from app.services.gmail_service import GmailService


def fetch_unread_priorities(
    gmail: GmailService,
    intake: MailIntakeAgent,
    triage: TriageAgent,
    action: ActionAgent,
    deadline: DeadlineAgent,
    priority: PriorityAgent,
    *,
    max_results: int = 10,
) -> dict[str, Any]:
    message_ids = gmail.list_unread_message_ids(max_results=max_results)

    emails: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for message_id in message_ids:
        try:
            raw = gmail.get_message(message_id)
        except (GmailIntegrationError, MessageNotFoundError) as exc:
            errors.append({"message_id": message_id, "error": exc.public_message})
            continue

        normalized = NormalizedEmail.model_validate(intake.run(raw).data)
        triage_out = triage.classify(normalized)
        action_out = action.detect(normalized, triage_out)
        deadline_out = deadline.analyze(normalized, triage_out, action_out)
        priority_out = priority.score(normalized, triage_out, action_out, deadline_out)

        pd = priority_out.data
        dd = deadline_out.data
        emails.append(
            {
                "email": {
                    "email_id": normalized.email_id,
                    "sender": normalized.sender.model_dump(),
                    "subject": normalized.subject,
                    "received_at": normalized.received_at.isoformat(),
                },
                "triage": {
                    "category": triage_out.data["category"],
                    "confidence": triage_out.data["confidence"],
                },
                "action": {
                    "action_required": action_out.data["action_required"],
                    "action_type": action_out.data["action_type"],
                    "actions": [a["action_type"] for a in action_out.data["actions"]],
                },
                "deadline": {
                    "has_deadline": dd["deadline_detected"],
                    "normalized_deadline": dd["normalized_deadline"],
                    "ambiguity_flag": dd["ambiguity_flag"],
                    "is_past": dd["is_past"],
                },
                "priority": {
                    "priority_level": pd["priority_level"],
                    "priority_score": pd["priority_score"],
                    "proximity_bucket": pd["proximity_bucket"],
                    "time_remaining_seconds": pd["time_remaining_seconds"],
                    "deadline_is_past": pd["deadline_is_past"],
                    "notify": pd["notify"],
                    "monitor": pd["monitor"],
                    "score_breakdown": pd["score_breakdown"],
                    "factors": pd["factors"],
                    "overrides_applied": pd["overrides_applied"],
                    "scoring_method": pd["scoring_method"],
                    "reasoning_summary": priority_out.reasoning_summary,
                    "confidence": pd["confidence"],
                    "needs_human_review": priority_out.needs_human_review,
                    "reference_time_used": pd["reference_time_used"],
                },
                "priority_envelope": priority_out.to_wire(),
            }
        )

    return {
        "count": len(emails),
        "max_results": max_results,
        "unread_ids_seen": len(message_ids),
        "emails": emails,
        "errors": errors,
    }
