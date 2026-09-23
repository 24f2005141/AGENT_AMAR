"""System connection status.

    GET /api/v1/system/status   backend + configured-LLM connection status

Read-only, no auth (the Flutter status bar needs it before / independent of
login). Lightweight: never runs an LLM completion, Gmail sync, or agent workflow.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_current_user
from app.core.config import Settings, get_settings
from app.db.models import User
from app.models.system import (
    AiModeResponse,
    AiModeUpdate,
    BackendComponent,
    LlmComponent,
    SystemStatusResponse,
)
from app.services.ai_mode_service import (
    ensure_mode_available,
    get_ai_mode_store,
    mode_options,
)
from app.services.system_status import check_llm

router = APIRouter(prefix="/api/v1/system", tags=["system"])


@router.get("/status", response_model=SystemStatusResponse)
def system_status(settings: Settings = Depends(get_settings)) -> SystemStatusResponse:
    llm = check_llm(settings)
    return SystemStatusResponse(
        backend=BackendComponent(status="online"),
        llm=LlmComponent(
            status=llm.status,
            provider=llm.provider,
            model=llm.model,
            detail=llm.detail,
        ),
    )


@router.get("/ai-mode", response_model=AiModeResponse)
def get_ai_mode(settings: Settings = Depends(get_settings)) -> AiModeResponse:
    """Return the active approach and safe availability metadata."""
    return AiModeResponse(
        selected=get_ai_mode_store().get(),
        options=mode_options(settings),
    )


@router.put("/ai-mode", response_model=AiModeResponse)
def set_ai_mode(
    payload: AiModeUpdate,
    settings: Settings = Depends(get_settings),
    _user: User = Depends(get_current_user),
) -> AiModeResponse:
    """Persist the personal backend's active decision approach."""
    try:
        ensure_mode_available(payload.mode, settings)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    get_ai_mode_store().set(payload.mode)
    return AiModeResponse(selected=payload.mode, options=mode_options(settings))
