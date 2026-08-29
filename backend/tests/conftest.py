"""Shared test fixtures.

The database is pointed at a temporary SQLite file for the whole test session
(never the dev ``agent_amar.db``), and every table is truncated before each
test for isolation.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

# The background scheduler must never run during the test suite — set this
# before anything imports app.core.config / app.main (pytest loads conftest
# first). Scheduler-specific tests build their own MonitorScheduler with an
# explicit Settings(scheduler_enabled=True).
os.environ["SCHEDULER_ENABLED"] = "false"
# ...and the Gmail-sync scheduler job must never touch a real mailbox during
# tests (a dev may have real credentials in .tokens/). Gmail-sync tests inject
# a FakeGmailResource or build Settings(gmail_sync_enabled=True) explicitly.
os.environ["GMAIL_SYNC_ENABLED"] = "false"

# Never let a real LLM be selected from a developer's .env during tests — every
# LLM test injects a fake / builds Settings(...) explicitly. This keeps the
# suite offline and deterministic regardless of LLM_PROVIDER in .env.
os.environ["LLM_PROVIDER"] = "none"
os.environ["LLM_API_KEY"] = ""

import pytest
from sqlalchemy import text

from app.db import base as db_base
from app.db import session as db_session

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session", autouse=True)
def _test_database(tmp_path_factory):
    """Point the engine at a throwaway SQLite file and create the schema once."""
    db_path = tmp_path_factory.mktemp("db") / "test_agent_amar.db"
    db_session.configure_for_tests(f"sqlite:///{db_path.as_posix()}")
    db_session.init_db()
    yield
    db_session.reset_engine()


@pytest.fixture(autouse=True)
def _clean_tables():
    """Wipe every table before each test."""
    engine = db_session.get_engine()
    with engine.begin() as conn:
        for table in reversed(db_base.Base.metadata.sorted_tables):
            conn.execute(table.delete())
    yield


@pytest.fixture
def db():
    """A plain session for tests that talk to the DB directly."""
    with db_session.db_session() as s:
        yield s


@pytest.fixture(autouse=True)
def _gmail_offline_by_default():
    """Force Gmail to look *disconnected* unless a test opts in.

    A developer may have real credentials in ``backend/.tokens/``; without this
    guard, any endpoint using ``get_gmail_service`` (now including
    ``POST /api/v1/gmail/sync``) could make a real API call. Tests that need a
    connected Gmail override ``get_gmail_service`` (or ``get_auth_service``)
    themselves — those overrides run after this fixture and win.
    """
    from app.api.deps import get_auth_service, get_gmail_service
    from app.core.config import Settings
    from app.main import app
    from app.services.gmail_auth_service import GmailAuthService
    from app.services.token_store import InMemoryTokenStore

    keys = (get_auth_service, get_gmail_service)
    saved = {k: app.dependency_overrides.get(k) for k in keys}
    app.dependency_overrides[get_auth_service] = lambda: GmailAuthService(
        Settings(), InMemoryTokenStore()
    )
    yield
    for k, v in saved.items():
        if v is None:
            app.dependency_overrides.pop(k, None)
        else:
            app.dependency_overrides[k] = v


@pytest.fixture
def sample_gmail_message() -> dict:
    """The raw Gmail API message resource used across intake tests."""
    return json.loads((FIXTURES / "sample_gmail_message.json").read_text("utf-8"))
