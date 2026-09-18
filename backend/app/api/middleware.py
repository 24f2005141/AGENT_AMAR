"""Production HTTP middleware: request correlation and response hardening.

Deliberately dependency-free (no slowapi/redis): the API is a single small
service in front of a private worker, so an in-process implementation is both
sufficient and one less thing to operate. Everything here is opt-in through
settings so development behaviour is unchanged.
"""

from __future__ import annotations

import time
import uuid
from collections import defaultdict, deque
from threading import Lock

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.logging_setup import secure_logger

logger = secure_logger("agent_amar.http")

#: Header used to correlate a client report with server logs.
REQUEST_ID_HEADER = "X-Request-ID"


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Attach a request id, log one structured line per request, and add the
    standard security response headers.

    The log line never contains the query string, a body, or an Authorization
    header — only method, path, status and duration.
    """

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex[:16]
        request.state.request_id = request_id
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            # The exception handlers below still format the client response;
            # this only guarantees the failure is correlated in the logs.
            logger.exception(
                "request failed method=%s path=%s request_id=%s",
                request.method,
                request.url.path,
                request_id,
            )
            raise
        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.info(
            "method=%s path=%s status=%s duration_ms=%.1f request_id=%s",
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
            request_id,
        )
        response.headers[REQUEST_ID_HEADER] = request_id
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Cache-Control", "no-store")
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """A small fixed-cost limiter for the endpoints that are expensive to
    serve — LLM reply generation and Gmail sync.

    Keyed on the caller's session bearer token (falling back to client host),
    NOT on a client-supplied id, so it cannot be trivially side-stepped. This
    protects the single limited-hardware LLM box and the Gmail API quota; it is
    not a defence against a distributed attacker, which belongs at the edge
    proxy.
    """

    #: path fragment -> settings attribute holding the per-minute allowance
    _RULES = (
        ("/reply-suggestions", "rate_limit_llm_per_minute"),
        ("/reply", "rate_limit_llm_per_minute"),
        ("/gmail/sync", "rate_limit_sync_per_minute"),
    )

    def __init__(self, app, settings):
        super().__init__(app)
        self._settings = settings
        self._hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = Lock()

    @property
    def _live_settings(self):
        """Re-read settings each request so the limiter can be switched off
        (or retuned) by configuration without rebuilding the ASGI app."""
        from app.core.config import get_settings

        try:
            return get_settings()
        except Exception:
            return self._settings

    def _rule_for(self, path: str) -> tuple[str, int] | None:
        for fragment, attr in self._RULES:
            if path.endswith(fragment):
                limit = int(getattr(self._settings, attr, 0) or 0)
                return (fragment, limit) if limit > 0 else None
        return None

    @staticmethod
    def _caller(request: Request) -> str:
        auth = request.headers.get("authorization") or ""
        if auth.lower().startswith("bearer ") and len(auth) > 12:
            # Never key on (or log) the raw token.
            return "s:" + str(hash(auth.strip()))
        client = request.client
        return "h:" + (client.host if client else "unknown")

    async def dispatch(self, request: Request, call_next):
        self._settings = self._live_settings
        if not self._settings.rate_limit_enabled:
            return await call_next(request)
        rule = self._rule_for(request.url.path)
        if rule is None or request.method not in {"POST", "PUT", "PATCH"}:
            return await call_next(request)

        fragment, limit = rule
        key = (self._caller(request), fragment)
        now = time.monotonic()
        with self._lock:
            bucket = self._hits[key]
            while bucket and now - bucket[0] > 60.0:
                bucket.popleft()
            if len(bucket) >= limit:
                retry_after = max(1, int(60.0 - (now - bucket[0])))
                logger.warning(
                    "rate limited path=%s limit=%s/min", request.url.path, limit
                )
                return JSONResponse(
                    status_code=429,
                    content={
                        "detail": "Too many requests. Please wait a moment and try again.",
                        "retry_after_seconds": retry_after,
                    },
                    headers={"Retry-After": str(retry_after)},
                )
            bucket.append(now)
        return await call_next(request)
