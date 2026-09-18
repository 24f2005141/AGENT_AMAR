"""AI reply suggestions + send (per-user, Gmail-threaded).

    POST /api/v1/emails/{email_id}/reply-suggestions   -> exactly 3 draft replies
    POST /api/v1/emails/{email_id}/reply               -> send a user-approved reply

Both require a bearer session AND that the email belongs to the caller. Kept in
its own router because — unlike ``routes_state`` — these endpoints DO make Gmail
calls (read the original for context/threading, then send).

The AI never sends anything: ``/reply-suggestions`` only returns text, and
``/reply`` sends exactly the ``body`` the client submitted, verbatim, never
re-generated.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.agents.intake_agent import MailIntakeAgent
from app.api.deps import (
    get_current_user,
    get_db,
    get_gmail_service,
    get_intake_agent,
    get_persistence_service,
    get_reply_llm_client,
)
from app.core.config import Settings, get_settings
from app.core.errors import GmailIntegrationError, MessageNotFoundError
from app.core.logging_setup import secure_logger
from app.db.models import User
from app.models.email_content import FullEmailResponse
from app.models.reply import (
    ReplySendRequest,
    ReplySendResult,
    ReplySuggestionsResponse,
)
from app.repositories import EmailRepository
from app.services.gmail_service import GmailService, build_reply_mime, get_header, parse_address_list
from app.services.llm_service import LLMClient
from app.services.persistence_service import PersistenceService
from app.services.reply_service import (
    ReplySuggestionService,
    claim_send,
    release_send,
    remember_send,
)

logger = secure_logger("agent_amar.reply_api")

router = APIRouter(prefix="/api/v1/emails", tags=["reply"])


def _load_owned(db: Session, email_id: str, user: User):
    record = EmailRepository(db).get_by_email_id(email_id, user_pk=user.id, with_children=True)
    # Gmail spam is never shown or acted on anywhere in the app (no reply
    # suggestions, no reply send, no full-body view) — treat it as not found.
    if record is None or record.is_spam:
        raise HTTPException(status_code=404, detail="email not found")
    return record


def _raw_gmail_id(email_id: str, settings: Settings) -> str:
    """``"gmail_18f.."`` (our id) -> ``"18f.."`` (the Gmail API id)."""
    prefix = settings.gmail_id_prefix
    return email_id[len(prefix):] if email_id.startswith(prefix) else email_id


def _fetch_original(gmail: GmailService, email_id: str, settings: Settings) -> dict:
    try:
        return gmail.get_message(_raw_gmail_id(email_id, settings))
    except MessageNotFoundError:
        # the row exists for this user but Gmail no longer has the message
        raise HTTPException(status_code=404, detail="original message not found in Gmail")


_MAX_BODY_CHARS = 100_000


@router.get("/{email_id}/full", response_model=FullEmailResponse)
def get_full_email(
    email_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    gmail: GmailService = Depends(get_gmail_service),
    intake: MailIntakeAgent = Depends(get_intake_agent),
    settings: Settings = Depends(get_settings),
) -> FullEmailResponse:
    """Return the complete email for the authenticated owner.

    Ownership-checked (`404` for an unknown / other-user `email_id`). The full
    body is fetched live from the owner's Gmail and returned as **plain text**
    (the intake normaliser flattens any HTML — no scripts, no raw markup). Raw
    Gmail objects / headers / tokens are never exposed.
    """
    record = _load_owned(db, email_id, user)
    raw = _fetch_original(gmail, email_id, settings)
    normalized = intake.normalize(raw)

    body = normalized.body or ""
    truncated = len(body) > _MAX_BODY_CHARS
    if truncated:
        body = body[:_MAX_BODY_CHARS] + "\n\n[... message truncated ...]"

    return FullEmailResponse(
        email_id=email_id,
        thread_id=record.thread_id,
        subject=normalized.subject or record.subject or "(no subject)",
        sender_name=normalized.sender.name or record.sender_name,
        sender_email=normalized.sender.email or record.sender_email,
        received_at=normalized.received_at or record.received_at,
        body=body,
        body_format=getattr(normalized.body_format, "value", str(normalized.body_format)),
        is_truncated=truncated,
        primary_category=record.primary_category,
        final_category=record.final_category,
        priority_level=record.priority_level,
        action_required=bool(record.action_required),
    )


@router.post("/{email_id}/reply-suggestions", response_model=ReplySuggestionsResponse)
def reply_suggestions(
    email_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    gmail: GmailService = Depends(get_gmail_service),
    intake: MailIntakeAgent = Depends(get_intake_agent),
    llm: LLMClient = Depends(get_reply_llm_client),
    settings: Settings = Depends(get_settings),
) -> ReplySuggestionsResponse:
    """Generate EXACTLY 3 meaningfully different reply drafts for this email.

    Ownership-checked. Uses the shared LLM abstraction — a missing/unavailable
    LLM returns ``503`` (``LLMUnavailableError``); an unusable model response
    returns ``502`` (``LLMResponseError``). Suggestions are not persisted.
    """
    _load_owned(db, email_id, user)
    raw = _fetch_original(gmail, email_id, settings)
    normalized = intake.normalize(raw)

    suggestions = ReplySuggestionService(llm, settings).generate(
        subject=normalized.subject,
        sender=normalized.sender.email,
        body=normalized.body,
    )
    return ReplySuggestionsResponse(email_id=email_id, suggestions=suggestions)


def _finish_reply(svc: PersistenceService, email_id: str) -> tuple[bool, bool]:
    """After a successful send: complete the REPLY action(s) and mark the email
    done. Best effort — the mail is already sent. Returns
    ``(reply_action_completed, email_marked_completed)``."""
    record = svc.complete_reply(email_id)
    if record is None:
        return False, False
    reply_done = any(
        (a.action_type or "").upper() == "REPLY" and a.status == "COMPLETED"
        for a in record.actions
    )
    return reply_done, bool(record.is_completed)


@router.post("/{email_id}/reply", response_model=ReplySendResult)
def send_reply(
    email_id: str,
    body: ReplySendRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    gmail: GmailService = Depends(get_gmail_service),
    settings: Settings = Depends(get_settings),
    persistence: PersistenceService = Depends(get_persistence_service),
) -> ReplySendResult:
    """Send ``body`` as a threaded reply from the authenticated user's Gmail.

    * Ownership-checked (`404` for another user's `email_id`).
    * ``body`` is sent **verbatim** — never re-generated by AI.
    * Threaded: ``In-Reply-To`` / ``References`` headers + Gmail ``threadId``.
    * Double-send safe: an identical ``(user, email, body)`` within ~2 min
      returns the first result and does not hit Gmail again.
    """
    record = _load_owned(db, email_id, user)
    reply_body = body.body.strip()

    is_first, prior = claim_send(user_id=user.id, email_id=email_id, body=reply_body)
    if not is_first:
        if prior is not None:
            return ReplySendResult(**{**prior, "duplicate_suppressed": True})
        # identical send in-flight — treat as a duplicate, do not send again
        return ReplySendResult(
            email_id=email_id,
            thread_id=record.thread_id,
            duplicate_suppressed=True,
        )

    try:
        raw = _fetch_original(gmail, email_id, settings)
        headers = (raw.get("payload") or {}).get("headers") or []
        original_message_id = get_header(headers, "Message-ID") or get_header(headers, "Message-Id")
        reply_to = parse_address_list(get_header(headers, "Reply-To"))
        from_addr = parse_address_list(get_header(headers, "From"))
        to_addresses = reply_to or from_addr
        if not to_addresses:
            raise HTTPException(status_code=422, detail="could not determine a reply recipient")

        mime = build_reply_mime(
            to_addresses=to_addresses,
            body_text=reply_body,
            subject=get_header(headers, "Subject"),
            in_reply_to=original_message_id,
            references=get_header(headers, "References"),
        )
        sent = gmail.send_reply(raw_message=mime, thread_id=raw.get("threadId"))
    except (HTTPException, GmailIntegrationError):
        release_send(user_id=user.id, email_id=email_id, body=reply_body)
        raise
    except Exception:  # noqa: BLE001 — never leak an internal error / stack trace
        release_send(user_id=user.id, email_id=email_id, body=reply_body)
        logger.warning("send_reply failed for %s", email_id)
        raise HTTPException(status_code=502, detail="Could not send the reply. Please try again.")

    action_completed = False
    email_completed = False
    try:
        action_completed, email_completed = _finish_reply(persistence, email_id)
    except Exception:  # noqa: BLE001 — the mail is already sent; state sync is best effort
        logger.warning("reply sent but marking the email complete failed")

    result = ReplySendResult(
        email_id=email_id,
        thread_id=raw.get("threadId") or record.thread_id,
        gmail_message_id=sent.get("id") if isinstance(sent, dict) else None,
        reply_action_completed=action_completed,
        email_marked_completed=email_completed,
        duplicate_suppressed=False,
    )
    remember_send(
        user_id=user.id, email_id=email_id, body=reply_body,
        result=result.model_dump(),
    )
    return result
