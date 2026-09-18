"""Decode the Google ID token returned by the OAuth code exchange (Phase 15).

The ID token is a signed JWT carrying the user's stable ``sub``, their email and
(optionally) their display name. We verify the signature + audience with
``google-auth`` — no extra network call is required beyond the certs fetch the
library caches.

Tests monkeypatch :func:`decode_id_token` so the suite never touches Google.
"""

from __future__ import annotations

from dataclasses import dataclass

from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token

from app.core.logging_setup import secure_logger

logger = secure_logger("agent_amar.google_identity")

_ISSUERS = {"accounts.google.com", "https://accounts.google.com"}


class GoogleIdentityError(ValueError):
    """The ID token was missing, malformed, or failed verification."""


@dataclass(frozen=True)
class GoogleIdentity:
    sub: str
    email: str
    name: str | None = None


def decode_id_token(raw_id_token: str | None, *, client_id: str) -> GoogleIdentity:
    """Verify a Google ID token and return the identity it asserts."""
    if not raw_id_token:
        raise GoogleIdentityError("no id_token in the OAuth response")
    try:
        claims = google_id_token.verify_oauth2_token(
            raw_id_token, google_requests.Request(), client_id
        )
    except Exception as exc:  # noqa: BLE001 — library raises bare ValueError
        raise GoogleIdentityError(f"id_token verification failed: {type(exc).__name__}") from exc

    if claims.get("iss") not in _ISSUERS:
        raise GoogleIdentityError("unexpected id_token issuer")
    sub = claims.get("sub")
    if not sub:
        raise GoogleIdentityError("id_token has no subject")
    email = claims.get("email") or ""
    name = claims.get("name") or None
    return GoogleIdentity(sub=str(sub), email=str(email), name=name)
