"""Phase 15 — one-shot migration from the single-user prototype to multi-user.

Two supported paths (see ``docs/MIGRATION_MULTIUSER.md``):

* **reset** — delete ``agent_amar.db`` + ``.tokens/`` and reconnect. Nothing to run.
* **adopt** — keep the existing data: :func:`adopt_legacy_single_user` creates one
  ``User`` from the legacy ``default`` Gmail account and assigns every unowned row
  (and the legacy ``gmail_sync_state``) to it.

Run:

    python -c "from app.db.session import configure_for_tests" 2>/dev/null; \
    python - <<'PY'
    from app.core.config import get_settings
    from app.db.session import db_session, init_db
    from app.db.migrations_multiuser import adopt_legacy_single_user
    init_db()
    with db_session() as s:
        print(adopt_legacy_single_user(s, get_settings().token_storage_dir))
    PY
"""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.models import User


def adopt_legacy_single_user(session: Session, token_dir: str | Path) -> dict:
    """Idempotent. Returns a summary of what was migrated."""
    token_dir = Path(token_dir)
    legacy_token = token_dir / "default.json"

    account_email = None
    blob = None
    if legacy_token.is_file():
        try:
            blob = json.loads(legacy_token.read_text("utf-8"))
            account_email = blob.get("account_email")
        except (json.JSONDecodeError, OSError):
            blob = None

    sub = f"legacy:{account_email}" if account_email else "legacy:default"
    user = session.query(User).filter(User.google_sub == sub).one_or_none()
    if user is None:
        user = User(google_sub=sub, google_email=account_email or "")
        session.add(user)
        session.flush()

    # 1. adopt every unowned email (children cascade via email_pk)
    adopted = session.execute(
        text("UPDATE emails SET user_pk = :uid WHERE user_pk IS NULL"),
        {"uid": user.id},
    ).rowcount

    # 2. re-key the legacy gmail_sync_state row (string user_id -> user_pk)
    synced = 0
    cols = {r[1] for r in session.execute(text('PRAGMA table_info("gmail_sync_state")'))}
    if "user_pk" in cols:
        params = {"uid": user.id}
        where = "user_pk IS NULL"
        if "user_id" in cols:
            where = "(user_pk IS NULL OR user_id = 'default')"
        synced = session.execute(
            text(f"UPDATE gmail_sync_state SET user_pk = :uid WHERE {where}"), params
        ).rowcount

    # 3. move the token blob into the encrypted per-user credential row
    creds_moved = False
    if blob is not None:
        from app.services.token_store import DbTokenStore

        DbTokenStore(session).put(blob, account_id=str(user.id))
        creds_moved = True

    session.commit()
    return {
        "user_id": user.id,
        "google_sub": sub,
        "account_email": account_email,
        "emails_adopted": adopted,
        "sync_state_rekeyed": synced,
        "gmail_credentials_moved": creds_moved,
    }
