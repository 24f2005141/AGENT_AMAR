"""System connection status.

    GET /api/v1/system/status   backend + configured-LLM connection status

Read-only, no auth (the Flutter status bar needs it before / independent of
login). Lightweight: never runs an LLM completion, Gmail sync, or agent workflow.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.config import Settings, get_settings
from app.models.system import BackendComponent, LlmComponent, SystemStatusResponse
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
