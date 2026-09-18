"""FCM device-registration data access (Phase 16)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.db.models import DeviceRegistration


class DeviceRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_by_token(self, fcm_token: str) -> DeviceRegistration | None:
        return self.session.execute(
            select(DeviceRegistration).where(DeviceRegistration.fcm_token == fcm_token)
        ).scalar_one_or_none()

    def upsert(
        self,
        *,
        user_pk: int,
        fcm_token: str,
        platform: str = "android",
        device_label: str | None = None,
        app_version: str | None = None,
    ) -> DeviceRegistration:
        row = self.get_by_token(fcm_token)
        now = utcnow()
        if row is None:
            row = DeviceRegistration(fcm_token=fcm_token, created_at=now)
            self.session.add(row)
        # reassign ownership if the same handset re-registers under a new user
        row.user_pk = user_pk
        row.platform = platform or "android"
        if device_label is not None:
            row.device_label = device_label
        if app_version is not None:
            row.app_version = app_version
        row.active = True
        row.last_seen_at = now
        self.session.flush()
        return row

    def list_active(self, user_pk: int) -> list[DeviceRegistration]:
        return list(
            self.session.execute(
                select(DeviceRegistration).where(
                    DeviceRegistration.user_pk == user_pk,
                    DeviceRegistration.active.is_(True),
                )
            ).scalars().all()
        )

    def deactivate_token(self, fcm_token: str) -> bool:
        row = self.get_by_token(fcm_token)
        if row is None or not row.active:
            return False
        row.active = False
        row.updated_at = utcnow()
        self.session.flush()
        return True

    def deactivate_for_user(self, user_pk: int, *, except_token: str | None = None) -> int:
        n = 0
        for row in self.list_active(user_pk):
            if except_token is not None and row.fcm_token == except_token:
                continue
            row.active = False
            n += 1
        self.session.flush()
        return n

    def touch(self, fcm_token: str) -> None:
        row = self.get_by_token(fcm_token)
        if row is not None:
            row.last_seen_at = utcnow()
            self.session.flush()
