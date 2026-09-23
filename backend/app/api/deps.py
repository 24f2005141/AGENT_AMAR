"""FastAPI dependency providers.

Kept in one place so routes can request a ready-to-use service and tests can
override any layer with ``app.dependency_overrides``.
"""

from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache

from fastapi import Depends, Header
from sqlalchemy.orm import Session

from app.agents.action_agent import ActionAgent
from app.agents.amar_orchestrator import AMAROrchestrator
from app.agents.deadline_agent import DeadlineAgent
from app.agents.intake_agent import MailIntakeAgent
from app.agents.priority_agent import PriorityAgent
from app.agents.triage_agent import TriageAgent
from app.core.config import Settings, get_settings
from app.core.errors import AuthRequiredError, GmailNotConnectedError
from app.db.models import User
from app.db.session import get_db as _get_db
from app.services.auth_session_service import AuthSessionService
from app.services.ai_mode_service import effective_ai_settings
from app.services.classification_feedback_service import ClassificationFeedbackService
from app.services.deadline_monitor_service import DeadlineMonitorService
from app.services.gmail_sync_service import GmailSyncService
from app.services.persistence_service import PersistenceService
from app.services.reminder_service import ReminderService
from app.services.gmail_auth_service import GmailAuthService
from app.services.gmail_service import GmailService
from app.ml.email_classifier import EmailMLClassifier, get_email_ml_classifier
from app.services.llm_service import LLMClient, build_llm_client
from app.services.decision_service import (
    DecisionClient,
    get_shared_decision_client,
)
from app.services.priority_context import PriorityContext, get_priority_context
from app.services.token_store import DbTokenStore, TokenStore


def get_db() -> Iterator[Session]:
    """Re-exported so tests override one symbol for both the routes and deps."""
    yield from _get_db()


# --- auth / current user ------------------------------------------------

def get_token_store(db: Session = Depends(get_db)) -> TokenStore:
    """Per-user OAuth credential store (Phase 15) — encrypted rows in the DB."""
    return DbTokenStore(db)


def get_auth_service(
    settings: Settings = Depends(get_settings),
    token_store: TokenStore = Depends(get_token_store),
) -> GmailAuthService:
    return GmailAuthService(settings, token_store)


def _bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return authorization.strip() or None


def get_current_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    """Resolve the application session bearer token to a live ``User`` or 401."""
    user = AuthSessionService(db).resolve(_bearer(authorization))
    if user is None:
        raise AuthRequiredError()
    return user


@lru_cache
def _cached_intake_agent() -> MailIntakeAgent:
    return MailIntakeAgent()


def get_intake_agent() -> MailIntakeAgent:
    return _cached_intake_agent()


def get_effective_ai_settings(
    settings: Settings = Depends(get_settings),
) -> Settings:
    """Settings with the persisted Flutter-selected decision mode applied."""
    return effective_ai_settings(settings)


def get_llm_client(settings: Settings = Depends(get_effective_ai_settings)) -> LLMClient:
    """LLM client chosen from settings; NullLLMClient when unconfigured."""
    return build_llm_client(settings)


def get_reply_llm_client(settings: Settings = Depends(get_effective_ai_settings)) -> LLMClient:
    """Same provider as :func:`get_llm_client`, with the longer reply-drafting
    timeout so a slow local model does not 503 the reply-suggestions endpoint."""
    return build_llm_client(settings, timeout=settings.llm_reply_timeout_resolved)


def get_decision_client(settings: Settings = Depends(get_effective_ai_settings)) -> DecisionClient:
    """Selected shared decision client, or a no-op in conventional mode."""
    return get_shared_decision_client(settings)


def get_email_ml_classifier_dep(
    settings: Settings = Depends(get_effective_ai_settings),
) -> EmailMLClassifier | None:
    """Local ML pre-classifier, or ``None`` when disabled / no model present."""
    return get_email_ml_classifier(settings)


def get_triage_agent(
    settings: Settings = Depends(get_effective_ai_settings),
    llm_client: LLMClient = Depends(get_llm_client),
    ml_classifier: EmailMLClassifier | None = Depends(get_email_ml_classifier_dep),
) -> TriageAgent:
    return TriageAgent(settings=settings, llm_client=llm_client, ml_classifier=ml_classifier)


def get_action_agent(
    settings: Settings = Depends(get_effective_ai_settings),
    llm_client: LLMClient = Depends(get_llm_client),
) -> ActionAgent:
    return ActionAgent(settings=settings, llm_client=llm_client)


def get_deadline_agent(
    settings: Settings = Depends(get_effective_ai_settings),
    llm_client: LLMClient = Depends(get_llm_client),
) -> DeadlineAgent:
    return DeadlineAgent(settings=settings, llm_client=llm_client)


def get_priority_context_dep() -> PriorityContext:
    return get_priority_context()


def get_priority_agent(
    settings: Settings = Depends(get_effective_ai_settings),
    llm_client: LLMClient = Depends(get_llm_client),
    context: PriorityContext = Depends(get_priority_context_dep),
) -> PriorityAgent:
    return PriorityAgent(settings=settings, llm_client=llm_client, context=context)


def get_amar_orchestrator(
    settings: Settings = Depends(get_effective_ai_settings),
    triage: TriageAgent = Depends(get_triage_agent),
    action: ActionAgent = Depends(get_action_agent),
    deadline: DeadlineAgent = Depends(get_deadline_agent),
    priority: PriorityAgent = Depends(get_priority_agent),
    decision_client: DecisionClient = Depends(get_decision_client),
) -> AMAROrchestrator:
    return AMAROrchestrator(
        triage,
        action,
        deadline,
        priority,
        settings=settings,
        decision_client=decision_client,
    )


# --- per-user scoped services ----------------------------------------

def get_persistence_service(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> PersistenceService:
    return PersistenceService(db, user_pk=user.id)


def get_deadline_monitor_service(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DeadlineMonitorService:
    return DeadlineMonitorService(db, user_pk=user.id)


def get_reminder_service(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ReminderService:
    return ReminderService(db, user_pk=user.id)


def get_gmail_sync_service(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> GmailSyncService:
    return GmailSyncService(db, user_pk=user.id)


def get_classification_feedback_service(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ClassificationFeedbackService:
    return ClassificationFeedbackService(db, user_pk=user.id)


def get_gmail_service(
    user: User = Depends(get_current_user),
    auth: GmailAuthService = Depends(get_auth_service),
) -> GmailService:
    """An authenticated :class:`GmailService` for the current user, or 401
    (reconnect) if that user's Gmail is not connected."""
    credentials = auth.get_credentials(account_id=str(user.id))
    if credentials is None:
        raise GmailNotConnectedError()
    return GmailService(credentials=credentials)
