"""In-memory fakes for the Gmail API resource (no network, no real OAuth)."""

from __future__ import annotations

from typing import Any


def make_http_error(status: int, message: str = "error"):
    """Build a googleapiclient HttpError with the given HTTP status."""
    from googleapiclient.errors import HttpError

    resp = type("FakeResp", (), {"status": status, "reason": message})()
    return HttpError(resp, f'{{"error": {{"message": "{message}"}}}}'.encode())


class _Execable:
    def __init__(self, result: Any = None, error: Exception | None = None) -> None:
        self._result = result
        self._error = error

    def execute(self, **_kwargs: Any) -> Any:
        if self._error is not None:
            raise self._error
        return self._result


class _MessagesResource:
    def __init__(self, fake: "FakeGmailResource") -> None:
        self._fake = fake

    def list(
        self,
        userId: str = "me",
        labelIds: list[str] | None = None,
        maxResults: int | None = None,
        **_: Any,
    ) -> _Execable:
        self._fake.calls.append(("messages.list", {"labelIds": labelIds, "maxResults": maxResults}))
        ids = list(self._fake.unread_ids)
        if maxResults:
            ids = ids[:maxResults]
        if not ids:
            return _Execable(result={})  # Gmail omits "messages" when empty
        return _Execable(result={"messages": [{"id": i, "threadId": i} for i in ids]})

    def get(self, userId: str = "me", id: str = "", format: str = "full", **_: Any) -> _Execable:
        self._fake.calls.append(("messages.get", {"id": id, "format": format}))
        if id in self._fake.messages:
            return _Execable(result=self._fake.messages[id])
        return _Execable(error=make_http_error(404, "Not Found"))


class _HistoryResource:
    def __init__(self, fake: "FakeGmailResource") -> None:
        self._fake = fake

    def list(
        self,
        userId: str = "me",
        startHistoryId: str | None = None,
        historyTypes: list[str] | None = None,
        labelId: str | None = None,
        pageToken: str | None = None,
        maxResults: int | None = None,
        **_: Any,
    ) -> _Execable:
        self._fake.calls.append(
            ("history.list", {"startHistoryId": startHistoryId, "labelId": labelId})
        )
        if self._fake.history_error is not None:
            return _Execable(error=self._fake.history_error)

        start = str(startHistoryId)
        records = []
        for entry in self._fake.history:
            if str(entry["id"]) <= start:
                continue
            labels = entry.get("labels", ["INBOX", "UNREAD"])
            records.append(
                {
                    "id": str(entry["id"]),
                    "messagesAdded": [
                        {"message": {"id": mid, "threadId": mid, "labelIds": labels}}
                        for mid in entry.get("added_message_ids", [])
                    ],
                }
            )
        out: dict[str, Any] = {"historyId": str(self._fake.history_id)}
        if records:
            out["history"] = records
        return _Execable(result=out)


class _UsersResource:
    def __init__(self, fake: "FakeGmailResource") -> None:
        self._fake = fake

    def messages(self) -> _MessagesResource:
        return _MessagesResource(self._fake)

    def history(self) -> _HistoryResource:
        return _HistoryResource(self._fake)

    def getProfile(self, userId: str = "me", **_: Any) -> _Execable:
        return _Execable(
            result={
                "emailAddress": self._fake.email,
                "historyId": str(self._fake.history_id),
                "messagesTotal": self._fake.messages_total,
            }
        )


class FakeGmailResource:
    """Stand-in for ``googleapiclient.discovery.build("gmail", "v1", ...)``."""

    def __init__(
        self,
        unread_ids: list[str] | tuple[str, ...] = (),
        messages: dict[str, dict] | None = None,
        email: str = "tester@gmail.com",
        *,
        history_id: str = "1000",
        history: list[dict] | None = None,
        history_error: Exception | None = None,
        messages_total: int = 0,
    ) -> None:
        self.unread_ids = list(unread_ids)
        self.messages = messages or {}
        self.email = email
        # incremental-sync state
        self.history_id = str(history_id)
        # each entry: {"id": <int-ish>, "added_message_ids": [...], "labels": [...]}
        self.history: list[dict] = history or []
        self.history_error = history_error
        self.messages_total = messages_total
        self.calls: list[tuple[str, dict]] = []

    def users(self) -> _UsersResource:
        return _UsersResource(self)


def minimal_raw_message(msg_id: str, subject: str = "Hello", body: str = "Hi there team.") -> dict:
    """A tiny but valid raw Gmail message resource (single text/plain part)."""
    import base64

    data = base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")
    return {
        "id": msg_id,
        "threadId": msg_id,
        "labelIds": ["INBOX", "UNREAD"],
        "snippet": body[:50],
        "internalDate": "1787888662000",
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": f"Sender {msg_id} <sender.{msg_id}@example.com>"},
                {"name": "To", "value": "me@example.com"},
                {"name": "Subject", "value": subject},
                {"name": "Date", "value": "Thu, 28 Aug 2026 09:14:22 +0530"},
                {"name": "Message-ID", "value": f"<{msg_id}@example.com>"},
            ],
            "body": {"size": len(body), "data": data},
        },
    }
