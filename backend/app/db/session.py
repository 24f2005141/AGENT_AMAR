"""Engine / session management.

Sync SQLAlchemy 2.x — the FastAPI app is sync (all routes are ``def``).

The engine is created lazily on first use so tests can point it at a temporary
database before anything connects.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.db.base import Base

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None
_override_url: str | None = None


def configure_for_tests(url: str) -> None:
    """Point the engine at ``url`` (call before the first DB access)."""
    global _override_url
    _override_url = url
    reset_engine()


def _url() -> str:
    if _override_url is not None:
        return _override_url
    return get_settings().database_url_resolved


def get_engine() -> Engine:
    global _engine, _SessionLocal
    if _engine is None:
        url = _url()
        kwargs: dict = {"echo": get_settings().database_echo, "future": True}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
        _engine = create_engine(url, **kwargs)
        _SessionLocal = sessionmaker(
            bind=_engine, autoflush=False, expire_on_commit=False, future=True
        )
    return _engine


def _session_factory() -> sessionmaker[Session]:
    get_engine()
    assert _SessionLocal is not None
    return _SessionLocal


def reset_engine() -> None:
    """Dispose the engine (tests, or a config change)."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None


def init_db() -> None:
    """Create any missing tables. Idempotent; safe to call at startup."""
    from app.db import models  # noqa: F401 — register the mappers

    Base.metadata.create_all(get_engine())


@contextmanager
def db_session() -> Iterator[Session]:
    """Context-managed session for scripts / the pipeline."""
    session = _session_factory()()
    try:
        yield session
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency. The caller (endpoint / service) commits explicitly."""
    session = _session_factory()()
    try:
        yield session
    finally:
        session.close()
