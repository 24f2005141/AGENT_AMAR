"""Declarative base + shared column types.

A constraint naming convention is set so a future Alembic migration to
PostgreSQL can autogenerate stable names.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import MetaData, String
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.types import TypeDecorator

_NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=_NAMING_CONVENTION)


class TZDateTime(TypeDecorator):
    """Timezone-aware datetime stored as a UTC ISO 8601 string.

    SQLite has no real timestamptz; storing normalised UTC ISO strings keeps
    range queries (``deadline < :now``) chronologically correct and is a no-op
    to move to PostgreSQL ``timestamptz`` later.
    """

    impl = String(40)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> str | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()

    def process_result_value(self, value: str | None, dialect) -> datetime | None:
        if value is None:
            return None
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
