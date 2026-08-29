"""Gmail fetch endpoints.

    GET /api/v1/gmail/unread          unread → MailIntakeAgent → NormalizedEmail
    GET /api/v1/gmail/unread/triage   ... → TriageAgent → classification

No Action / Deadline / Priority agents.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.agents.action_agent import ActionAgent
from app.agents.amar_orchestrator import AMAROrchestrator
from app.agents.deadline_agent import DeadlineAgent
from app.agents.intake_agent import MailIntakeAgent
from app.agents.priority_agent import PriorityAgent
from app.agents.triage_agent import TriageAgent
from app.api.deps import (
    get_action_agent,
    get_amar_orchestrator,
    get_deadline_agent,
    get_gmail_service,
    get_gmail_sync_service,
    get_intake_agent,
    get_persistence_service,
    get_priority_agent,
    get_triage_agent,
)
from app.services.persistence_service import PersistenceService
from app.services.action_pipeline import fetch_unread_actions
from app.services.amar_pipeline import process_unread
from app.services.deadline_pipeline import fetch_unread_deadlines
from app.services.gmail_pipeline import fetch_unread_normalized
from app.services.gmail_service import GmailService
from app.services.gmail_sync_service import GmailSyncService
from app.services.priority_pipeline import fetch_unread_priorities
from app.services.triage_pipeline import fetch_unread_triaged

router = APIRouter(prefix="/api/v1/gmail", tags=["gmail"])


@router.get("/unread")
def get_unread(
    max_results: int = Query(default=10, ge=1, le=100),
    gmail: GmailService = Depends(get_gmail_service),
    intake: MailIntakeAgent = Depends(get_intake_agent),
) -> dict:
    """Fetch unread Gmail messages and normalize each one."""
    return fetch_unread_normalized(gmail, intake, max_results=max_results)


@router.get("/unread/triage")
def get_unread_triage(
    max_results: int = Query(default=10, ge=1, le=100),
    gmail: GmailService = Depends(get_gmail_service),
    intake: MailIntakeAgent = Depends(get_intake_agent),
    triage: TriageAgent = Depends(get_triage_agent),
) -> dict:
    """Fetch unread messages, normalize, then classify each with the Triage Agent."""
    return fetch_unread_triaged(gmail, intake, triage, max_results=max_results)


@router.get("/unread/actions")
def get_unread_actions(
    max_results: int = Query(default=10, ge=1, le=100),
    gmail: GmailService = Depends(get_gmail_service),
    intake: MailIntakeAgent = Depends(get_intake_agent),
    triage: TriageAgent = Depends(get_triage_agent),
    action: ActionAgent = Depends(get_action_agent),
) -> dict:
    """Fetch unread messages → normalize → classify → detect required actions."""
    return fetch_unread_actions(gmail, intake, triage, action, max_results=max_results)


@router.get("/unread/deadlines")
def get_unread_deadlines(
    max_results: int = Query(default=10, ge=1, le=100),
    gmail: GmailService = Depends(get_gmail_service),
    intake: MailIntakeAgent = Depends(get_intake_agent),
    triage: TriageAgent = Depends(get_triage_agent),
    action: ActionAgent = Depends(get_action_agent),
    deadline: DeadlineAgent = Depends(get_deadline_agent),
) -> dict:
    """Fetch unread messages → normalize → classify → actions → extract deadlines."""
    return fetch_unread_deadlines(
        gmail, intake, triage, action, deadline, max_results=max_results
    )


@router.get("/unread/priorities")
def get_unread_priorities(
    max_results: int = Query(default=10, ge=1, le=100),
    gmail: GmailService = Depends(get_gmail_service),
    intake: MailIntakeAgent = Depends(get_intake_agent),
    triage: TriageAgent = Depends(get_triage_agent),
    action: ActionAgent = Depends(get_action_agent),
    deadline: DeadlineAgent = Depends(get_deadline_agent),
    priority: PriorityAgent = Depends(get_priority_agent),
) -> dict:
    """Full pipeline: normalize → classify → actions → deadlines → priority score."""
    return fetch_unread_priorities(
        gmail, intake, triage, action, deadline, priority, max_results=max_results
    )


@router.get("/unread/process")
def get_unread_process(
    max_results: int = Query(default=10, ge=1, le=100),
    persist: bool = Query(default=True, description="persist Final Decisions to the DB"),
    gmail: GmailService = Depends(get_gmail_service),
    intake: MailIntakeAgent = Depends(get_intake_agent),
    orchestrator: AMAROrchestrator = Depends(get_amar_orchestrator),
    persistence: PersistenceService = Depends(get_persistence_service),
) -> dict:
    """Full pipeline per unread email → Final Decision Object, persisted to SQLite.

    Idempotent: calling this repeatedly updates the same email rows (and appends
    a ProcessingRun) — it never duplicates emails, actions or deadlines.
    """
    return process_unread(
        gmail, intake, orchestrator,
        max_results=max_results,
        persistence=persistence if persist else None,
    )


@router.post("/sync")
def gmail_incremental_sync(
    gmail: GmailService = Depends(get_gmail_service),
    intake: MailIntakeAgent = Depends(get_intake_agent),
    orchestrator: AMAROrchestrator = Depends(get_amar_orchestrator),
    persistence: PersistenceService = Depends(get_persistence_service),
    sync: GmailSyncService = Depends(get_gmail_sync_service),
) -> dict:
    """Incremental Gmail sync (Phase 12).

    First call after connecting only records the monitoring baseline (current
    mailbox ``historyId``) and processes nothing. Later calls use the Gmail
    History API to process **only newly added messages** since the last
    successful sync. Idempotent — safe to call repeatedly / alongside the
    background scheduler (a concurrent run returns ``status: skipped_locked``).
    """
    return sync.sync_new_messages(
        gmail, intake=intake, orchestrator=orchestrator, persistence=persistence
    )


@router.get("/sync/status")
def gmail_sync_status(
    sync: GmailSyncService = Depends(get_gmail_sync_service),
) -> dict:
    """Persistent Gmail monitoring baseline + progress. No Gmail call."""
    state = sync.get_state()
    if state is None:
        return {
            "monitoring": False,
            "account_email": None,
            "monitoring_started_at": None,
            "last_sync_at": None,
            "last_history_id": None,
        }
    return {
        "monitoring": bool(state.last_history_id),
        "account_email": state.account_email,
        "monitoring_started_at": state.monitoring_started_at.isoformat()
        if state.monitoring_started_at else None,
        "last_sync_at": state.last_sync_at.isoformat() if state.last_sync_at else None,
        "last_history_id": state.last_history_id,
    }
