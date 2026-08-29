# AGENT AMAR — Background Scheduler (Phase 11B.1)

Makes deadline monitoring, escalation and user-reminder firing happen
**automatically** — previously they only ran when `POST /api/v1/monitor/deadlines/check`
was called by hand.

The scheduler adds **no business logic**. It is a timer that calls the same
`DeadlineMonitorService` the manual endpoint uses.

```
FastAPI startup ─▶ scheduler.start()
                        │
        ┌───────────────┴────────────────┐
        ▼                                ▼
 deadline loop (every N s)        reminder loop (every M s)
        │                                │
 DeadlineMonitorService            DeadlineMonitorService
 .run_deadline_check(              .run_reminder_check()
   include_reminders=False)              │
        │                                │
   auto-start monitoring            find due reminders
   evaluate each deadline           create user_reminder notification
   escalation rung (policy)         reminder → TRIGGERED
        │                                │
        └──────────► notifications table ◄┘  (status = PENDING)
                            │
                    Flutter GET /api/v1/notifications
FastAPI shutdown ─▶ scheduler.stop()  (cancel + await tasks)
```

---

## Architecture decision

**Chosen:** a minimal in-process `asyncio` scheduler (`app/services/scheduler.py`),
one background task per job.

**Why:**

| Requirement | How this meets it |
|---|---|
| Works with FastAPI, starts/stops with the app | started in the `lifespan` context manager, cancelled + awaited on shutdown |
| Doesn't block API requests | the sync monitor runs in a worker thread via `asyncio.to_thread`; the event loop only sleeps |
| Easy to test | job functions (`_deadline_cycle`, `_reminder_cycle`) are plain sync methods — tests call them directly, no waiting |
| No external infra | pure stdlib `asyncio` — **no Celery / Redis / RabbitMQ / APScheduler** |
| Survives job failure | each cycle is wrapped in `_run_safely`: the exception is counted + logged, the loop continues |
| Scheduler failure ≠ API crash | the loops are independent tasks; an unhandled error in one is caught, and even a task dying does not affect request handling |

**Rejected:** APScheduler (extra dependency for two fixed-interval jobs), Celery/
Redis (enterprise infra, explicitly out of scope), OS cron (not portable, not
"starts with the app").

### Known limitation — single instance only

This is a **single-process prototype**. Each backend process runs its own
scheduler. If you run more than one replica **with the scheduler enabled**, both
tick, and in the same wall-clock second both could try to create the same
notification. The database-state deduplication (see below) makes this *mostly*
harmless — the loser's insert is a duplicate row at worst, and escalation
dedup is level-keyed — but it is **not** a distributed lock. Before scaling
horizontally: run the scheduler on exactly one instance
(`SCHEDULER_ENABLED=false` on the others), or move to an external scheduler /
leader-election.

---

## Jobs

| Job | Calls | What it does |
|---|---|---|
| **deadline** | `DeadlineMonitorService.run_deadline_check(include_reminders=False)` | auto-starts monitoring for `should_monitor` deadlines, evaluates every monitored deadline, emits `deadline_escalation` / `deadline_passed` / `ambiguous_deadline` notifications per the Phase 10 escalation policy |
| **reminder** | `DeadlineMonitorService.run_reminder_check()` | finds `PENDING` reminders with `reminder_at <= now`, creates a `user_reminder` notification, sets the reminder `TRIGGERED` (or `SKIPPED` if the email/action is already done) |

> The manual `POST /api/v1/monitor/deadlines/check` still runs **both** halves in
> one call (`include_reminders=True`, the default) — its behaviour is unchanged.
> `run_reminder_check` shares the exact reminder code path
> (`_process_due_reminders`), so there is no second reminder implementation.

Escalation thresholds live **only** in `app/services/escalation_policy.py`
(`LADDERS`) — the scheduler never sees a number.

---

## Environment configuration

`backend/app/core/config.py` (dependency-free `Settings`, env-driven):

| Env var | Default | Meaning |
|---|---|---|
| `SCHEDULER_ENABLED` | `true` | master switch. `false` ⇒ scheduler never starts; use the manual endpoint. |
| `DEADLINE_CHECK_INTERVAL_SECONDS` | `60` | seconds between deadline-monitoring cycles |
| `REMINDER_CHECK_INTERVAL_SECONDS` | `60` | seconds between reminder cycles |

Change them in `.env` (or the process environment) — **no code change**. Minimum
enforced interval is 1 second. First cycle runs ~0.5 s after startup, then every
interval.

