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
from app.core.logging_setup import secure_logger

logger = secure_logger(__name__)
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


# Phase 15: columns added to pre-existing tables. ``create_all`` only creates
# missing *tables*, never missing *columns*, so a dev DB from an earlier phase
# needs these added additively (SQLite ``ADD COLUMN`` is safe + non-locking).
# A full migration path is documented in docs/MIGRATION_MULTIUSER.md.
_ADDITIVE_COLUMNS: dict[str, dict[str, str]] = {
    "emails": {
        "user_pk": "INTEGER",
        # mutually-exclusive inbox bucket; legacy rows default until reprocessed
        "primary_category": "VARCHAR(24) DEFAULT 'LOW_PRIORITY'",
        # Phase 18: the automated derivation, kept even when the user overrides
        # `primary_category` (preserved for training / audit).
        "auto_primary_category": "VARCHAR(24) DEFAULT 'LOW_PRIORITY'",
        # "auto" | "user" — when "user", reprocessing does not move primary_category.
        "primary_category_source": "VARCHAR(8) DEFAULT 'auto'",
        # "auto" (derived from action statuses) | "user" (explicitly resolved).
        # When "user", a reprocess / Gmail sync never un-completes the email.
        "completion_source": "VARCHAR(8) DEFAULT 'auto'",
        # Gmail's own SPAM system label — set if an already-ingested message is
        # later moved to Spam. Excluded from every view; row never deleted.
        "is_spam": "BOOLEAN DEFAULT 0",
    },
    "audit_events": {"user_pk": "INTEGER"},
    "gmail_sync_state": {"user_pk": "INTEGER"},
    "notifications": {"pushed_at": "TEXT", "push_status": "VARCHAR(24)"},
}


def _apply_additive_migrations(engine: Engine) -> None:
    if not engine.url.get_backend_name().startswith("sqlite"):
        return
    from sqlalchemy import text

    with engine.begin() as conn:
        existing_tables = {
            row[0] for row in conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            )
        }
        for table, columns in _ADDITIVE_COLUMNS.items():
            if table not in existing_tables:
                continue
            have = {
                row[1] for row in conn.execute(text(f'PRAGMA table_info("{table}")'))
            }
            for name, ddl_type in columns.items():
                if name not in have:
                    conn.execute(text(f'ALTER TABLE "{table}" ADD COLUMN {name} {ddl_type}'))

        # Phase 15: the legacy gmail_sync_state had a NOT NULL string ``user_id``
        # that blocks new per-user rows. Rebuild it once (rows kept, user_pk NULL
        # — re-keyed by adopt_legacy_single_user / a fresh baseline).
        if "gmail_sync_state" in existing_tables:
            gss_cols = {
                row[1] for row in conn.execute(text('PRAGMA table_info("gmail_sync_state")'))
            }
            if "user_id" in gss_cols:
                conn.execute(text("""
                    CREATE TABLE gmail_sync_state_v15 (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_pk INTEGER,
                        account_email TEXT,
                        monitoring_started_at TEXT,
                        last_sync_at TEXT,
                        last_history_id VARCHAR(32),
                        created_at TEXT,
                        updated_at TEXT
                    )
                """))
                conn.execute(text("""
                    INSERT INTO gmail_sync_state_v15
                        (id, account_email, monitoring_started_at, last_sync_at,
                         last_history_id, created_at, updated_at)
                    SELECT id, account_email, monitoring_started_at, last_sync_at,
                           last_history_id, created_at, updated_at
                    FROM gmail_sync_state
                """))
                conn.execute(text("DROP TABLE gmail_sync_state"))
                conn.execute(text("ALTER TABLE gmail_sync_state_v15 RENAME TO gmail_sync_state"))
                conn.execute(text(
                    "CREATE UNIQUE INDEX ix_gmail_sync_state_user_pk "
                    "ON gmail_sync_state (user_pk)"
                ))


def init_db() -> None:
    """Prepare the schema for this process.

    Development / tests: create any missing tables directly (fast, no
    migration step in the loop) plus the additive-column repairs below.

    Production: **never** touch DDL here. The schema is owned by Alembic
    (``alembic upgrade head``, run as a deploy step / container command), so a
    rolling deploy can't have two versions racing to mutate the schema, and an
    accidental model change can't silently alter a live table. Startup instead
    verifies the database is migrated and refuses to serve if it is not.
    """
    from app.db import models  # noqa: F401 — register the mappers

    engine = get_engine()
    if get_settings().is_production:
        _verify_schema_is_migrated(engine)
    else:
        Base.metadata.create_all(engine)
        _apply_additive_migrations(engine)
    _run_startup_backfills()


def _verify_schema_is_migrated(engine) -> None:
    """Fail fast when the production database has not been migrated.

    Cheap check: Alembic's own bookkeeping table must exist and be stamped.
    Running the migrations from inside the app is deliberately NOT done — that
    is a deploy step, so it happens once, under supervision, not N times in
    parallel as replicas boot.
    """
    from sqlalchemy import inspect

    inspector = inspect(engine)
    if not inspector.has_table("alembic_version"):
        raise RuntimeError(
            "Production database has no alembic_version table — the schema has "
            "not been migrated. Run `alembic upgrade head` before starting the "
            "API (see docs/DEPLOYMENT.md)."
        )
    with engine.connect() as conn:
        stamped = conn.exec_driver_sql(
            "SELECT version_num FROM alembic_version"
        ).fetchone()
    if not stamped:
        raise RuntimeError(
            "Production database is not stamped with an Alembic revision. "
            "Run `alembic upgrade head` before starting the API."
        )
    logger.info("database schema at alembic revision %s", stamped[0])


def _run_startup_backfills() -> None:
    """Idempotent repairs for rows written by an earlier code version."""
    from app.db.backfill import backfill_primary_category

    with db_session() as session:
        backfill_primary_category(session)


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
