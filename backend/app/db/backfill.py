"""One-off, idempotent data backfills run at startup.

These repair rows written by an earlier code version. Each is a no-op once every
row is consistent, so calling them on every ``init_db()`` is cheap.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.decision import derive_primary_category


def backfill_primary_category(session: Session) -> int:
    """Recompute the automated primary bucket from already-stored signals.

    Rows classified before the ``primary_category`` column existed keep the
    column default (``LOW_PRIORITY``). The bucket is a deterministic collapse of
    ``final_category`` / the action rows / ``priority_level`` — all persisted —
    so it can be rebuilt without re-running the agents.

    ``auto_primary_category`` (Phase 18) always follows the derivation.
    ``primary_category`` follows it too **unless** the user manually corrected
    this email (``primary_category_source == "user"``) — that correction is kept.

    Returns the number of rows changed; commits only when something changed.
    """
    from app.db.models import EmailRecord

    changed = 0
    for record in session.query(EmailRecord).all():
        want = derive_primary_category(
            final_category=record.final_category,
            action_required=bool(record.action_required),
            action_types=[a.action_type for a in record.actions],
            priority_level=record.priority_level,
        ).value
        if getattr(record, "auto_primary_category", None) != want:
            record.auto_primary_category = want
            changed += 1
        user_corrected = getattr(record, "primary_category_source", "auto") == "user"
        if not user_corrected and record.primary_category != want:
            record.primary_category = want
            changed += 1

    if changed:
        session.commit()
    return changed
