# AGENT AMAR — Incremental Gmail Sync (Phase 12)

Stops AGENT AMAR from re-processing the user's entire unread inbox. Monitoring
begins at a **persistent baseline** and then only **new** Gmail changes are
processed.

```
First connect  ──▶  ensure_baseline()
                    · record monitoring_started_at
                    · record the mailbox's current historyId
                    · process NOTHING (the pre-existing unread inbox is left alone)

Every cycle     ──▶  sync_new_messages()
after that          · Gmail History API: messages added since last_history_id
                    · fetch + run each through the SAME pipeline
                      (MailIntakeAgent → AMAROrchestrator → PersistenceService)
                    · THEN persist the new historyId (the resume point)
```

The resume point lives in the database (`gmail_sync_state.last_history_id`) — it
survives **restart, crash, reload and scheduler restart**. Process start time is
never used as sync state.

---

## Persistent state — `gmail_sync_state`

One row per connected account (`app/db/models.py`).

| Column | Meaning |
|---|---|
| `id` | PK |
| `user_id` | account key (`"default"` — single-user prototype), unique |
| `account_email` | connected Gmail address |
| `monitoring_started_at` | when AGENT AMAR started watching this mailbox |
| `last_sync_at` | last successful incremental sync |
| `last_history_id` | Gmail mailbox `historyId` processed up to — **the resume point** |
| `created_at` / `updated_at` | row lifecycle |

`monitoring` in the API = `last_history_id is not null`.

---

## When the baseline is established

1. **On OAuth connect** — `GET /api/v1/auth/google/callback` calls
   `GmailSyncService.ensure_baseline(...)` after storing credentials
   (best-effort — a hiccup there never fails the connect).
2. **Lazily** — the first `sync_new_messages()` call (manual or scheduled) with
   no state / no `last_history_id` baselines and returns `status: "baselined"`,
   `processed: 0`.

`ensure_baseline` is **idempotent** — once `last_history_id` is set it is never
moved by another baseline call.

---

## Incremental sync

`GmailService.list_added_message_ids_since(start_history_id, label_id="UNREAD")`:

- `users.history.list` with `historyTypes=["messageAdded"]`, paginated.
- Keeps only messages that still carry the `UNREAD` label (newly-arrived unread
  mail); de-duplicates ids; caps at `GMAIL_SYNC_MAX_MESSAGES`.
- Returns `(message_ids, latest_history_id)`.

Then `sync_new_messages` fetches each id, runs the existing pipeline, and
`PersistenceService.persist_decision(...)` writes durable state — **idempotent on
`email_id`** (`gmail_<id>`).

`last_history_id` / `last_sync_at` are committed **once, at the end of the
batch** — only after processing.

### Result `status` values

| `status` | Meaning |
|---|---|
| `baselined` | first run — baseline recorded, nothing processed |
| `synced` | processed 0..N new messages, `last_history_id` advanced |
| `history_expired_rebaselined` | `startHistoryId` too old (HTTP 404) — baseline re-set to the current `historyId`, a small gap of missed changes is accepted |
| `skipped_locked` | another sync (scheduled or manual) is already running |

---

## Deduplication & idempotency

Nothing new is invented — Phase 12 reuses the Phase 9 guarantees:

| Scenario | Why it's safe |
|---|---|
| Server crashes before `last_history_id` is committed | next run replays the same window; `persist_decision` finds the existing `email_id` and **updates** it (adds a `ProcessingRun`) — no duplicate `emails` / `actions` / `deadlines` rows |
| Same `messageAdded` history event appears twice | same as above — dedup on `email_id` |
| Scheduler cycle overlaps a manual `POST /api/v1/gmail/sync` | an in-process `threading.Lock` (non-blocking) — the second caller gets `status: "skipped_locked"` and does nothing |
| One message fails to fetch / parse | reported in `errors[]`, the batch continues, `last_history_id` still advances (a permanently-broken message is skipped, matching `/unread/process`) |
| Backend restart | `gmail_sync_state` is read from the DB; sync resumes from `last_history_id` |

---

## Background scheduler integration (Phase 11B.1)

The scheduler gained a third loop, `gmail`, alongside `deadline` and `reminder`:

```
MonitorScheduler._gmail_cycle()
  · GmailAuthService.get_credentials()  → None ⇒ log "not connected", skip (no error)
  · GmailService(credentials=...)
  · GmailSyncService(session).sync_new_messages(gmail)   ← same service the endpoint uses
```

