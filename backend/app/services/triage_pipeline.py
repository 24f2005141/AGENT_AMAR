"""Fetch pipeline with classification.

    GmailService  ->  MailIntakeAgent  ->  NormalizedEmail  ->  TriageAgent
    ->  Classification result + AgentOutput envelope

Only classification is added. No Action / Deadline / Priority agents.
"""

from __future__ import annotations

from typing import Any

from app.agents.intake_agent import MailIntakeAgent
from app.agents.triage_agent import TriageAgent
from app.core.errors import GmailIntegrationError, MessageNotFoundError
from app.models.email import NormalizedEmail
from app.services.gmail_service import GmailService


def fetch_unread_triaged(
    gmail: GmailService,
    intake: MailIntakeAgent,
    triage: TriageAgent,
    *,
    max_results: int = 10,
) -> dict[str, Any]:
    """Fetch unread messages, normalize, then classify each one."""
    message_ids = gmail.list_unread_message_ids(max_results=max_results)

    emails: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for message_id in message_ids:
        try:
            raw = gmail.get_message(message_id)
        except (GmailIntegrationError, MessageNotFoundError) as exc:
            errors.append({"message_id": message_id, "error": exc.public_message})
            continue

        intake_output = intake.run(raw)
        normalized = NormalizedEmail.model_validate(intake_output.data)

        triage_output = triage.classify(normalized)
        tdata = triage_output.data

        emails.append(
            {
                "email": normalized.to_wire(),
                "triage": {
                    "category": tdata["category"],
                    "subcategory": tdata["subcategory"],
                    "importance_estimate": tdata["importance_estimate"],
                    "further_analysis_required": tdata["further_analysis_required"],
                    "confidence": tdata["confidence"],
                    "needs_human_review": triage_output.needs_human_review,
                    "classification_method": tdata["signals"]["classification_method"],
                    "reasoning_summary": triage_output.reasoning_summary,
                },
                "triage_envelope": triage_output.to_wire(),
            }
        )

    return {
        "count": len(emails),
        "max_results": max_results,
        "unread_ids_seen": len(message_ids),
        "emails": emails,
        "errors": errors,
    }
