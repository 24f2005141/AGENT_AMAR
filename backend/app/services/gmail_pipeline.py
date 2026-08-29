"""The end-to-end fetch pipeline.

    GmailService.list_unread_message_ids()
        -> GmailService.get_message(id)          (raw Gmail payload)
        -> MailIntakeAgent.run(raw)              (deterministic normalization)
        -> AgentOutput (data = NormalizedEmail)

No classification, priority, or deadline logic — that is a later phase.
"""

from __future__ import annotations

from typing import Any

from app.agents.intake_agent import MailIntakeAgent
from app.core.errors import GmailIntegrationError, MessageNotFoundError
from app.models.email import NormalizedEmail
from app.services.gmail_service import GmailService


def fetch_unread_normalized(
    gmail: GmailService,
    intake: MailIntakeAgent,
    *,
    max_results: int = 10,
) -> dict[str, Any]:
    """Fetch unread messages and run each through the Mail Intake Agent.

    Returns a development-friendly structure: a compact summary per email plus
    the full normalized object for inspection. Individual message failures are
    reported per-item instead of failing the whole batch.
    """
    message_ids = gmail.list_unread_message_ids(max_results=max_results)

    emails: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for message_id in message_ids:
        try:
            raw = gmail.get_message(message_id)
        except (GmailIntegrationError, MessageNotFoundError) as exc:
            errors.append({"message_id": message_id, "error": exc.public_message})
            continue

        output = intake.run(raw)
        # Guard: the envelope's data must still validate as a NormalizedEmail.
        normalized = NormalizedEmail.model_validate(output.data)

        emails.append(
            {
                "summary": _summary(normalized, output),
                "intake": {
                    "status": output.status,
                    "confidence": output.confidence,
                    "needs_human_review": output.needs_human_review,
                    "run_id": output.run_id,
                },
                "email": normalized.to_wire(),
            }
        )

    return {
        "count": len(emails),
        "max_results": max_results,
        "unread_ids_seen": len(message_ids),
        "emails": emails,
        "errors": errors,
    }


def _summary(email: NormalizedEmail, output: Any) -> dict[str, Any]:
    return {
        "email_id": email.email_id,
        "thread_id": email.thread_id,
        "sender": email.sender.model_dump(),
        "subject": email.subject,
        "received_at": email.received_at.isoformat(),
        "is_unread": email.is_unread,
        "labels": email.labels,
        "has_attachments": len(email.attachments) > 0,
        "has_links": email.has_links,
    }
