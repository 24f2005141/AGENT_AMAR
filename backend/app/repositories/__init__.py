"""Data-access layer. One repository per aggregate; sessions are passed in.

No business logic here — that lives in ``app/services/persistence_service.py``.
"""

from app.repositories.action_repository import ActionRepository
from app.repositories.deadline_repository import DeadlineRepository
from app.repositories.email_repository import EmailRepository
from app.repositories.gmail_sync_repository import GmailSyncRepository
from app.repositories.notification_repository import NotificationRepository
from app.repositories.processing_repository import ProcessingRepository
from app.repositories.reminder_repository import ReminderRepository

__all__ = [
    "ActionRepository",
    "DeadlineRepository",
    "EmailRepository",
    "GmailSyncRepository",
    "NotificationRepository",
    "ProcessingRepository",
    "ReminderRepository",
]
