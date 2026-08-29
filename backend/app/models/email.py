"""The normalized email object.

This is a 1:1 Pydantic encoding of ``04-Schemas/Email Schema.md`` from the
Obsidian vault. That document is the source of truth; if the two ever disagree,
the vault wins and this file should be corrected.

Field-by-field mapping (vault -> model):

============  =========================  ========  =====================================
vault field   type                       required  notes
============  =========================  ========  =====================================
email_id      string                     yes       "gmail_" + raw Gmail message id
thread_id     string                     yes       "gmail_thread_" + raw Gmail thread id
message_id_.. string                     no        RFC Message-ID header
sender        {name?, email}             yes       SenderInfo
to            string[]                   yes       recipient addresses
cc            string[]                   no        defaults to []
reply_to      string                     no
subject       string                     yes       may be an empty string
body          string                     yes       cleaned plain text, never raw HTML
body_format   "text" | "html_converted"  yes       BodyFormat enum
snippet       string                     no
received_at   ISO 8601 (offset)          yes       when the mail server received it
labels        string[]                   yes       Gmail labels
is_unread     boolean                    yes       convenience flag (labels contains UNREAD)
attachments   object[]                   yes       metadata only, never contents
links         string[]                   no        URLs found in body; defaults to []
has_links     boolean                    yes       kept consistent with links
language      string                     no        ISO 639-1, best effort
body_parse_.. boolean                    yes
needs_human.. boolean                    yes
source        string                     yes       "gmail"
ingested_at   ISO 8601 (offset)          yes       when intake processed the message
============  =========================  ========  =====================================
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

#: Placeholder used when a required address cannot be parsed from the raw
#: message. Whenever this appears, ``needs_human_review`` must be ``True``.
UNKNOWN_ADDRESS = "unknown@unknown.invalid"


class BodyFormat(str, Enum):
    """How ``body`` was produced.

    * ``text`` – taken directly from a ``text/plain`` MIME part.
    * ``html_converted`` – the message had no plain-text part, so ``body`` was
      converted from ``text/html``.
    """

    TEXT = "text"
    HTML_CONVERTED = "html_converted"


class SenderInfo(BaseModel):
    """The ``sender`` object: display name (optional) plus address (required)."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(
        default=None, description="Display name from the From header, if any."
    )
    email: str = Field(description="Sender email address (lower-cased).")

    @field_validator("email")
    @classmethod
    def _validate_email(cls, value: str) -> str:
        value = value.strip().lower()
        if value == UNKNOWN_ADDRESS:
            return value
        if not _EMAIL_RE.match(value):
            raise ValueError(f"not a valid email address: {value!r}")
        return value

    @field_validator("name")
    @classmethod
    def _clean_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip().strip('"').strip()
        return value or None


class AttachmentMetadata(BaseModel):
    """Attachment metadata only — file contents are never included."""

    model_config = ConfigDict(extra="forbid")

    filename: str = Field(description="Attachment file name.")
    mime_type: str = Field(description="MIME type, e.g. application/pdf.")
    size_bytes: int = Field(ge=0, description="Size in bytes (0 if unknown).")
    attachment_id: str | None = Field(
        default=None,
        description="Gmail attachmentId, used later to fetch the file if needed.",
    )


class NormalizedEmail(BaseModel):
    """Normalized email object consumed by every downstream AGENT AMAR agent.

    Produced by :class:`app.agents.intake_agent.MailIntakeAgent`. Extra fields
    are rejected so the contract cannot drift silently.
    """

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    # --- identity -----------------------------------------------------------
    email_id: str = Field(min_length=1, description='"gmail_" + raw message id.')
    thread_id: str = Field(min_length=1, description='"gmail_thread_" + raw thread id.')
    message_id_header: str | None = Field(
        default=None, description="RFC 5322 Message-ID header value."
    )

    # --- participants -----------------------------------------------------
    sender: SenderInfo
    to: list[str] = Field(default_factory=list, description="Recipient addresses.")
    cc: list[str] = Field(default_factory=list, description="CC addresses.")
    reply_to: str | None = Field(default=None, description="Reply-To address.")

    # --- content ----------------------------------------------------------
    subject: str = Field(description="Subject line; may be an empty string.")
    body: str = Field(description="Cleaned plain-text body. Never raw HTML.")
    body_format: BodyFormat = Field(description="text | html_converted.")
    snippet: str | None = Field(
        default=None, description="Short preview (~200 chars)."
    )

    # --- metadata -------------------------------------------------------
    received_at: datetime = Field(
        description="When the mail server received the message (offset-aware)."
    )
    labels: list[str] = Field(default_factory=list, description="Gmail labels.")
    is_unread: bool = Field(description="True when labels contains UNREAD.")
    attachments: list[AttachmentMetadata] = Field(default_factory=list)
    links: list[str] = Field(
        default_factory=list, description="URLs found in the body."
    )
    has_links: bool = Field(description="Kept consistent with links.")
    language: str | None = Field(
        default=None, description="Detected ISO 639-1 language code, best effort."
    )

    # --- intake status ------------------------------------------------
    body_parse_error: bool = Field(
        default=False, description="True if a body part could not be decoded."
    )
    needs_human_review: bool = Field(
        default=False,
        description="True if required headers were missing or parsing degraded.",
    )
    source: str = Field(default="gmail", description="Origin system.")
    ingested_at: datetime = Field(
        description="When intake processed the message (offset-aware)."
    )

    # --- validators -----------------------------------------------------
    @field_validator("received_at", "ingested_at")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        """Email Schema rule: timestamps must carry an explicit offset."""
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware (ISO 8601 with offset)")
        return value

    @field_validator("to", "cc")
    @classmethod
    def _clean_address_list(cls, value: list[str]) -> list[str]:
        return [addr.strip() for addr in value if addr and addr.strip()]

    @model_validator(mode="after")
    def _keep_links_consistent(self) -> "NormalizedEmail":
        """``has_links`` is always derived from ``links`` (schema invariant)."""
        object.__setattr__(self, "has_links", len(self.links) > 0)
        return self

    def to_wire(self) -> dict:
        """JSON-serialisable dict (ISO 8601 strings, enum values)."""
        return self.model_dump(mode="json")
