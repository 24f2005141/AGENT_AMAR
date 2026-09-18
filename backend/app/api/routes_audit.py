"""Tamper-evident audit chain — read-only inspection (Phase 14).

    GET /api/v1/audit/verify   recompute the hash chain, report integrity
    GET /api/v1/audit/events   the audit metadata ledger (newest first)

Non-sensitive metadata only — no email content, OTPs, addresses, tokens or keys.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.db.models import User
from app.services.audit_service import AuditService

router = APIRouter(prefix="/api/v1/audit", tags=["audit"])


@router.get("/verify")
def verify_audit_chain(
    limit: int | None = Query(default=None, ge=1, le=100000,
                              description="check only the first N records"),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> dict:
    """`{valid, records_checked, first_invalid_record, reason}` — whole-chain
    integrity check (not user-scoped; any authenticated user may run it)."""
    return AuditService(db).verify_chain(limit=limit)


@router.get("/events")
def list_audit_events(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """The current user's audit ledger, newest first. Metadata only."""
    svc = AuditService(db)
    events = svc.recent(limit=limit, offset=offset, user_pk=user.id)
    return {
        "total": svc.count(user_pk=user.id),
        "events": [
            {
                "audit_id": e.audit_id,
                "sequence": e.sequence,
                "timestamp": e.timestamp.isoformat() if e.timestamp else None,
                "event_type": e.event_type,
                "resource_type": e.resource_type,
                "resource_id": e.resource_id,
                "detail": e.detail or {},
                "previous_hash": e.previous_hash,
                "record_hash": e.record_hash,
            }
            for e in events
        ],
    }
