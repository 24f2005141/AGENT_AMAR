"""Gmail message fetching + parsing support.

Two responsibilities, kept separate from everything else:

* **Parsing helpers** (module-level functions) — turn a raw Gmail API message
  resource (``users.messages.get`` with ``format=full``) into the raw pieces the
  :class:`~app.agents.intake_agent.MailIntakeAgent` needs.
* **:class:`GmailService`** — a thin authenticated client that lists unread
  message ids and fetches full messages. It returns **raw** Gmail payloads; it
  never normalizes them and never performs AI analysis.

Normalization stays entirely in the Mail Intake Agent.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Iterator
from datetime import datetime, timezone
from email.utils import getaddresses, parsedate_to_datetime, parseaddr
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from app.core.errors import (
    GmailApiError,
    GmailHistoryExpiredError,
    GmailNotConnectedError,
    MessageNotFoundError,
)

if TYPE_CHECKING:  # avoid importing google libs at module import time for tests
    from google.oauth2.credentials import Credentials

# ---------------------------------------------------------------------------
# Header helpers
# ---------------------------------------------------------------------------


def get_header(headers: list[dict[str, str]] | None, name: str) -> str | None:
    """Return the first header value matching ``name`` (case-insensitive).

    Gmail delivers headers as ``[{"name": "...", "value": "..."}, ...]``.
    Missing headers return ``None`` rather than raising.
    """
    if not headers:
        return None
    target = name.lower()
    for header in headers:
        if str(header.get("name", "")).lower() == target:
            value = header.get("value")
            return value if value is not None else None
    return None


def parse_address(raw: str | None) -> tuple[str | None, str | None]:
    """Split a header value like ``"Placement Cell <placement@x.edu>"``.

    Returns ``(display_name_or_None, email_or_None)``.
    """
    if not raw:
        return None, None
    name, addr = parseaddr(raw)
    name = name.strip() or None
    addr = addr.strip().lower() or None
    return name, addr


def parse_address_list(raw: str | None) -> list[str]:
    """Parse a ``To`` / ``Cc`` header into a list of bare email addresses."""
    if not raw:
        return []
    addresses: list[str] = []
    for _name, addr in getaddresses([raw]):
        addr = addr.strip().lower()
        if addr:
            addresses.append(addr)
    return addresses


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------


def internal_date_to_datetime(
    internal_date_ms: str | int | None,
    tz_name: str = "UTC",
) -> datetime | None:
    """Convert Gmail ``internalDate`` (epoch milliseconds, UTC) to a datetime.

    The result is timezone-aware and expressed in ``tz_name`` so the intake
    agent can render an ISO 8601 string with an explicit offset.
    """
    if internal_date_ms is None:
        return None
    try:
        seconds = int(internal_date_ms) / 1000.0
    except (TypeError, ValueError):
        return None
    dt_utc = datetime.fromtimestamp(seconds, tz=timezone.utc)
    try:
        return dt_utc.astimezone(ZoneInfo(tz_name))
    except Exception:  # unknown tz name -> keep UTC
        return dt_utc


def rfc2822_date_to_datetime(
    raw: str | None,
    tz_name: str = "UTC",
) -> datetime | None:
    """Parse an RFC 2822 ``Date`` header. Fallback when ``internalDate`` is absent."""
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:  # some senders omit the offset
        dt = dt.replace(tzinfo=timezone.utc)
    try:
        return dt.astimezone(ZoneInfo(tz_name))
    except Exception:
        return dt


# ---------------------------------------------------------------------------
# MIME payload traversal
# ---------------------------------------------------------------------------


def iter_parts(payload: dict[str, Any] | None) -> Iterator[dict[str, Any]]:
    """Depth-first walk over a Gmail payload and every nested ``parts`` entry.

    Yields the payload itself and all descendants (multipart containers
    included), so callers can filter by ``mimeType``.
    """
    if not payload:
        return
    yield payload
    for part in payload.get("parts", []) or []:
        yield from iter_parts(part)


def decode_base64url(data: str | None) -> tuple[bytes, bool]:
    """Decode Gmail's URL-safe base64 body data.

    Returns ``(decoded_bytes, had_error)``. On failure returns ``(b"", True)``
    so the caller can set ``body_parse_error`` instead of crashing.
    """
    if not data:
        return b"", False
    padded = data + "=" * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode(padded.encode("ascii")), False
    except (binascii.Error, ValueError):
        return b"", True


def _part_charset(part: dict[str, Any]) -> str:
    """Best-effort charset from a part's Content-Type header; default utf-8."""
    ctype = get_header(part.get("headers"), "Content-Type") or ""
    for token in ctype.split(";"):
        token = token.strip()
        if token.lower().startswith("charset="):
            return token.split("=", 1)[1].strip().strip('"') or "utf-8"
    return "utf-8"


