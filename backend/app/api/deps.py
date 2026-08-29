"""FastAPI dependency providers.

Kept in one place so routes can request a ready-to-use service and tests can
override any layer with ``app.dependency_overrides``.
"""

from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache

from fastapi import Depends
from sqlalchemy.orm import Session

from app.agents.action_agent import ActionAgent
from app.agents.amar_orchestrator import AMAROrchestrator
from app.agents.deadline_agent import DeadlineAgent
from app.agents.intake_agent import MailIntakeAgent
from app.agents.priority_agent import PriorityAgent
from app.agents.triage_agent import TriageAgent
from app.core.config import Settings, get_settings
from app.core.errors import GmailNotConnectedError
from app.db.session import get_db as _get_db
from app.services.deadline_monitor_service import DeadlineMonitorService
from app.services.gmail_sync_service import GmailSyncService
from app.services.persistence_service import PersistenceService
from app.services.reminder_service import ReminderService
from app.services.gmail_auth_service import GmailAuthService
from app.services.gmail_service import GmailService
from app.services.llm_service import LLMClient, build_llm_client
from app.services.priority_context import PriorityContext, get_priority_context
from app.services.token_store import FileTokenStore, TokenStore


@lru_cache
def _cached_token_store(path: str) -> FileTokenStore:
    return FileTokenStore(path)


def get_token_store(settings: Settings = Depends(get_settings)) -> TokenStore:
    """File-backed token store (development). Swap for a DB store later."""
    return _cached_token_store(str(settings.token_storage_dir))


def get_auth_service(
    settings: Settings = Depends(get_settings),
    token_store: TokenStore = Depends(get_token_store),
) -> GmailAuthService:
    return GmailAuthService(settings, token_store)


@lru_cache
def _cached_intake_agent() -> MailIntakeAgent:
    return MailIntakeAgent()


def get_intake_agent() -> MailIntakeAgent:
    return _cached_intake_agent()


def get_llm_client(settings: Settings = Depends(get_settings)) -> LLMClient:
    """LLM client chosen from settings; NullLLMClient when unconfigured."""
    return build_llm_client(settings)


def get_triage_agent(
    settings: Settings = Depends(get_settings),
    llm_client: LLMClient = Depends(get_llm_client),
) -> TriageAgent:
    return TriageAgent(settings=settings, llm_client=llm_client)


def get_action_agent(
    settings: Settings = Depends(get_settings),
    llm_client: LLMClient = Depends(get_llm_client),
) -> ActionAgent:
    return ActionAgent(settings=settings, llm_client=llm_client)


def get_deadline_agent(
    settings: Settings = Depends(get_settings),
    llm_client: LLMClient = Depends(get_llm_client),
) -> DeadlineAgent:
    return DeadlineAgent(settings=settings, llm_client=llm_client)


def get_priority_context_dep() -> PriorityContext:
    return get_priority_context()


def get_priority_agent(
    settings: Settings = Depends(get_settings),
    llm_client: LLMClient = Depends(get_llm_client),
    context: PriorityContext = Depends(get_priority_context_dep),
) -> PriorityAgent:
    return PriorityAgent(settings=settings, llm_client=llm_client, context=context)


def get_amar_orchestrator(
    settings: Settings = Depends(get_settings),
    triage: TriageAgent = Depends(get_triage_agent),
    action: ActionAgent = Depends(get_action_agent),
    deadline: DeadlineAgent = Depends(get_deadline_agent),
    priority: PriorityAgent = Depends(get_priority_agent),
) -> AMAROrchestrator:
    return AMAROrchestrator(triage, action, deadline, priority, settings=settings)


def get_db() -> Iterator[Session]:
    """Re-exported so tests override one symbol for both the routes and deps."""
    yield from _get_db()


def get_persistence_service(db: Session = Depends(get_db)) -> PersistenceService:
    return PersistenceService(db)


def get_deadline_monitor_service(db: Session = Depends(get_db)) -> DeadlineMonitorService:
    return DeadlineMonitorService(db)


def get_reminder_service(db: Session = Depends(get_db)) -> ReminderService:
    return ReminderService(db)


def get_gmail_sync_service(db: Session = Depends(get_db)) -> GmailSyncService:
    return GmailSyncService(db)


def get_gmail_service(
    auth: GmailAuthService = Depends(get_auth_service),
) -> GmailService:
    """An authenticated :class:`GmailService`, or 401 if Gmail is not connected."""
    credentials = auth.get_credentials()
    if credentials is None:
        raise GmailNotConnectedError()
    return GmailService(credentials=credentials)
