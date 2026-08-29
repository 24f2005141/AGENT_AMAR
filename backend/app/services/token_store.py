"""Token storage abstraction.

The OAuth flow produces a credential blob per connected Gmail account. Where
that blob lives is deliberately hidden behind :class:`TokenStore` so the
file-based development implementation can be swapped for a database-backed one
later without touching the auth service or the routes.

A "credential blob" here is a plain ``dict`` (the JSON produced by
``google.oauth2.credentials.Credentials.to_json()`` plus an ``account_email``
key). This module never interprets it.
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

#: Account key used while the system is single-user (development).
DEFAULT_ACCOUNT = "default"

_SAFE_KEY_RE = re.compile(r"[^A-Za-z0-9._@-]+")


def _safe_key(account_id: str) -> str:
    """Make an account id safe to use as a filename."""
    cleaned = _SAFE_KEY_RE.sub("_", account_id.strip()) or DEFAULT_ACCOUNT
    return cleaned[:200]


class TokenStore(ABC):
    """Persist / retrieve OAuth credential blobs keyed by account id."""

    @abstractmethod
    def get(self, account_id: str = DEFAULT_ACCOUNT) -> dict[str, Any] | None:
        """Return the stored blob for ``account_id`` or ``None``."""

    @abstractmethod
    def put(self, data: dict[str, Any], account_id: str = DEFAULT_ACCOUNT) -> None:
        """Create or replace the blob for ``account_id``."""

    @abstractmethod
    def delete(self, account_id: str = DEFAULT_ACCOUNT) -> None:
        """Remove the blob for ``account_id`` (no error if absent)."""

    @abstractmethod
    def list_accounts(self) -> list[str]:
        """Return every account id that currently has a stored blob."""

    def exists(self, account_id: str = DEFAULT_ACCOUNT) -> bool:
        """Convenience: whether a blob is stored for ``account_id``."""
        return self.get(account_id) is not None


class InMemoryTokenStore(TokenStore):
    """Non-persistent store. Handy for tests."""

    def __init__(self) -> None:
        self._data: dict[str, dict[str, Any]] = {}

    def get(self, account_id: str = DEFAULT_ACCOUNT) -> dict[str, Any] | None:
        blob = self._data.get(account_id)
        return json.loads(json.dumps(blob)) if blob is not None else None

    def put(self, data: dict[str, Any], account_id: str = DEFAULT_ACCOUNT) -> None:
        self._data[account_id] = json.loads(json.dumps(data))

    def delete(self, account_id: str = DEFAULT_ACCOUNT) -> None:
        self._data.pop(account_id, None)

    def list_accounts(self) -> list[str]:
        return sorted(self._data)


class FileTokenStore(TokenStore):
    """One JSON file per account under ``directory`` (development default).

    Not encrypted. The directory must be excluded from version control
    (see ``backend/.gitignore``).
    """

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)

    def _path(self, account_id: str) -> Path:
        return self.directory / f"{_safe_key(account_id)}.json"

    def get(self, account_id: str = DEFAULT_ACCOUNT) -> dict[str, Any] | None:
        path = self._path(account_id)
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text("utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def put(self, data: dict[str, Any], account_id: str = DEFAULT_ACCOUNT) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self._path(account_id)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(path)

    def delete(self, account_id: str = DEFAULT_ACCOUNT) -> None:
        self._path(account_id).unlink(missing_ok=True)

    def list_accounts(self) -> list[str]:
        if not self.directory.is_dir():
            return []
        return sorted(p.stem for p in self.directory.glob("*.json"))