def _decode_part_text(part: dict[str, Any]) -> tuple[str | None, bool]:
    """Decode a single leaf part's body to text. Returns ``(text, had_error)``."""
    body = part.get("body") or {}
    raw_data = body.get("data")
    if raw_data is None:
        return None, False
    decoded, err = decode_base64url(raw_data)
    if err:
        return None, True
    charset = _part_charset(part)
    try:
        return decoded.decode(charset, errors="replace"), False
    except (LookupError, ValueError):
        return decoded.decode("utf-8", errors="replace"), False


def _is_attachment(part: dict[str, Any]) -> bool:
    disposition = (get_header(part.get("headers"), "Content-Disposition") or "").lower()
    if disposition.startswith("attachment"):
        return True
    body = part.get("body") or {}
    return bool(part.get("filename")) and bool(body.get("attachmentId"))


def extract_plain_text_body(payload: dict[str, Any] | None) -> tuple[str | None, bool]:
    """Return the first ``text/plain`` body found (skipping attachments).

    Returns ``(text_or_None, body_parse_error)``.
    """
    parse_error = False
    for part in iter_parts(payload):
        if part.get("mimeType") != "text/plain":
            continue
        if _is_attachment(part):
            continue
        text, err = _decode_part_text(part)
        parse_error = parse_error or err
        if text is not None:
            return text, parse_error
    return None, parse_error


def extract_html_body(payload: dict[str, Any] | None) -> tuple[str | None, bool]:
    """Return the first ``text/html`` body found (skipping attachments).

    Returns ``(html_or_None, body_parse_error)``.
    """
    parse_error = False
    for part in iter_parts(payload):
        if part.get("mimeType") != "text/html":
            continue
        if _is_attachment(part):
            continue
        text, err = _decode_part_text(part)
        parse_error = parse_error or err
        if text is not None:
            return text, parse_error
    return None, parse_error


