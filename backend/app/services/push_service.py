"""Backend push-notification dispatch (Phase 16).

    Notification decision → persist ``notifications`` row → PushNotificationService
                                                          → FCM → user's devices

Runs entirely server-side and independently of the Flutter app. A notification
row is pushed **at most once** (``notifications.pushed_at``), so repeated
scheduler cycles never re-notify. Push payloads carry **no email content** — only
ids the app uses to fetch the authorised data after the user taps.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.config import Settings, get_settings
from app.core.logging_setup import secure_logger
from app.db.base import utcnow
from app.db.models import EmailRecord, NotificationRecord
from app.db.session import db_session
from app.repositories.device_repository import DeviceRepository
from app.services.fcm_client import FcmClient, build_fcm_client

logger = secure_logger("agent_amar.push")

# Generic, non-sensitive copy per notification_type (NEVER the subject/body).
_COPY: dict[str, tuple[str, str]] = {
    "new_priority_email": ("Important email", "A new email needs your attention."),
    "deadline_escalation": ("Deadline approaching", "An email deadline is coming up."),
    "deadline_passed": ("Deadline passed", "A deadline for one of your emails has passed."),
    "ambiguous_deadline": ("Check a deadline", "An email looks time-sensitive — please review it."),
    "user_reminder": ("Reminder", "You asked to be reminded about an email."),
}
_ALARM_COPY = ("Urgent: deadline alarm", "An urgent deadline needs action now.")


def render_copy(notification_type: str, *, requires_alarm: bool = False) -> tuple[str, str]:
    if requires_alarm:
        return _ALARM_COPY
    return _COPY.get(notification_type, ("AGENT AMAR", "You have a new notification."))


class PushNotificationService:
    def __init__(self, session: Session, *, fcm: FcmClient | None = None,
                 settings: Settings | None = None) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.fcm = fcm or build_fcm_client(self.settings)
        self.devices = DeviceRepository(session)

    # -- registration ------------------------------------------------

    def register_device(self, user_pk: int, *, fcm_token: str, platform: str = "android",
                        device_label: str | None = None, app_version: str | None = None):
        return self.devices.upsert(
            user_pk=user_pk, fcm_token=fcm_token, platform=platform,
            device_label=device_label, app_version=app_version,
        )

    def unregister_device(self, fcm_token: str) -> bool:
        return self.devices.deactivate_token(fcm_token)

    # -- sending --------------------------------------------------

    def send_to_user(
        self,
        user_pk: int,
        *,
        notification_id: int,
        title: str,
        body: str,
        type: str,
        severity: str = "NORMAL",
        email_id: str | None = None,
        requires_alarm: bool = False,
    ) -> dict:
        """Send one notification to every active device of ``user_pk``. Never raises.

        Invalid tokens (FCM ``UNREGISTERED`` / mismatch) are deactivated.
        """
        devices = self.devices.list_active(user_pk)
        if not devices:
            return {"status": "no_devices", "sent": 0, "devices": 0}

        data = {
            "notification_id": str(notification_id),
            "email_id": email_id or "",
            "type": type,
            "severity": severity,
            "requires_alarm": "true" if requires_alarm else "false",
        }
        sent = 0
        last_error: str | None = None
        for device in devices:
            try:
                result = self.fcm.send(device.fcm_token, title=title, body=body, data=data)
            except Exception:  # noqa: BLE001 — a device must not break the loop
                logger.warning("fcm send raised for device %s", device.id)
                last_error = "exception"
                continue
            if result.ok:
                sent += 1
            else:
                last_error = result.error
                if result.token_invalid:
                    self.devices.deactivate_token(device.fcm_token)
        self.session.commit()

        if sent == len(devices):
            status = "sent"
        elif sent > 0:
            status = "partial"
        elif last_error == "push_disabled":
            status = "disabled"
        else:
            status = "failed"
        return {"status": status, "sent": sent, "devices": len(devices)}


# --- fire-and-forget dispatch of freshly-created notification rows -------

def dispatch_unpushed(user_pk: int | None = None, *, fcm: FcmClient | None = None) -> dict:
    """Push every notification row not yet pushed (optionally for one user).

    Opens its own short session (like the audit writer) so it is decoupled from
    the caller's transaction. Failures are logged, never raised.
    """
    summary = {"considered": 0, "pushed": 0}
    try:
        settings = get_settings()
        cutoff = utcnow() - timedelta(minutes=max(1, settings.push_max_age_minutes))
        with db_session() as session:
            stmt = (
                select(NotificationRecord)
                .join(EmailRecord, NotificationRecord.email_pk == EmailRecord.id)
                .options(selectinload(NotificationRecord.email))
                .where(
                    NotificationRecord.pushed_at.is_(None),
                    NotificationRecord.status != "SKIPPED",
                    NotificationRecord.created_at >= cutoff,
                    EmailRecord.user_pk.is_not(None),
                )
                .order_by(NotificationRecord.id)
                .limit(200)
            )
            if user_pk is not None:
                stmt = stmt.where(EmailRecord.user_pk == user_pk)
            rows = list(session.execute(stmt).scalars().all())
            if not rows:
                return summary

            svc = PushNotificationService(session, fcm=fcm, settings=settings)
            for note in rows:
                summary["considered"] += 1
                owner = note.email.user_pk if note.email is not None else None
                if owner is None:
                    note.pushed_at = utcnow()
                    note.push_status = "no_owner"
                    continue
                title, body = render_copy(
                    note.notification_type, requires_alarm=note.requires_alarm
                )
                res = svc.send_to_user(
                    owner,
                    notification_id=note.id,
                    title=title,
                    body=body,
                    type=note.notification_type,
                    severity=note.severity,
                    email_id=note.email.email_id if note.email is not None else None,
                    requires_alarm=note.requires_alarm,
                )
                note.pushed_at = utcnow()
                note.push_status = res["status"]
                if res["status"] in ("sent", "partial"):
                    summary["pushed"] += 1
            session.commit()
    except Exception:  # noqa: BLE001 — push must never break agent processing
        logger.exception("push dispatch failed")
    return summary
