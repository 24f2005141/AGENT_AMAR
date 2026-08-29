"""Typed errors for the Gmail integration.

Each carries an ``http_status`` and a safe, public ``message`` so the FastAPI
exception handler can turn it into a clean response **without leaking tokens,
secrets, or raw Google error payloads**.
"""

from __future__ import annotations


class GmailIntegrationError(Exception):
    """Base class for every Gmail-integration failure."""

    http_status: int = 500
    public_message: str = "Gmail integration error."

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.public_message)
        # What we expose to API clients (never the raw cause).
        self.public_message = message or self.public_message


class OAuthConfigError(GmailIntegrationError):
    """GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET missing or malformed."""

    http_status = 503
    public_message = (
        "Google OAuth is not configured on the server "
        "(missing GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET)."
    )


class GmailNotConnectedError(GmailIntegrationError):
    """No stored credentials for the requested account."""

    http_status = 401
    public_message = "Gmail is not connected. Visit /api/v1/auth/google/login first."


class OAuthAccessDeniedError(GmailIntegrationError):
    """The user declined consent on Google's screen."""

    http_status = 400
    public_message = "Google authorization was denied or cancelled."


class OAuthExchangeError(GmailIntegrationError):
    """The authorization-code -> token exchange failed."""

    http_status = 400
    public_message = "Could not complete the Google sign-in. Please try connecting again."


class TokenRefreshError(GmailNotConnectedError):
    """A stored token expired and could not be refreshed."""

    http_status = 401
    public_message = (
        "Your Gmail session expired and could not be refreshed. "
        "Please reconnect via /api/v1/auth/google/login."
    )


class GmailApiError(GmailIntegrationError):
    """Gmail API returned an error we cannot recover from."""

    http_status = 502
    public_message = "Gmail API request failed."


class MessageNotFoundError(GmailIntegrationError):
    """A requested message id does not exist / is not visible."""

    http_status = 404
    public_message = "Message not found."


class GmailHistoryExpiredError(GmailApiError):
    """``startHistoryId`` is too old — Gmail purged that far back in history.

    The sync layer recovers by re-establishing the baseline at the current
    ``historyId`` (a small gap of missed changes is accepted; see docs).
    """

    http_status = 409
    public_message = (
        "Gmail sync baseline is too old; monitoring was re-based to the current mailbox state."
    )