def extract_attachments(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Collect attachment *metadata* (never contents).

    Each item: ``{filename, mime_type, size_bytes, attachment_id}``.
    """
    attachments: list[dict[str, Any]] = []
    for part in iter_parts(payload):
        if not _is_attachment(part):
            continue
        body = part.get("body") or {}
        attachments.append(
            {
                "filename": part.get("filename") or "unnamed",
                "mime_type": part.get("mimeType") or "application/octet-stream",
                "size_bytes": int(body.get("size") or 0),
                "attachment_id": body.get("attachmentId"),
            }
        )
    return attachments


def extract_labels(message: dict[str, Any] | None) -> list[str]:
    """Return ``labelIds`` from a Gmail message resource."""
    if not message:
        return []
    labels = message.get("labelIds") or []
    return [str(label) for label in labels]


# ---------------------------------------------------------------------------
# Authenticated Gmail client
# ---------------------------------------------------------------------------


class GmailFetchNotConfigured(GmailNotConnectedError):
    """Backwards-compatible alias (Slice 1 name) for "no credentials".

    Subclasses :class:`~app.core.errors.GmailNotConnectedError` so new code can
    catch the base class and old tests can still catch this exact name.
    """

    http_status = 401
    public_message = "Gmail is not connected. Visit /api/v1/auth/google/login first."


_UNREAD_QUERY_LABEL = "UNREAD"
_MAX_RESULTS_CAP = 100


class GmailService:
    """Thin authenticated Gmail client.

    Pass either an authorized ``Credentials`` object (production) or a
    pre-built ``service`` resource (tests / dependency injection). With
    neither, any API call raises :class:`GmailFetchNotConfigured`.
    """

    def __init__(
        self,
        credentials: "Credentials | None" = None,
        *,
        service: Any = None,
        user_id: str = "me",
    ) -> None:
        self._credentials = credentials
        self._service = service
        self.user_id = user_id

    # -- client construction --------------------------------------------

    @property
    def service(self) -> Any:
        """The googleapiclient Gmail resource, built lazily from credentials."""
        if self._service is not None:
            return self._service
        if self._credentials is None:
            raise GmailFetchNotConfigured()
        from googleapiclient.discovery import build

        self._service = build(
            "gmail", "v1", credentials=self._credentials, cache_discovery=False
        )
        return self._service

    # -- public API ------------------------------------------------------

    def list_unread_message_ids(self, max_results: int = 25) -> list[str]:
        """Return ids of unread messages, newest first.

        ``max_results`` is clamped to ``[1, 100]``. An empty inbox (no unread
        messages) returns ``[]`` — not an error.
        """
        limit = max(1, min(int(max_results or 1), _MAX_RESULTS_CAP))
        try:
            response = (
                self.service.users()
                .messages()
                .list(
                    userId=self.user_id,
                    labelIds=[_UNREAD_QUERY_LABEL],
                    maxResults=limit,
                )
                .execute()
            )
        except GmailNotConnectedError:
            raise
        except Exception as exc:  # HttpError and friends
            raise _translate_api_error(exc) from exc

        messages = response.get("messages") or []
        return [m["id"] for m in messages if m.get("id")]

    def get_message(self, message_id: str) -> dict[str, Any]:
        """Fetch one full raw Gmail message resource (``format=full``).

        The payload is returned **unmodified**. Normalization is the Mail
        Intake Agent's job.
        """
        if not message_id:
            raise MessageNotFoundError("No message id provided.")
        try:
            return (
                self.service.users()
                .messages()
                .get(userId=self.user_id, id=message_id, format="full")
                .execute()
            )
        except GmailNotConnectedError:
            raise
        except Exception as exc:
            raise _translate_api_error(exc, message_id=message_id) from exc

    def get_profile_email(self) -> str | None:
        """Return the connected account's email address, or ``None``."""
        try:
            profile = self.service.users().getProfile(userId=self.user_id).execute()
            return profile.get("emailAddress")
        except Exception:
            return None

    # -- incremental sync (Phase 12) -----------------------------------

    def get_history_id(self) -> str | None:
        """Current mailbox ``historyId`` (from ``users.getProfile``).

        This is the sync baseline: process nothing before it, everything after.
        """
        try:
            profile = self.service.users().getProfile(userId=self.user_id).execute()
        except GmailNotConnectedError:
            raise
        except Exception as exc:
            raise _translate_api_error(exc) from exc
        hid = profile.get("historyId")
        return str(hid) if hid is not None else None

    def list_added_message_ids_since(
        self,
        start_history_id: str,
        *,
        label_id: str | None = _UNREAD_QUERY_LABEL,
        max_messages: int = 50,
        max_pages: int = 20,
    ) -> tuple[list[str], str | None]:
        """Message ids **added** since ``start_history_id`` + the latest historyId.

        Uses ``users.history.list`` with ``historyTypes=['messageAdded']``. When
        ``label_id`` is set, only messages still carrying that label are kept
        (default: ``UNREAD`` — newly arrived unread mail).

        Raises :class:`GmailHistoryExpiredError` when ``start_history_id`` is too
        old for Gmail to serve (HTTP 404) — the caller re-baselines.
        Newest-first is NOT guaranteed; ids are de-duplicated and capped at
        ``max_messages``.
        """
        start = str(start_history_id)
        latest = start
        seen: set[str] = set()
        ids: list[str] = []
        page_token: str | None = None

        for _ in range(max(1, max_pages)):
            params: dict[str, Any] = {
                "userId": self.user_id,
                "startHistoryId": start,
                "historyTypes": ["messageAdded"],
            }
            if label_id:
                params["labelId"] = label_id
            if page_token:
                params["pageToken"] = page_token
            try:
                response = self.service.users().history().list(**params).execute()
            except GmailNotConnectedError:
                raise
            except Exception as exc:
                if _error_status(exc) == 404:
                    raise GmailHistoryExpiredError() from exc
                raise _translate_api_error(exc) from exc

            if response.get("historyId") is not None:
                latest = str(response["historyId"])

            for record in response.get("history") or []:
                for added in record.get("messagesAdded") or []:
                    msg = added.get("message") or {}
                    mid = msg.get("id")
                    if not mid or mid in seen:
                        continue
                    if label_id and label_id not in (msg.get("labelIds") or []):
                        continue
                    seen.add(mid)
                    ids.append(mid)
                    if len(ids) >= max_messages:
                        return ids, latest

            page_token = response.get("nextPageToken")
            if not page_token:
                break

        return ids, latest


def _error_status(exc: Exception) -> int | None:
    """Best-effort HTTP status from a googleapiclient / httplib2 error."""
    status = getattr(getattr(exc, "resp", None), "status", None)
    if status is None:
        status = getattr(exc, "status_code", None)
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None


def _translate_api_error(exc: Exception, *, message_id: str | None = None) -> Exception:
    """Map a googleapiclient error onto our typed errors (no payload leakage)."""
    status = _error_status(exc)
    if status in (401, 403):
        return GmailNotConnectedError("Gmail rejected the stored credentials.")
    if status == 404:
        return MessageNotFoundError(
            f"Message {message_id!r} not found." if message_id else "Message not found."
        )
    return GmailApiError(
        f"Gmail API error (HTTP {status})." if status else "Gmail API request failed."
    )
