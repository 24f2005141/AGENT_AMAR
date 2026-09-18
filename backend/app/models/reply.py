"""AI reply-suggestion + send models (Task: "AI Reply Suggestions").

The suggestion generation reuses the existing :class:`~app.services.llm_service.LLMClient`
abstraction; sending reuses the authenticated per-user Gmail credentials. The AI
never sends anything — a suggestion is only text until the user explicitly calls
``POST /api/v1/emails/{email_id}/reply`` with a body they approved.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

#: Exactly this many suggestions are generated / returned, always.
SUGGESTION_COUNT = 3

#: Default stance labels, one per suggestion, in order. The LLM may return its
#: own short label per option; these are the fallback and define the intent.
DEFAULT_SUGGESTION_LABELS: tuple[str, str, str] = ("Direct", "Professional", "Alternative")


class ReplySuggestion(BaseModel):
    """One generated reply option. ``body`` is plain text, ready to send/edit."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description='"option_1" | "option_2" | "option_3".')
    label: str = Field(description="Short stance label, e.g. Direct / Professional / Alternative.")
    body: str = Field(min_length=1, description="The suggested reply text (plain text).")


class ReplySuggestionsResponse(BaseModel):
    """``POST /api/v1/emails/{email_id}/reply-suggestions`` success body."""

    model_config = ConfigDict(extra="forbid")

    email_id: str
    suggestions: list[ReplySuggestion] = Field(min_length=SUGGESTION_COUNT, max_length=SUGGESTION_COUNT)


class ReplySendRequest(BaseModel):
    """``POST /api/v1/emails/{email_id}/reply`` request body.

    ``body`` is the FINAL, user-approved text — an unmodified suggestion, an
    edited suggestion, or a reply the user typed themselves. The backend sends it
    verbatim and never re-generates it.
    """

    model_config = ConfigDict(extra="forbid")

    body: str = Field(min_length=1, max_length=25_000, description="Final reply text to send verbatim.")


class ReplySendResult(BaseModel):
    """``POST /api/v1/emails/{email_id}/reply`` success body."""

    model_config = ConfigDict(extra="forbid")

    email_id: str
    thread_id: str | None = None
    gmail_message_id: str | None = None
    #: True when a matching ``REPLY`` action on the email was marked COMPLETED.
    reply_action_completed: bool = False
    #: True when the email itself was marked done (the reply fulfils its
    #: obligation and no other pending action remains).
    email_marked_completed: bool = False
    #: True when this exact send (same user + email + body) was already performed
    #: moments ago — the stored result is returned and Gmail is NOT hit again.
    duplicate_suppressed: bool = False
