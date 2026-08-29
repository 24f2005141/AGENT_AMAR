"""Fetch pipeline: intake → triage → action → deadline.

No Priority agent, no notifications, no persistence.
"""

from __future__ import annotations

from typing import Any

from app.agents.action_agent import ActionAgent
from app.agents.deadline_agent import DeadlineAgent
from app.agents.intake_agent import MailIntakeAgent
from app.agents.triage_agent import TriageAgent
from app.core.errors import GmailIntegrationError, MessageNotFoundError
from app.models.email import NormalizedEmail
from app.services.gmail_service import GmailService


def fetch_unread_deadlines(
    gmail: GmailService,
    intake: MailIntakeAgent,
    triage: TriageAgent,
    action: ActionAgent,
    deadline: DeadlineAgent,
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

        dd = deadline_out.data
        emails.append(
            {
                "email": normalized.to_wire(),
                "triage": {
                    "category": triage_out.data["category"],
                    "confidence": triage_out.data["confidence"],
                },
                "action": {
                    "action_required": action_out.data["action_required"],
                    "action_type": action_out.data["action_type"],
                    "actions": [
                        {"action_id": a["action_id"], "action_type": a["action_type"]}
                        for a in action_out.data["actions"]
                    ],
                },
                "deadline": {
                    "has_deadline": dd["deadline_detected"],
                    "primary": {
                        "raw_deadline_text": dd["raw_deadline_text"],
                        "normalized_deadline": dd["normalized_deadline"],
                        "timezone": dd["timezone"],
                        "ambiguity_flag": dd["ambiguity_flag"],
                        "ambiguity_reason": dd["ambiguity_reason"],
                        "is_past": dd["is_past"],
                    },
                    "deadlines": dd["deadlines"],
                    "event_dates": dd["event_dates"],
                    "monitoring_required": dd["monitoring_required"],
                    "reference_time_used": dd["reference_time_used"],
                    "confidence": dd["confidence"],
                    "detection_method": dd["detection_method"],
                    "needs_human_review": deadline_out.needs_human_review,
                    "reasoning_summary": deadline_out.reasoning_summary,
                },
                "deadline_envelope": deadline_out.to_wire(),
            }
        )

    return {
        "count": len(emails),
        "max_results": max_results,
        "unread_ids_seen": len(message_ids),
        "emails": emails,
        "errors": errors,
    }
