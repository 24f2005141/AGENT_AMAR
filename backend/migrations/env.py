"""Alembic environment for the Sorted backend.

Two deliberate choices:

* The database URL comes from the application's own ``Settings`` (i.e. from
  ``DATABASE_URL`` / ``.env``), never from ``alembic.ini``. That keeps one
  source of truth and means no connection string is ever committed.
* ``target_metadata`` is the app's real ``Base.metadata``, so ``alembic
  revision --autogenerate`` diffs against the live ORM models.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Import the models package so every table is registered on Base.metadata
# before autogenerate compares.
from app.core.config import get_settings
from app.db.base import Base
from app.db import models  # noqa: F401  (side-effect import: registers tables)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    return get_settings().database_url_resolved


def run_migrations_offline() -> None:
    """Emit SQL to stdout without a live DB (``alembic upgrade head --sql``)."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # SQLite cannot ALTER most columns; batch mode rewrites the table.
        render_as_batch=_database_url().startswith("sqlite"),
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(
        section, prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=connection.dialect.name == "sqlite",
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
