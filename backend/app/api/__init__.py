"""HTTP layer: FastAPI routers and their dependencies.

Routes stay thin — OAuth and Gmail logic live in ``app/services``.
"""

from app.api.routes_auth import router as auth_router
from app.api.routes_gmail import router as gmail_router
from app.api.routes_monitor import router as monitor_router
from app.api.routes_state import router as state_router

__all__ = ["auth_router", "gmail_router", "monitor_router", "state_router"]
