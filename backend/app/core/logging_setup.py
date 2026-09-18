"""Log redaction (Phase 14).

A :class:`logging.Filter` that runs every ``agent_amar*`` log message (already
formatted, args flattened) through :func:`app.core.sanitization.mask_sensitive`
before it reaches any handler. Belt-and-braces so a stray f-string or an
exception's ``str(exc)`` can never leak an OTP / password / token / key.

Filters only fire on the logger they are attached to (not ancestors), so
:func:`secure_logger` attaches the filter to each app logger. Call
:func:`install` once at startup to also cover already-configured root handlers
(uvicorn) and any pre-existing ``agent_amar*`` loggers.
"""

from __future__ import annotations

import logging

from app.core.sanitization import mask_sensitive

_FILTER_ATTR = "_amar_redacting"


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001 — never let logging itself crash
            return True
        redacted = mask_sensitive(msg)
        if redacted != msg:
            record.msg = redacted
            record.args = ()
        return True


_redactor = RedactingFilter()


def _has_filter(target: logging.Logger | logging.Handler) -> bool:
    return any(getattr(f, _FILTER_ATTR, False) for f in target.filters)


def _mark(f: logging.Filter) -> logging.Filter:
    setattr(f, _FILTER_ATTR, True)
    return f


setattr(_redactor, _FILTER_ATTR, True)


def secure_logger(name: str) -> logging.Logger:
    """``logging.getLogger(name)`` with the redaction filter attached (once)."""
    log = logging.getLogger(name)
    if not _has_filter(log):
        log.addFilter(_redactor)
    return log


def install() -> None:
    """Attach the redaction filter to root handlers + known app loggers.

    Idempotent. Handlers see propagated records too, so this covers third-party
    loggers (uvicorn access/error) as well.
    """
    root = logging.getLogger()
    for handler in root.handlers:
        if not _has_filter(handler):
            handler.addFilter(_redactor)
    for name in list(logging.root.manager.loggerDict):
        if name == "agent_amar" or name.startswith("agent_amar."):
            secure_logger(name)
    secure_logger("agent_amar")
