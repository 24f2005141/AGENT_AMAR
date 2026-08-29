"""End-to-end pipeline: Gmail → Mail Intake → AMAR Orchestrator → Final Decision.

Gmail-specific fetching stays here; the orchestrator only sees a
:class:`NormalizedEmail`, so it is reusable for Outlook / uploaded email /
fixtures later (STEP 12).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.agents.amar_orchestrator import AMAROrchestrator, to_activity_log
from app.agents.intake_agent import MailIntakeAgent
from app.core.errors import GmailIntegrationError, MessageNotFoundError
from app.models.email import NormalizedEmail
from app.models.persistence import PersistedRef
from app.services.gmail_service import GmailService
from app.services.persistence_service import PersistenceService


def process_unread(
    gmail: GmailService,
    intake: MailIntakeAgent,
    orchestrator: AMAROrchestrator,
    *,
    max_results: int = 10,
    now: datetime | None = None,
    include_activity_log: bool = True,
    persistence: PersistenceService | None = None,
) -> dict[str, Any]:
    """Fetch unread messages and run each through the full AGENT AMAR pipeline."""
    message_ids = gmail.list_unread_message_ids(max_results=max_results)

    emails: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for message_id in message_ids:
        try:
            raw = gmail.get_message(message_id)
        except (GmailIntegrationError, MessageNotFoundError) as exc:
            errors.append({"message_id": message_id, "error": exc.public_message})
            continue

        intake_out = intake.run(raw)
        normalized = NormalizedEmail.model_validate(intake_out.data)
        decision_env = orchestrator.process(normalized, intake_out, now=now)

        item: dict[str, Any] = {
            "email_id": normalized.email_id,
            "subject": normalized.subject,
            "sender": normalized.sender.model_dump(),
            "received_at": normalized.received_at.isoformat(),
            "status": decision_env.status,
            "final_decision": decision_env.data,
        }
        if include_activity_log:
            item["activity_log"] = to_activity_log(decision_env)

        if persistence is not None:
            record = persistence.persist_decision(normalized, decision_env)
            item["persisted"] = PersistedRef(
                email_id=record.email_id,
                created=len(record.processing_runs) == 1,
                is_viewed=record.is_viewed,
                is_completed=record.is_completed,
                snoozed_until=record.snoozed_until,
                processing_run_count=len(record.processing_runs),
                notification_created=bool(record.notifications),
            ).model_dump()

        emails.append(item)

    return {
        "count": len(emails),
        "max_results": max_results,
        "unread_ids_seen": len(message_ids),
        "emails": emails,
        "errors": errors,
    }
