"""HTTP layer: FastAPI routers and their dependencies.

Routes stay thin — OAuth and Gmail logic live in ``app/services``.
"""

from app.api.routes_audit import router as audit_router
from app.api.routes_auth import router as auth_router
from app.api.routes_devices import router as devices_router
from app.api.routes_gmail import router as gmail_router
from app.api.routes_monitor import router as monitor_router
from app.api.routes_reply import router as reply_router
from app.api.routes_state import router as state_router
from app.api.routes_system import router as system_router

__all__ = [
    "audit_router",
    "auth_router",
    "devices_router",
    "gmail_router",
    "monitor_router",
    "reply_router",
    "state_router",
    "system_router",
]
