"""Mail Intake Agent — deterministic.

Implements ``01-Agents/Mail Intake Agent.md``:

    Raw Gmail API payload
        -> extract relevant information
        -> clean and normalise data
        -> validate against the Pydantic email model
        -> return normalized email object

There is **no LLM** anywhere in this file. The agent does not classify, assign
priority, detect actions, detect deadlines, or judge importance. It only turns
a raw Gmail message into a :class:`~app.models.email.NormalizedEmail`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.models.agent_output import AgentError, AgentOutput, AgentStatus
from app.models.email import (
    UNKNOWN_ADDRESS,
    AttachmentMetadata,
    BodyFormat,
    NormalizedEmail,
    SenderInfo,
)
from app.services import gmail_service as gmail
from app.utils.text_cleaning import clean_body_text, collapse_whitespace, extract_links, html_to_text

AGENT_NAME = "Mail Intake Agent"
AGENT_VERSION = "0.1.0"

_UNREAD_LABEL = "UNREAD"


def _detect_language(text: str) -> str | None:
    """Best-effort, deterministic language detection.

    Uses ``langdetect`` with a fixed seed when available; returns ``None``
    otherwise (the field is optional in the schema).
    """
    sample = text.strip()
    if len(sample) < 20:
        return None
    try:
        from langdetect import DetectorFactory, detect

        DetectorFactory.seed = 0
        return detect(sample)
    except Exception:
        return None


class MailIntakeAgent:
    """Turns a raw Gmail message resource into a normalized email object."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._tz = self._resolve_tz(self.settings.default_timezone)

    # -- public API --------------------------------------------------------

    def normalize(self, raw_message: dict) -> NormalizedEmail:
        """Return the :class:`NormalizedEmail` for ``raw_message``.

        Never raises for merely-degraded input: missing headers or an
        undecodable body produce a valid object with ``needs_human_review`` /
        ``body_parse_error`` set (per the Email Schema rule). It only raises if
        the input is not a dict-shaped Gmail message at all.
        """
        if not isinstance(raw_message, dict):
            raise TypeError("raw_message must be a Gmail message dict")

        fields, _notes = self._build_fields(raw_message)
        try:
            return NormalizedEmail(**fields)
        except ValidationError:
            # Schema rule: still emit the object, flagged for a human.
            fields["needs_human_review"] = True
            fields = self._coerce_safe_defaults(fields)
            return NormalizedEmail(**fields)

    def run(self, raw_message: dict) -> AgentOutput:
        """Run intake and wrap the result in the Agent Output envelope.

        ``data`` is the full normalized email object, per the Mail Intake Agent
        section of ``04-Schemas/Agent Output Schema.md``.
        """
        started_at = self._now()
        errors: list[AgentError] = []
        try:
            fields, notes = self._build_fields(raw_message)
            try:
                email = NormalizedEmail(**fields)
            except ValidationError as exc:
                errors.append(AgentError(code="validation_failed", message=str(exc)))
                fields["needs_human_review"] = True
                fields = self._coerce_safe_defaults(fields)
                email = NormalizedEmail(**fields)
        except Exception as exc:  # unrecoverable (not a Gmail message shape)
            finished_at = self._now()
            return AgentOutput(
                agent=AGENT_NAME,
                agent_version=AGENT_VERSION,
                email_id="gmail_UNKNOWN",
                run_id=self._run_id(),
                status=AgentStatus.ERROR,
                confidence=0.0,
                needs_human_review=True,
                reasoning_summary=f"Intake failed: {exc}",
                data={},
                errors=[AgentError(code="intake_exception", message=str(exc))],
                started_at=started_at,
                finished_at=finished_at,
            )

        for note in notes:
            errors.append(AgentError(code="degraded", message=note))

        status = AgentStatus.OK
        confidence = 1.0
        if email.needs_human_review or email.body_parse_error:
            status = AgentStatus.PARTIAL
            confidence = 0.6

        return AgentOutput(
            agent=AGENT_NAME,
            agent_version=AGENT_VERSION,
            email_id=email.email_id,
            run_id=self._run_id(),
            status=status,
            confidence=confidence,
            needs_human_review=email.needs_human_review,
            reasoning_summary=self._summary(email, notes),
            data=email.to_wire(),
            errors=errors,
            started_at=started_at,
            finished_at=self._now(),
        )

    # -- internals -------------------------------------------------------

    def _build_fields(self, raw_message: dict) -> tuple[dict, list[str]]:
        """Extract + clean every field. Returns ``(field_dict, degradation_notes)``."""
        notes: list[str] = []
        payload = raw_message.get("payload") or {}
        headers = payload.get("headers") or []

        # --- identity ---------------------------------------------------
        raw_id = raw_message.get("id")
        raw_thread_id = raw_message.get("threadId")
        if not raw_id:
            notes.append("message id missing")
        if not raw_thread_id:
            notes.append("thread id missing")
        email_id = f"{self.settings.gmail_id_prefix}{raw_id}" if raw_id else "gmail_UNKNOWN"
        thread_id = (
            f"{self.settings.gmail_thread_id_prefix}{raw_thread_id}"
            if raw_thread_id
            else "gmail_thread_UNKNOWN"
        )

        # --- sender ---------------------------------------------------
        from_name, from_email = gmail.parse_address(gmail.get_header(headers, "From"))
        if not from_email:
            notes.append("From header missing or unparseable")
            from_email = UNKNOWN_ADDRESS
        sender = SenderInfo(name=from_name, email=from_email)

        # --- recipients ---------------------------------------------
        to = gmail.parse_address_list(gmail.get_header(headers, "To"))
        cc = gmail.parse_address_list(gmail.get_header(headers, "Cc"))
        _rt_name, reply_to = gmail.parse_address(gmail.get_header(headers, "Reply-To"))

        # --- subject ------------------------------------------------
        subject = collapse_whitespace(gmail.get_header(headers, "Subject") or "")

        # --- body -------------------------------------------------
        plain, plain_err = gmail.extract_plain_text_body(payload)
        body_parse_error = plain_err
        if plain is not None:
            body = clean_body_text(plain)
            body_format = BodyFormat.TEXT
        else:
            html, html_err = gmail.extract_html_body(payload)
            body_parse_error = body_parse_error or html_err
            if html is not None:
                body = clean_body_text(html_to_text(html))
                body_format = BodyFormat.HTML_CONVERTED
            else:
                body = ""
                body_format = BodyFormat.TEXT
                body_parse_error = True
                notes.append("no readable text/plain or text/html body part")

        # --- metadata --------------------------------------------
        message_id_header = gmail.get_header(headers, "Message-ID") or gmail.get_header(
            headers, "Message-Id"
        )

        received_at = gmail.internal_date_to_datetime(
            raw_message.get("internalDate"), self.settings.default_timezone
        )
        if received_at is None:
            received_at = gmail.rfc2822_date_to_datetime(
                gmail.get_header(headers, "Date"), self.settings.default_timezone
            )
        if received_at is None:
            notes.append("no internalDate or Date header; using ingest time")
            received_at = self._now()

        labels = gmail.extract_labels(raw_message)
        is_unread = _UNREAD_LABEL in labels

        attachments = [
            AttachmentMetadata(**meta) for meta in gmail.extract_attachments(payload)
        ]

        links = extract_links(body)
        snippet_raw = raw_message.get("snippet")
        snippet = collapse_whitespace(snippet_raw) if snippet_raw else None

        needs_human_review = bool(notes) or body_parse_error

        return (
            {
                "email_id": email_id,
                "thread_id": thread_id,
                "message_id_header": message_id_header,
                "sender": sender,
                "to": to,
                "cc": cc,
                "reply_to": reply_to,
                "subject": subject,
                "body": body,
                "body_format": body_format,
                "snippet": snippet,
                "received_at": received_at,
                "labels": labels,
                "is_unread": is_unread,
                "attachments": attachments,
                "links": links,
                "has_links": bool(links),
                "language": _detect_language(body),
                "body_parse_error": body_parse_error,
                "needs_human_review": needs_human_review,
                "source": "gmail",
                "ingested_at": self._now(),
            },
            notes,
        )

    @staticmethod
    def _coerce_safe_defaults(fields: dict) -> dict:
        """Last-resort fill so a degraded message still validates."""
        fields.setdefault("subject", "")
        fields.setdefault("body", "")
        fields.setdefault("body_format", BodyFormat.TEXT)
        if not isinstance(fields.get("sender"), SenderInfo):
            fields["sender"] = SenderInfo(name=None, email=UNKNOWN_ADDRESS)
        fields["links"] = list(fields.get("links") or [])
        fields["has_links"] = bool(fields["links"])
        return fields

    @staticmethod
    def _summary(email: NormalizedEmail, notes: list[str]) -> str:
        base = (
            f"Parsed Gmail message from {email.sender.email} "
            f"(subject: {email.subject!r}); body_format={email.body_format}, "
            f"{len(email.attachments)} attachment(s), {len(email.labels)} label(s)."
        )
        if notes:
            base += " Degraded: " + "; ".join(notes) + "."
        return base

    def _now(self) -> datetime:
        return datetime.now(self._tz)

    @staticmethod
    def _run_id() -> str:
        now = datetime.now(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")
        return f"run_{now}_{uuid.uuid4().hex[:8]}"

    @staticmethod
    def _resolve_tz(tz_name: str) -> ZoneInfo:
        try:
            return ZoneInfo(tz_name)
        except Exception:
            return ZoneInfo("UTC")
