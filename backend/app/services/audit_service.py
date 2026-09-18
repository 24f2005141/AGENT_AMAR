"""Tamper-evident audit chain (Phase 14).

A lightweight, blockchain-*inspired* append-only ledger. **No** Ethereum /
Hyperledger / mining / wallets / smart contracts / consensus — just a hash
chain in the same SQLite/SQLAlchemy DB the rest of the app uses.

    record_hash = SHA-256( canonical_json(non_sensitive_metadata) + previous_hash )

Each record links to the previous via ``previous_hash``; the genesis record
uses ``GENESIS_HASH`` ("0" * 64). :func:`AuditService.verify_chain` recomputes
every hash and checks every link + the ``sequence`` for gaps.

Audit records contain **only non-sensitive metadata** — event type, resource
type, an opaque resource id, a timestamp, and optionally tiny non-sensitive
counters. Never email content, OTPs, addresses, tokens or keys.

Writes go through :func:`audit_record`, which appends in its **own** short
transaction (serialised by a process lock) so the chain stays consistent
regardless of the caller's transaction. A failure to record is logged, never
raised into the business flow.
"""

from __future__ import annotations

import hashlib
import json
import threading
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.logging_setup import secure_logger
from app.core.sanitization import redact_mapping
from app.db.base import utcnow
from app.db.models import GENESIS_HASH, AuditEvent
from app.db.session import db_session
from app.repositories.audit_repository import AuditRepository

logger = secure_logger("agent_amar.audit")

_CHAIN_LOCK = threading.Lock()


def _canonical(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _ts_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def compute_hash(
    *,
    audit_id: str,
    sequence: int,
    timestamp: str,
    event_type: str,
    resource_type: str,
    resource_id: str | None,
    detail: dict,
    previous_hash: str,
    user_pk: int | None = None,
) -> str:
    payload = {
        "audit_id": audit_id,
        "sequence": sequence,
        "timestamp": timestamp,
        "event_type": event_type,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "user_pk": user_pk,
        "detail": detail,
    }
    return hashlib.sha256((_canonical(payload) + previous_hash).encode("utf-8")).hexdigest()


def audit_record(
    event_type: str,
    resource_type: str,
    *,
    resource_id: str | None = None,
    detail: dict | None = None,
    timestamp: datetime | None = None,
    user_pk: int | None = None,
) -> str | None:
    """Append one link to the audit chain. Returns the ``audit_id`` or ``None``
    if recording failed (which is logged, never raised).

    ``detail`` must be non-sensitive metadata only; it is defensively sanitised.
    """
    ids = audit_record_many(
        [{
            "event_type": event_type,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "detail": detail,
            "user_pk": user_pk,
        }],
        timestamp=timestamp,
    )
    return ids[0] if ids else None


def audit_record_many(
    events: list[dict],
    *,
    timestamp: datetime | None = None,
) -> list[str]:
    """Append several links in ONE short transaction (one lock acquire, one
    commit). Returns the ``audit_id``s, or ``[]`` on failure (logged, not raised).
    """
    if not events:
        return []
    ts = timestamp or utcnow()
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    ts_iso = _ts_iso(ts)
    try:
        with _CHAIN_LOCK, db_session() as session:
            repo = AuditRepository(session)
            prev = repo.last()
            previous_hash = prev.record_hash if prev else GENESIS_HASH
            sequence = (prev.sequence + 1) if prev else 1
            out: list[str] = []
            for ev in events:
                safe_detail = redact_mapping(ev.get("detail") or {})
                resource_id = ev.get("resource_id")
                user_pk = ev.get("user_pk")
                audit_id = str(uuid.uuid4())
                record_hash = compute_hash(
                    audit_id=audit_id,
                    sequence=sequence,
                    timestamp=ts_iso,
                    event_type=ev["event_type"],
                    resource_type=ev["resource_type"],
                    resource_id=resource_id,
                    detail=safe_detail,
                    previous_hash=previous_hash,
                    user_pk=user_pk,
                )
                session.add(
                    AuditEvent(
                        audit_id=audit_id,
                        sequence=sequence,
                        timestamp=ts,
                        event_type=ev["event_type"],
                        resource_type=ev["resource_type"],
                        resource_id=(resource_id or None),
                        user_pk=user_pk,
                        detail=safe_detail,
                        previous_hash=previous_hash,
                        record_hash=record_hash,
                    )
                )
                out.append(audit_id)
                previous_hash = record_hash
                sequence += 1
            session.commit()
            return out
    except Exception:  # noqa: BLE001 — auditing must never break the business flow
        types = ",".join(sorted({e.get("event_type", "?") for e in events}))
        logger.exception("failed to append audit events [%s]", types)
        return []


class AuditService:
    """Read-side helper: chain verification + listing."""

    def __init__(self, session: Session) -> None:
        self.repo = AuditRepository(session)

    def verify_chain(self, *, limit: int | None = None) -> dict:
        """Recompute every hash and check every link.

        Returns ``{"valid", "records_checked", "first_invalid_record", "reason"}``.
        Never returns sensitive data.
        """
        checked = 0
        expected_prev = GENESIS_HASH
        expected_seq = 1

        for event in self.repo.iter_chain():
            if limit is not None and checked >= limit:
                break
            checked += 1

            if event.sequence != expected_seq:
                return _fail(checked, event.audit_id,
                             f"sequence gap: expected {expected_seq}, got {event.sequence}")
            if event.previous_hash != expected_prev:
                return _fail(checked, event.audit_id,
                             "previous_hash does not match the prior record")

            recomputed = compute_hash(
                audit_id=event.audit_id,
                sequence=event.sequence,
                timestamp=_ts_iso(event.timestamp),
                event_type=event.event_type,
                resource_type=event.resource_type,
                resource_id=event.resource_id,
                detail=event.detail or {},
                previous_hash=event.previous_hash,
                user_pk=event.user_pk,
            )
            if recomputed != event.record_hash:
                return _fail(checked, event.audit_id,
                             "record_hash mismatch (record was modified)")

            expected_prev = event.record_hash
            expected_seq += 1

        return {
            "valid": True,
            "records_checked": checked,
            "first_invalid_record": None,
            "reason": None,
        }

    def recent(
        self, *, limit: int = 100, offset: int = 0, user_pk: int | None = None
    ) -> list[AuditEvent]:
        return self.repo.list_recent(limit=limit, offset=offset, user_pk=user_pk)

    def count(self, *, user_pk: int | None = None) -> int:
        return self.repo.count_for_user(user_pk) if user_pk is not None else self.repo.count()


def _fail(checked: int, audit_id: str | None, reason: str) -> dict:
    return {
        "valid": False,
        "records_checked": checked,
        "first_invalid_record": audit_id,
        "reason": reason,
    }
