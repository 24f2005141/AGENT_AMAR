"""Response models for ``GET /api/v1/system/status``."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class BackendComponent(BaseModel):
    #: Always ``"online"`` — if the client got this response, the backend is up.
    status: str = "online"


class LlmComponent(BaseModel):
    #: ``online`` | ``offline`` | ``unconfigured`` | ``unknown``
    status: str
    #: Configured provider: ``none`` | ``ollama`` | ``gemini`` | ``openai`` | ``anthropic``
    provider: str | None = None
    #: Effective model name, when a provider is configured.
    model: str | None = None
    #: Short human-readable note (never a secret / stack trace).
    detail: str | None = None


class SystemStatusResponse(BaseModel):
    backend: BackendComponent = Field(default_factory=BackendComponent)
    llm: LlmComponent


class AiModeOption(BaseModel):
    id: Literal["conventional", "jev", "laya"]
    label: str
    available: bool
    model: str | None = None
    detail: str | None = None


class AiModeResponse(BaseModel):
    selected: Literal["conventional", "jev", "laya"]
    options: list[AiModeOption]


class AiModeUpdate(BaseModel):
    mode: Literal["conventional", "jev", "laya"]