`.env.example`:
```
SCHEDULER_ENABLED=true
DEADLINE_CHECK_INTERVAL_SECONDS=60
REMINDER_CHECK_INTERVAL_SECONDS=60
```

The test suite forces `SCHEDULER_ENABLED=false` (`tests/conftest.py`) so no
background loop runs during tests; scheduler tests build their own
`MonitorScheduler(Settings(scheduler_enabled=True, ...))`.

---

## Application lifecycle

`backend/app/main.py`:

```python
@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    scheduler = get_scheduler()      # module singleton
    scheduler.start()                # non-blocking; no-op if disabled/already running
    try:
        yield
    finally:
        await scheduler.stop()       # cancel tasks, await them
```

- `get_scheduler()` returns a process-wide singleton → **no duplicate scheduler
  instances**, even if `lifespan` somehow ran twice.
- `start()` is idempotent and returns immediately (creates tasks, doesn't await).
- `stop()` cancels both loop tasks and awaits them → **no orphaned tasks**.
- A `CancelledError` during a `sleep` exits the loop cleanly; a cycle already
  running in a thread finishes (threads aren't cancellable) and its result is
  simply not recorded.

---

## Deduplication / idempotency

The scheduler relies **entirely** on the Phase 9/10 persisted-state dedup — it
adds nothing of its own. Running every minute is safe because:

| Concern | Guard (already in the DB / services) |
|---|---|
| Re-escalating the same deadline | `NotificationRepository.highest_escalation_for(deadline_pk)` — a new `deadline_escalation` row is created **only** when the target rung out-ranks the highest already issued (`PENDING`/`SENT`). Same rung on the next cycle ⇒ `NO_CHANGE`, nothing created. |
| "Deadline passed" spam | `exists_for_deadline(deadline_pk, "deadline_passed", …)` — one only. |
| "Ambiguous deadline" spam | `exists_for_deadline(deadline_pk, "ambiguous_deadline", …)` — one only. |
| Quiet-hours retries | one `SKIPPED` row per `(deadline, rung)`, not re-created each cycle. |
| Re-firing a reminder | `list_due` filters `status == "PENDING"`; a fired reminder is `TRIGGERED`, so the next cycle skips it. |
| Firing a cancelled reminder | `status == "CANCELLED"` ⇒ not in `list_due`. |
| Initial "important email" alert | created once at processing time (`new_priority_email`), guarded by `exists_for`. The scheduler never creates it. |
| Restart mid-escalation | escalation state is **derived** from the `notifications` table, not held in memory. A fresh process reads the same rows and reaches the same `NO_CHANGE`. |
| Monitoring flags | `deadlines.is_monitoring` / `monitoring_stopped_at` are persisted; a restart does not re-open a stopped monitor. |

**The database is the single source of truth.** The scheduler holds only
observability counters (cycle counts, last-run timestamps) which reset on
restart and affect nothing.

Escalation progression across cycles:

```
cycle @ T-13h   deadline 13h out (URGENT priority)   → REMINDER   → 1 notification
cycle @ T-12h   still REMINDER rung                  → NO_CHANGE  → 0
cycle @ T-2h    crosses the URGENT rung              → URGENT     → 1 notification
cycle @ T-90m   still URGENT rung                    → NO_CHANGE  → 0
cycle @ T-10m   crosses the ALARM rung (unviewed)    → ALARM      → 1 notification (requires_alarm)
cycle @ T-5m    still ALARM                          → NO_CHANGE  → 0
cycle @ T+1m    past                                 → DEADLINE_PASSED → 1 notification, then stop after grace
```

---

## Failure handling

| Failure | Behaviour |
|---|---|
| One cycle raises (DB locked, bug, bad row) | `_run_safely` catches it, increments `failures[job]`, sets `last_error`, logs `scheduler job failed: <job> (will retry next tick)` with a stack trace. The loop sleeps and runs again next interval. |
| The deadline job keeps failing | the reminder job is unaffected (separate task). API requests unaffected. |
| A loop task dies unexpectedly | caught on shutdown; does not propagate. (Cycles can't normally kill the task because every cycle body is inside `_run_safely`.) |
| Backend restart | in-flight cycle is lost; nothing is half-written (each `run_*` commits once at the end); next startup resumes from DB state. |

---

## Observability (logging)

Logger `agent_amar.scheduler`, all `INFO` except failures (`ERROR` + traceback):

```
scheduler started (deadline_interval=60s reminder_interval=60s)
deadline monitoring cycle started
deadline monitoring cycle completed: deadlines_evaluated=4 notifications_created=1
reminder check started
reminder check completed: reminders_due=1 notifications_created=1
scheduler job failed: deadline (will retry next tick)     ← + stack trace
scheduler stopped
```

**No email content is logged** — only counts, ids and timestamps. (The monitor
service itself logs nothing about content either.)

---

## Status endpoint

`GET /api/v1/monitor/status` (new; `/health` is untouched):

```json
{
  "scheduler": "running",
  "enabled": true,
  "started_at": "2026-08-29T06:27:31Z",
  "deadline_check_interval_seconds": 60,
  "reminder_check_interval_seconds": 60,
  "last_deadline_check": "2026-08-29T06:28:31Z",
  "last_reminder_check": "2026-08-29T06:28:31Z",
  "deadline_cycles": 12,
  "reminder_cycles": 12,
  "deadline_failures": 0,
  "reminder_failures": 0,
  "last_error": null
}
```
`scheduler` is `"running"` / `"stopped"`. `last_*_check` are ISO-8601 UTC or
`null` before the first cycle. Counters reset on restart.

---

## Manual testing

### A. Watch it run

```bash
cd backend
cp .env.example .env
#  in .env:
#    SCHEDULER_ENABLED=true
#    DEADLINE_CHECK_INTERVAL_SECONDS=10
#    REMINDER_CHECK_INTERVAL_SECONDS=10
uvicorn app.main:app --reload
```
Logs on startup:
```
agent_amar.scheduler  scheduler started (deadline_interval=10s reminder_interval=10s)
agent_amar.scheduler  deadline monitoring cycle started
agent_amar.scheduler  deadline monitoring cycle completed: deadlines_evaluated=0 notifications_created=0
```
…repeating every 10 s.

### B. End-to-end (Gmail connected)

```bash
# 1. ingest an email that has a near deadline
curl "http://localhost:8000/api/v1/gmail/unread/process?max_results=10"

# 2. schedule a reminder ~1 minute out
curl -X POST "http://localhost:8000/api/v1/emails/<email_id>/reminders" \
     -H 'content-type: application/json' \
     -d '{"reminder_at":"<now + 60s, ISO 8601>"}'

# 3. do nothing — wait ~90 s. Watch the logs:
#      reminder check completed: reminders_due=1 notifications_created=1

# 4. verify state (no manual /check call)
curl "http://localhost:8000/api/v1/monitor/status"
curl "http://localhost:8000/api/v1/emails/<email_id>/reminders"     # status: TRIGGERED
curl "http://localhost:8000/api/v1/notifications?email_id=<email_id>"

# 5. wait another interval and re-check — notification count is UNCHANGED (no spam)
curl "http://localhost:8000/api/v1/notifications?email_id=<email_id>"
```

### C. Restart safety

Trigger a reminder as above, `Ctrl-C` the server, restart it, wait one interval:
`GET /api/v1/notifications` shows **the same** rows — the reminder stays
`TRIGGERED`, no duplicate `user_reminder`.

### D. Disable it

`SCHEDULER_ENABLED=false` → startup logs
`scheduler disabled (scheduler_enabled=false) — not starting`;
`GET /api/v1/monitor/status` → `"scheduler": "stopped"`. The manual
`POST /api/v1/monitor/deadlines/check` still works.

---

## Configuration examples

| Scenario | `.env` |
|---|---|
| Development / demo (see it react fast) | `DEADLINE_CHECK_INTERVAL_SECONDS=10` · `REMINDER_CHECK_INTERVAL_SECONDS=10` |
| Default | `60` / `60` |
| Low-traffic prototype (save cycles) | `DEADLINE_CHECK_INTERVAL_SECONDS=300` · `REMINDER_CHECK_INTERVAL_SECONDS=120` |
| Manual only (CI, tests, debugging) | `SCHEDULER_ENABLED=false` |
| Multi-replica deploy | `SCHEDULER_ENABLED=true` on **one** instance, `false` on the rest |

---

## What Phase 11B.1 does NOT do

No push / FCM / device notifications, no local alarms, no Flutter changes, no
Celery/Redis, no distributed locking, no Gmail polling (emails still enter via
`GET /api/v1/gmail/unread/process`). Notifications are created with
`status = "PENDING"` and left for the Phase 11B.2 delivery layer.
