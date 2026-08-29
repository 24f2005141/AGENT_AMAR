"""Shared builders for Triage Agent tests."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.models.agent_output import AgentOutput
from app.models.email import AttachmentMetadata, BodyFormat, NormalizedEmail, SenderInfo
from app.services.llm_service import LLMClient, LLMResponseError, LLMUnavailableError

_TS = datetime(2026, 8, 28, 9, 14, 22, tzinfo=timezone.utc)


def make_email(
    *,
    sender: str = "someone@example.com",
    sender_name: str | None = None,
    subject: str = "Hello",
    body: str = "This is a plain email body with enough text to look real.",
    labels: list[str] | None = None,
    links: list[str] | None = None,
    attachments: list[AttachmentMetadata] | None = None,
    body_format: BodyFormat = BodyFormat.TEXT,
    to: list[str] | None = None,
    received_at: datetime | None = None,
) -> NormalizedEmail:
    """Build a valid NormalizedEmail for agent tests."""
    ts = received_at or _TS
    return NormalizedEmail(
        email_id="gmail_test001",
        thread_id="gmail_thread_test001",
        sender=SenderInfo(name=sender_name, email=sender),
        to=to or ["student@example.com"],
        subject=subject,
        body=body,
        body_format=body_format,
        received_at=ts,
        labels=labels or ["INBOX", "UNREAD"],
        is_unread=True,
        attachments=attachments or [],
        links=links or [],
        has_links=bool(links),
        body_parse_error=False,
        needs_human_review=False,
        source="gmail",
        ingested_at=ts,
    )


def triage_stub(category: str = "OTHER", *, confidence: float = 0.9) -> AgentOutput:
    """A minimal Triage AgentOutput for agents downstream of Triage."""
    return AgentOutput(
        agent="Triage Agent",
        agent_version="0.1.0",
        email_id="gmail_test001",
        run_id="run_stub",
        status="ok",
        confidence=confidence,
        needs_human_review=False,
        reasoning_summary="stub",
        data={"category": category, "confidence": confidence,
              "signals": {"classification_method": "deterministic"}},
        errors=[],
        started_at=_TS,
        finished_at=_TS,
    )


def action_stub(
    actions: list[dict] | None = None,
    *,
    action_required: bool | None = None,
    primary_type: str | None = None,
) -> AgentOutput:
    """A minimal Action AgentOutput for agents downstream of Action.

    ``actions`` items may set just ``action_type``; the rest is filled in.
    """
    items = []
    for i, a in enumerate(actions or [], start=1):
        items.append(
            {
                "action_id": a.get("action_id", f"act_{i:03d}"),
                "action_type": a["action_type"],
                "action_description": a.get("action_description", a["action_type"]),
                "target_link": a.get("target_link"),
                "related_email": "gmail_test001",
                "blocking": a.get("blocking", True),
                "raw_deadline_hint": a.get("raw_deadline_hint"),
                "confidence": a.get("confidence", 0.9),
                "status": "OPEN",
                "evidence": a.get("evidence", ""),
            }
        )
    req = action_required if action_required is not None else bool(items)
    return AgentOutput(
        agent="Action Agent",
        agent_version="0.1.0",
        email_id="gmail_test001",
        run_id="run_stub",
        status="ok",
        confidence=0.9,
        needs_human_review=False,
        reasoning_summary="stub",
        data={
            "action_required": req,
            "actions": items,
            "action_type": primary_type or (items[0]["action_type"] if items else None),
            "action_description": None,
            "related_email": "gmail_test001",
            "confidence": 0.9,
            "detection_method": "deterministic",
        },
        errors=[],
        started_at=_TS,
        finished_at=_TS,
    )


def deadline_stub(
    normalized_deadline: str | None = None,
    *,
    detected: bool | None = None,
    ambiguity_flag: bool = False,
    is_past: bool = False,
    confidence: float = 0.9,
    needs_human_review: bool = False,
) -> AgentOutput:
    """A minimal Deadline AgentOutput for the Priority Agent."""
    has = detected if detected is not None else normalized_deadline is not None or ambiguity_flag
    return AgentOutput(
        agent="Deadline Agent",
        agent_version="0.1.0",
        email_id="gmail_test001",
        run_id="run_stub",
        status="ok",
        confidence=confidence,
        needs_human_review=needs_human_review,
        reasoning_summary="stub",
        data={
            "deadline_detected": bool(has),
            "raw_deadline_text": "stub deadline" if has else None,
            "normalized_deadline": normalized_deadline,
            "timezone": "Asia/Kolkata",
            "ambiguity_flag": ambiguity_flag,
            "ambiguity_reason": "stub" if ambiguity_flag else None,
            "monitoring_required": bool(has),
            "confidence": confidence,
            "reference_time_used": _TS.isoformat(),
            "is_past": is_past,
            "deadlines": [],
            "event_dates": [],
            "detection_method": "deterministic",
        },
        errors=[],
        started_at=_TS,
        finished_at=_TS,
    )


class FakeLLM(LLMClient):
    """Deterministic stand-in for a real LLM client."""

    provider = "fake"

    def __init__(
        self,
        *,
        available: bool = True,
        response: dict[str, Any] | None = None,
        raise_error: Exception | None = None,
    ) -> None:
        self._available = available
        self._response = response
        self._raise = raise_error
        self.calls: list[tuple[str, str]] = []
        self.model = "fake-model"

    @property
    def is_available(self) -> bool:
        return self._available

    def complete_json(self, system: str, user: str, *, max_tokens: int = 512) -> dict[str, Any]:
        self.calls.append((system, user))
        if self._raise is not None:
            raise self._raise
        if self._response is None:
            raise LLMUnavailableError("FakeLLM has no response configured.")
        return self._response