It self-skips when Gmail is not connected, so connecting later needs no restart.
A cycle failure is caught / counted / logged like the other jobs — it never
crashes the app or stops future cycles.

`GET /api/v1/monitor/status` now also reports `gmail_sync_enabled`,
`gmail_sync_interval_seconds`, `last_gmail_sync`, `gmail_cycles`,
`gmail_failures`.

---

## Configuration (`app/core/config.py`, env-driven)

| Env var | Default | Meaning |
|---|---|---|
| `GMAIL_SYNC_ENABLED` | `true` | run the incremental-sync scheduler loop |
| `GMAIL_SYNC_INTERVAL_SECONDS` | `120` | seconds between automatic sync cycles |
| `GMAIL_SYNC_MAX_MESSAGES` | `25` | max messages processed per cycle |

The test suite forces `GMAIL_SYNC_ENABLED=false` (`tests/conftest.py`) and an
autouse fixture makes Gmail look disconnected unless a test opts in — no test
ever touches a real mailbox.

---

## API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/v1/gmail/sync` | run incremental sync now (401 if Gmail not connected) |
| `GET` | `/api/v1/gmail/sync/status` | `{ monitoring, account_email, monitoring_started_at, last_sync_at, last_history_id }` — no Gmail call |

`GET /api/v1/gmail/unread/process` is **unchanged** — still a manual, capped,
"process the current unread page" endpoint. It does not use the sync baseline.

### `POST /api/v1/gmail/sync` — sample responses

```jsonc
// first call after connecting
{ "status": "baselined", "monitoring_started_at": "2026-08-29T06:00:00Z",
  "last_history_id": "184092", "processed": 0, "new_message_ids": [], "errors": [] }

// later, with 2 new unread messages
{ "status": "synced", "from_history_id": "184092", "last_history_id": "184310",
  "last_sync_at": "2026-08-29T06:02:00Z",
  "new_message_ids": ["18f...", "18a..."], "processed": 2,
  "results": [ { "email_id": "gmail_18f...", "created": true,
                 "priority_level": "URGENT", "final_category": "INTERNSHIP" } ],
  "errors": [] }
```

---

## Manual testing

```bash
cd backend && cp .env.example .env
#  .env:  GMAIL_SYNC_ENABLED=true  GMAIL_SYNC_INTERVAL_SECONDS=15
uvicorn app.main:app --reload
```

1. Connect Gmail: open `/api/v1/auth/google/login` → consent.
2. `curl http://localhost:8000/api/v1/gmail/sync/status`
   → `"monitoring": true`, `"last_history_id": "<some id>"`, `"account_email": "you@gmail.com"`.
   **Your existing unread inbox was NOT processed** — `GET /api/v1/emails` is empty.
3. Send yourself a new email (or have one arrive).
4. Wait ~15 s (scheduler) **or** `curl -X POST http://localhost:8000/api/v1/gmail/sync`.
   Logs: `gmail sync completed: new=1 processed=1 …`.
5. `curl http://localhost:8000/api/v1/emails` → the new email, analysed.
6. `curl -X POST http://localhost:8000/api/v1/gmail/sync` again
   → `"status": "synced", "processed": 0` — no duplicate.
7. Restart the backend, `curl .../gmail/sync/status`
   → same `last_history_id` (resumes; never re-baselines).

---

## Known limitations

- **Single account** (`user_id="default"`), single backend instance.
- **History window** — Gmail keeps mailbox history for roughly a week. If the
  backend is offline longer than that, `startHistoryId` expires and the sync
  **re-baselines to now**, silently skipping the messages that arrived during
  the gap. (Run `POST /api/v1/gmail/unread/process` once to sweep recent unread
  mail after a long outage.)
- **`UNREAD`-only** — sync ingests newly-added messages that still carry the
  `UNREAD` label. A message that was added and read before the next cycle is not
  processed.
- **Advances past a poison message** — a message that always fails parsing is
  reported in `errors[]` and then skipped (its `historyId` is passed). Matches
  the existing `/unread/process` behaviour.
- **In-process lock only** — overlapping syncs within one process are
  serialised; two backend processes are not (don't run replicas with the
  scheduler enabled — see `docs/BACKGROUND_SCHEDULER.md`).
