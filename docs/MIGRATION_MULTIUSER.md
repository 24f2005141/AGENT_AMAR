# Migration — single-user prototype → multi-user (Phase 15)

Phase 15 introduces an application `User` and scopes every email / action /
deadline / reminder / notification / Gmail credential / sync-state row to a
`user_id`. Data written before Phase 15 has **no owner** (`emails.user_pk IS
NULL`) and is therefore invisible to any signed-in user until migrated. It is
**never deleted or corrupted** — you choose how to proceed.

`init_db()` (startup) additively adds the new columns (`emails.user_pk`,
`audit_events.user_pk`, `gmail_sync_state.user_pk`) and the new tables
(`users`, `app_sessions`, `oauth_credentials`) — it does not rewrite existing
rows.

## Option A — Reset (recommended for development)

```bash
cd backend
rm -f agent_amar.db
rm -rf .tokens/
```

Restart the server and sign in again with "Continue with Google". The historical
inbox is **not** re-ingested (Phase 12 baseline behaviour is per-user).

## Option B — Adopt the existing data

Keeps every processed email and re-assigns it to a single new user built from the
legacy `.tokens/default.json` account:

```bash
cd backend
python - <<'PY'
from app.core.config import get_settings
from app.db.session import db_session, init_db
from app.db.migrations_multiuser import adopt_legacy_single_user
init_db()
with db_session() as s:
    print(adopt_legacy_single_user(s, get_settings().token_storage_dir))
PY
```

`adopt_legacy_single_user` is **idempotent**. It:

1. creates `User(google_sub="legacy:<email>")` from the legacy token's
   `account_email`;
2. `UPDATE emails SET user_pk = <id> WHERE user_pk IS NULL` (children cascade);
3. re-keys the legacy `gmail_sync_state` row to `user_pk`;
4. moves the token blob into the encrypted `oauth_credentials` row for that user.

The first time that user signs in with Google, their real `sub` replaces the
`legacy:` placeholder (matched by email) and the same row is reused.

## Notes

* Old `gmail_sync_state.user_id` (string `"default"`) column is left in place by
  SQLite; it is unused after the re-key.
* Audit-chain rows written before Phase 15 have `user_pk = NULL`; they stay in the
  chain and still verify. `GET /api/v1/audit/events` only shows the caller's own
  rows, so pre-migration events are visible only after adoption.
