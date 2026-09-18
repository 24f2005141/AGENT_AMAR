"""Full-email response model (Phase 18).

The stored ``EmailRecord`` keeps only a short ``snippet`` (the full body is never
persisted). This endpoint fetches the message live from the owner's Gmail and
returns the **intake-normalised plain-text body** — the Mail Intake Agent already
converts any ``text/html`` part to text (``app.utils.text_cleaning.html_to_text``
drops ``<script>`` / ``<style>`` and every tag), so the payload can never carry
executable markup. Raw Gmail API objects, headers and tokens are not exposed.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class FullEmailResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email_id: str
    thread_id: str | None = None
    subject: str
    sender_name: str | None = None
    sender_email: str
    received_at: datetime | None = None

    #: Always plain text. ``body_format`` says whether the original was
    #: ``text`` (a real text/plain part) or ``html_converted`` (we flattened HTML).
    body: str
    body_format: str = "text"
    #: True if the returned body was clipped for size.
    is_truncated: bool = False

    #: Classification passthrough so the full-email screen can show / correct it
    #: without a second round-trip.
    primary_category: str = "LOW_PRIORITY"
    final_category: str = "OTHER"
    priority_level: str = "LOW"
    action_required: bool = False
