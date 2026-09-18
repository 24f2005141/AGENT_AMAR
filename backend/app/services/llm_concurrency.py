"""Global concurrency limit for LLM inference.

The private Ollama host is a single machine with limited RAM/VRAM. Two large
concurrent generations do not run "twice as fast" — they swap, blow past the
request timeout, and can take the box down, which then also breaks the Gmail
worker. So every inference passes through one process-wide semaphore:

    worker/API caller -> acquire slot (bounded wait) -> Ollama -> release

If no slot frees within the queue timeout the caller gets
:class:`LLMUnavailableError`, which the pipeline already treats as "fall back
to the local ML result" — degraded, never crashed.

This is intentionally in-process. With the recommended deployment (one API
container + one worker container) it bounds each process; the hard ceiling for
the box as a whole is the worker's own concurrency, which is 1 by default.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager

from app.core.logging_setup import secure_logger

logger = secure_logger(__name__)

_lock = threading.Lock()
_semaphore: threading.BoundedSemaphore | None = None
_configured_limit = 0
_queue_timeout = 30.0


def configure(max_concurrent: int, queue_timeout_seconds: float) -> None:
    """(Re)configure the limiter. Called once at startup from settings."""
    global _semaphore, _configured_limit, _queue_timeout
    with _lock:
        _configured_limit = max(0, int(max_concurrent))
        _queue_timeout = max(0.1, float(queue_timeout_seconds))
        _semaphore = (
            threading.BoundedSemaphore(_configured_limit) if _configured_limit > 0 else None
        )


def limit() -> int:
    return _configured_limit


class LLMBusyError(RuntimeError):
    """No inference slot became available within the queue timeout."""


@contextmanager
def inference_slot():
    """Hold one inference slot for the duration of the block.

    A no-op when the limit is disabled (``LLM_MAX_CONCURRENT_REQUESTS=0``).
    """
    sem = _semaphore
    if sem is None:
        yield
        return
    acquired = sem.acquire(timeout=_queue_timeout)
    if not acquired:
        logger.warning(
            "LLM inference slot unavailable after %.1fs (limit=%s) — shedding load",
            _queue_timeout,
            _configured_limit,
        )
        raise LLMBusyError(
            f"No LLM capacity after {_queue_timeout:.0f}s (limit {_configured_limit})"
        )
    try:
        yield
    finally:
        sem.release()
