"""Tests for the token storage abstraction (STEP 8.3)."""

from __future__ import annotations

from app.services.token_store import (
    DEFAULT_ACCOUNT,
    FileTokenStore,
    InMemoryTokenStore,
    TokenStore,
)

import pytest

BLOB = {"token": "a", "refresh_token": "r", "account_email": "x@gmail.com"}


@pytest.fixture(params=["file", "memory"])
def store(request, tmp_path) -> TokenStore:
    if request.param == "file":
        return FileTokenStore(tmp_path / "tokens")
    return InMemoryTokenStore()


def test_empty_store(store: TokenStore):
    assert store.get() is None
    assert store.exists() is False
    assert store.list_accounts() == []


def test_put_get_roundtrip(store: TokenStore):
    store.put(BLOB)
    assert store.get() == BLOB
    assert store.exists() is True
    assert store.list_accounts() == [DEFAULT_ACCOUNT]


def test_put_is_replace(store: TokenStore):
    store.put(BLOB)
    store.put({"token": "b", "refresh_token": "r2"})
    assert store.get()["token"] == "b"


def test_delete(store: TokenStore):
    store.put(BLOB)
    store.delete()
    assert store.get() is None
    store.delete()  # idempotent


def test_multiple_accounts(store: TokenStore):
    store.put(BLOB, account_id="alice@gmail.com")
    store.put(BLOB, account_id="bob@gmail.com")
    assert set(store.list_accounts()) == {"alice@gmail.com", "bob@gmail.com"}
    assert store.get(account_id="alice@gmail.com") == BLOB
    assert store.get(account_id="carol@gmail.com") is None


def test_get_returns_copy(store: TokenStore):
    store.put(BLOB)
    fetched = store.get()
    fetched["token"] = "mutated"
    assert store.get()["token"] == "a"


def test_file_store_survives_new_instance(tmp_path):
    path = tmp_path / "tokens"
    FileTokenStore(path).put(BLOB)
    assert FileTokenStore(path).get() == BLOB


def test_file_store_tolerates_corrupt_file(tmp_path):
    path = tmp_path / "tokens"
    store = FileTokenStore(path)
    store.put(BLOB)
    (path / f"{DEFAULT_ACCOUNT}.json").write_text("{not json", encoding="utf-8")
    assert store.get() is None
