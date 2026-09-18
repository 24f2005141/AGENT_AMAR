"""Deterministic sensitive-data detection & masking (Phase 14).

One reusable utility — no per-module regexes. Pattern-based first (fast,
predictable). Not an AI security system; it catches the common, high-signal
shapes: OTP / verification codes, passwords, API keys, bearer / access tokens,
JWTs, generic ``sk-``/``key-`` secrets, AWS keys.

Used to keep raw secrets out of:
  * logs & exception messages  (via :class:`RedactingFilter`)
  * notification payloads      (``NotificationRecord.detail``)
  * the tamper-evident audit chain (which only stores non-sensitive metadata
    anyway — this is defence in depth)
  * debug output

The system may still retain the *original* email encrypted at rest when the
app's core functionality needs it (:mod:`app.core.crypto`).
"""

from __future__ import annotations

import re

_MASK = "******"

# (name, compiled regex). Order matters only for readability. Each pattern
# replaces the *secret span* with _MASK, keeping surrounding context.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # "OTP is 483921", "verification code: 8391", "code 12-345-678"
    (
        "otp",
        re.compile(
            r"(?i)\b(?:one[\s-]?time\s+(?:passcode|password)|otp|verification|"
            r"security|confirmation|auth(?:entication)?|login|access)\s*"
            r"(?:code|pin|passcode|password|number)?\s*(?:is|:|=|->)?\s*"
            r"(?P<secret>(?:\d[\s-]?){4,10})\b"
        ),
    ),
    # "your code is 483921" / "code: 483921" / "code 90-2214"
    (
        "code_is",
        re.compile(r"(?i)\bcode\b\s*(?:is|:|=|->)?\s*(?P<secret>\d(?:[\s-]?\d){3,11})\b"),
    ),
    # password: hunter2  /  pwd = "hunter2"  /  passphrase 's3cr3t'
    (
        "password",
        re.compile(
            r"(?i)\b(?:password|passwd|pwd|passphrase|secret)\b\s*(?:is|:|=)?\s*"
            r"[\"']?(?P<secret>[^\s\"'<>,;]{4,128})[\"']?"
        ),
    ),
    # Authorization: Bearer <token>   /   "bearer eyJ..."
    (
        "bearer",
        re.compile(r"(?i)\bbearer\s+(?P<secret>[A-Za-z0-9\-._~+/]{12,}=*)"),
    ),
    # JWT  aaaa.bbbb.cccc
    (
        "jwt",
        re.compile(r"\b(?P<secret>eyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,})\b"),
    ),
    # OpenAI / Anthropic / generic "sk-..." / "key-..." / "api_key=..."
    (
        "api_key_kv",
        re.compile(
            r"(?i)\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|"
            r"secret[_-]?key|auth[_-]?token|token)\b\s*(?:is|:|=)?\s*"
            r"[\"']?(?P<secret>[A-Za-z0-9\-._~+/]{12,}=*)[\"']?"
        ),
    ),
    (
        "sk_prefix",
        re.compile(r"\b(?P<secret>(?:sk|rk|pk)-[A-Za-z0-9_-]{16,})\b"),
    ),
    # AWS access key id
    (
        "aws_akid",
        re.compile(r"\b(?P<secret>(?:AKIA|ASIA)[A-Z0-9]{16})\b"),
    ),
    # Google OAuth refresh tokens ("1//0e...") and ya29 access tokens
    (
        "google_token",
        re.compile(r"\b(?P<secret>(?:1//[A-Za-z0-9_-]{20,}|ya29\.[A-Za-z0-9_-]{20,}))\b"),
    ),
]


def _mask_span(match: re.Match[str]) -> str:
    s, e = match.span("secret")
    whole = match.group(0)
    off = match.start()
    return whole[: s - off] + _MASK + whole[e - off :]


def mask_sensitive(text: str | None) -> str | None:
    """Return ``text`` with every detected secret span replaced by ``******``.

    ``None`` and non-``str`` pass through unchanged.
    """
    if not isinstance(text, str) or not text:
        return text
    out = text
    for _name, pattern in _PATTERNS:
        out = pattern.sub(_mask_span, out)
    return out


def contains_sensitive(text: str | None) -> bool:
    """True if any sensitive pattern matches ``text``."""
    if not isinstance(text, str) or not text:
        return False
    return any(p.search(text) for _n, p in _PATTERNS)


_SENSITIVE_KEYS = re.compile(
    r"(?i)(pass(word|wd|phrase)?|pwd|secret|token|api[_-]?key|access[_-]?key|"
    r"authorization|auth|otp|code|credential|client[_-]?secret|refresh)"
)


def redact_mapping(data: dict, *, _depth: int = 0) -> dict:
    """Deep-copy ``data`` with values under sensitive-looking keys fully
    replaced by ``******`` and every string value run through
    :func:`mask_sensitive`. For structured log/context dicts."""
    if _depth > 6 or not isinstance(data, dict):
        return data
    out: dict = {}
    for k, v in data.items():
        if isinstance(k, str) and _SENSITIVE_KEYS.search(k):
            out[k] = _MASK
        elif isinstance(v, dict):
            out[k] = redact_mapping(v, _depth=_depth + 1)
        elif isinstance(v, list):
            out[k] = [
                redact_mapping(i, _depth=_depth + 1) if isinstance(i, dict)
                else mask_sensitive(i) if isinstance(i, str) else i
                for i in v
            ]
        elif isinstance(v, str):
            out[k] = mask_sensitive(v)
        else:
            out[k] = v
    return out
