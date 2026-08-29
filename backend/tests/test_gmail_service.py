"""Tests for GmailService using a mocked Gmail API resource (STEP 8.4-8.6)."""

from __future__ import annotations

import pytest

from app.core.errors import GmailApiError, GmailNotConnectedError, MessageNotFoundError
from app.services.gmail_service import GmailFetchNotConfigured, GmailService
from tests.fakes import FakeGmailResource, make_http_error, minimal_raw_message


def test_unauthenticated_service_raises():
    with pytest.raises(GmailFetchNotConfigured):
        GmailService().list_unread_message_ids()
    with pytest.raises(GmailNotConnectedError):  # base class also matches
        GmailService().get_message("abc")


def test_list_unread_message_ids():
    fake = FakeGmailResource(unread_ids=["m1", "m2", "m3"])
    svc = GmailService(service=fake)
    assert svc.list_unread_message_ids() == ["m1", "m2", "m3"]


def test_list_unread_empty_inbox_returns_empty_list():
    svc = GmailService(service=FakeGmailResource(unread_ids=[]))
    assert svc.list_unread_message_ids() == []


def test_list_unread_respects_and_clamps_max_results():
    fake = FakeGmailResource(unread_ids=[f"m{i}" for i in range(50)])
    svc = GmailService(service=fake)

    assert svc.list_unread_message_ids(max_results=5) == [f"m{i}" for i in range(5)]
    svc.list_unread_message_ids(max_results=9999)
    assert fake.calls[-1][1]["maxResults"] == 100  # capped
    svc.list_unread_message_ids(max_results=0)
    assert fake.calls[-1][1]["maxResults"] == 1  # floored


def test_get_message_returns_raw_payload_unmodified():
    raw = minimal_raw_message("m1", subject="Raw subject")
    svc = GmailService(service=FakeGmailResource(messages={"m1": raw}))
    assert svc.get_message("m1") is raw


def test_get_message_unknown_id_raises_not_found():
    svc = GmailService(service=FakeGmailResource(messages={}))
    with pytest.raises(MessageNotFoundError):
        svc.get_message("does-not-exist")


def test_get_message_empty_id_raises_not_found():
    svc = GmailService(service=FakeGmailResource())
    with pytest.raises(MessageNotFoundError):
        svc.get_message("")


def test_api_500_maps_to_gmail_api_error(monkeypatch):
    fake = FakeGmailResource(unread_ids=["m1"])

    class Boom:
        def messages(self):
            raise make_http_error(500, "backend error")

    fake.users = lambda: Boom()  # type: ignore[assignment]
    svc = GmailService(service=fake)
    with pytest.raises(GmailApiError):
        svc.list_unread_message_ids()


def test_api_403_maps_to_not_connected():
    fake = FakeGmailResource(unread_ids=["m1"])

    class Denied:
        def messages(self):
            raise make_http_error(403, "insufficient permissions")

    fake.users = lambda: Denied()  # type: ignore[assignment]
    svc = GmailService(service=fake)
    with pytest.raises(GmailNotConnectedError):
        svc.list_unread_message_ids()


def test_get_profile_email():
    svc = GmailService(service=FakeGmailResource(email="me@gmail.com"))
    assert svc.get_profile_email() == "me@gmail.com"
