"""Fetch pipeline with classification + action detection.

    GmailService -> MailIntakeAgent -> NormalizedEmail
                 -> TriageAgent   -> category
                 -> ActionAgent   -> action(s)

No Deadline / Priority agents, no notifications, no persistence.
"""

from __future__ import annotations

from typing import Any

from app.agents.action_agent import ActionAgent
from app.agents.intake_agent import MailIntakeAgent
from app.agents.triage_agent import TriageAgent
from app.core.errors import GmailIntegrationError, MessageNotFoundError
from app.models.email import NormalizedEmail
from app.services.gmail_service import GmailService


def fetch_unread_actions(
    gmail: GmailService,
    intake: MailIntakeAgent,
    triage: TriageAgent,
    action: ActionAgent,
    *,
    max_results: int = 10,
) -> dict[str, Any]:
    """Fetch unread messages → normalize → classify → detect actions."""
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
        triage_output = triage.classify(normalized)
        action_output = action.detect(normalized, triage_output)

        tdata = triage_output.data
        adata = action_output.data

        emails.append(
            {
                "email": normalized.to_wire(),
                "triage": {
                    "category": tdata["category"],
                    "confidence": tdata["confidence"],
                    "classification_method": tdata["signals"]["classification_method"],
                    "needs_human_review": triage_output.needs_human_review,
                },
                "action": {
                    "action_required": adata["action_required"],
                    "action_type": adata["action_type"],
                    "action_description": adata["action_description"],
                    "actions": [
                        {
                            "action_type": a["action_type"],
                            "action_description": a["action_description"],
                            "blocking": a["blocking"],
                            "target_link": a["target_link"],
                            "raw_deadline_hint": a["raw_deadline_hint"],
                            "confidence": a["confidence"],
                            "evidence": a["evidence"],
                        }
                        for a in adata["actions"]
                    ],
                    "confidence": adata["confidence"],
                    "detection_method": adata["detection_method"],
                    "needs_human_review": action_output.needs_human_review,
                    "reasoning_summary": action_output.reasoning_summary,
                },
                "action_envelope": action_output.to_wire(),
            }
        )

    return {
        "count": len(emails),
        "max_results": max_results,
        "unread_ids_seen": len(message_ids),
        "emails": emails,
        "errors": errors,
    }
