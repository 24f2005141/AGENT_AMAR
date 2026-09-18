"""Thin wrapper over Firebase Cloud Messaging (Phase 16).

The real client uses the ``firebase-admin`` SDK. It is created lazily and only
when a credential source is configured; otherwise :class:`NullFcmClient` is used
so the pipeline runs unchanged with push simply skipped. Tests inject a fake.

No Firebase secret ever reaches Flutter — the service account lives only here,
server-side (``FIREBASE_CREDENTIALS_FILE`` / ``FIREBASE_CREDENTIALS_JSON``).
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass

from app.core.config import Settings, get_settings
from app.core.logging_setup import secure_logger

logger = secure_logger("agent_amar.fcm")


@dataclass(frozen=True)
class FcmResult:
    ok: bool
    token_invalid: bool = False
    error: str | None = None


class FcmClient:
    """Sends one message per call. Never raises."""

    def send(self, token: str, *, title: str, body: str, data: dict[str, str]) -> FcmResult:  # noqa: ARG002
        raise NotImplementedError

    @property
    def enabled(self) -> bool:
        return False


class NullFcmClient(FcmClient):
    """Used when FCM is disabled / unconfigured. Every send is a no-op."""

    def send(self, token: str, *, title: str, body: str, data: dict[str, str]) -> FcmResult:
        return FcmResult(ok=False, error="push_disabled")


_ADMIN_LOCK = threading.Lock()
_admin_app = None  # firebase_admin.App singleton


class FirebaseFcmClient(FcmClient):
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._messaging = None
        self._init_error: str | None = None

    @property
    def enabled(self) -> bool:
        return self._init_error is None

    def _ensure(self) -> bool:
        global _admin_app
        if self._messaging is not None:
            return True
        if self._init_error is not None:
            return False
        try:
            import firebase_admin
            from firebase_admin import credentials, messaging

            with _ADMIN_LOCK:
                if _admin_app is None:
                    if self.settings.firebase_credentials_json.strip():
                        cred = credentials.Certificate(
                            json.loads(self.settings.firebase_credentials_json)
                        )
                    else:
                        cred = credentials.Certificate(self.settings.firebase_credentials_file)
                    opts = {}
                    if self.settings.firebase_project_id:
                        opts["projectId"] = self.settings.firebase_project_id
                    _admin_app = firebase_admin.initialize_app(cred, opts or None)
            self._messaging = messaging
            logger.info("firebase messaging initialised")
            return True
        except Exception as exc:  # noqa: BLE001 — bad creds ⇒ push disabled, app still runs
            self._init_error = type(exc).__name__
            logger.warning("firebase messaging unavailable (%s) — push disabled", self._init_error)
            return False

    def send(self, token: str, *, title: str, body: str, data: dict[str, str]) -> FcmResult:
        if not self._ensure():
            return FcmResult(ok=False, error=self._init_error or "fcm_init_failed")
        m = self._messaging
        try:
            msg = m.Message(
                token=token,
                notification=m.Notification(title=title, body=body),
                data={k: str(v) for k, v in data.items()},
                android=m.AndroidConfig(priority="high"),
            )
            m.send(msg)
            return FcmResult(ok=True)
        except Exception as exc:  # noqa: BLE001
            name = type(exc).__name__
            invalid = name in {
                "UnregisteredError", "SenderIdMismatchError", "InvalidArgumentError"
            }
            if not invalid:
                logger.warning("fcm send failed: %s", name)
            return FcmResult(ok=False, token_invalid=invalid, error=name)


def build_fcm_client(settings: Settings | None = None) -> FcmClient:
    settings = settings or get_settings()
    if not settings.push_configured:
        return NullFcmClient()
    return FirebaseFcmClient(settings)
