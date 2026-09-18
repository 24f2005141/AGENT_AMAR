"""FCM device-registration endpoints (Phase 16). All require a bearer session.

    POST /api/v1/devices/register     register / refresh this device's FCM token
    POST /api/v1/devices/unregister   deactivate a token (logout on this device)
    GET  /api/v1/devices              list the current user's active devices
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.db.models import User
from app.services.push_service import PushNotificationService

router = APIRouter(prefix="/api/v1/devices", tags=["devices"])


class DeviceRegisterIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fcm_token: str = Field(min_length=8, max_length=512)
    platform: str = "android"
    device_label: str | None = None
    app_version: str | None = None
    # optional: a previous token this device used (rotated) — deactivated cleanly.
    previous_token: str | None = None


class DeviceTokenIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fcm_token: str = Field(min_length=8, max_length=512)


class DeviceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    platform: str
    device_label: str | None = None
    app_version: str | None = None
    active: bool
    last_seen_at: object | None = None


@router.post("/register", response_model=DeviceOut)
def register_device(
    body: DeviceRegisterIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DeviceOut:
    svc = PushNotificationService(db)
    if body.previous_token and body.previous_token != body.fcm_token:
        svc.unregister_device(body.previous_token)
    row = svc.register_device(
        user.id,
        fcm_token=body.fcm_token,
        platform=body.platform,
        device_label=body.device_label,
        app_version=body.app_version,
    )
    db.commit()
    db.refresh(row)
    return DeviceOut.model_validate(row)


@router.post("/unregister")
def unregister_device(
    body: DeviceTokenIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    changed = PushNotificationService(db).unregister_device(body.fcm_token)
    db.commit()
    return {"status": "unregistered" if changed else "not_found"}


@router.get("", response_model=list[DeviceOut])
def list_devices(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[DeviceOut]:
    from app.repositories import DeviceRepository

    return [DeviceOut.model_validate(d) for d in DeviceRepository(db).list_active(user.id)]
