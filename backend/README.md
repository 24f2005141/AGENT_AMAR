# AGENT AMAR — Backend

**Phase 12.** The full pipeline + persistent state + automatic monitoring +
**incremental Gmail sync** (no more re-processing the whole unread inbox):

```
FastAPI lifespan → MonitorScheduler.start()
  ├─ deadline loop (every DEADLINE_CHECK_INTERVAL_SECONDS)   ┐
  ├─ reminder loop (every REMINDER_CHECK_INTERVAL_SECONDS)   ┤ same services the
  └─ gmail   loop (every GMAIL_SYNC_INTERVAL_SECONDS)        ┘ manual endpoints use
                     │
   GmailSyncService: baseline on connect (persist historyId) → then only
   messages ADDED since gmail_sync_state.last_history_id (Gmail History API)
                     ▼
… AMAR Orchestrator → Final Decision Object
                     → PersistenceService → SQLite (emails, actions, deadlines,
                                        processing_runs, reminders, notifications)
                     → GET /api/v1/emails/…            (read/act on state)
                     → DeadlineMonitorService          (auto, + POST /api/v1/monitor/deadlines/check)
                          ├─ escalate deadlines  NORMAL → REMINDER → URGENT → ALARM
                          └─ fire user-scheduled reminders
                     → notifications rows (status=PENDING)  → GET /api/v1/notifications
```

Persistence only **maps** the Final Decision Object into durable state — no
intelligence lives in DB code. Idempotent on `email_id`; reprocessing appends a
`ProcessingRun` and never destroys user state (viewed / completed / snoozed).

The monitor is **deterministic** (`run_deadline_check(now)`, time injectable). It
reads priority / viewed / completed / snoozed state straight from the DB and
emits `notifications` rows. The **background scheduler** (Phase 11B.1,
`app/services/scheduler.py`) triggers it on a configurable interval — no Celery /
Redis, single-instance only. See
[`../docs/BACKGROUND_SCHEDULER.md`](../docs/BACKGROUND_SCHEDULER.md). Still
**no actual delivery** (push / sound / FCM) — that is Phase 11B.2.

**Incremental Gmail sync** (Phase 12, `app/services/gmail_sync_service.py`):
first connect records a monitoring baseline (current mailbox `historyId`) and
**does not ingest the historical unread inbox**; later cycles process only
messages added since `gmail_sync_state.last_history_id` via the Gmail History
API. The resume point is persistent — survives restart / crash / scheduler
reload. Idempotent (dedup on `email_id`). See
[`../docs/GMAIL_SYNC.md`](../docs/GMAIL_SYNC.md).

**Frontend API contract:** [`../docs/API_CONTRACT.md`](../docs/API_CONTRACT.md)
is the frozen, authoritative surface for Flutter integration (Phase 10.5) —
every Flutter-facing endpoint, exact response shapes, frozen enums, error
shapes, and a sample flow.

---

## The intelligence pipeline (Phases 2–8)

The full intelligence pipeline, coordinated by the **AMAR Orchestrator**:

```
Gmail account
  → Google OAuth 2.0        fetch unread ids → fetch full message   (Phase 2)
  → MailIntakeAgent         deterministic normalization             (Phase 2)
  → NormalizedEmail
  → AMAR Orchestrator ─────────────────────────────────────────────  (Phase 8)
       ├─ TriageAgent        "what kind of email is this?"           (Phase 3)
       ├─ ActionAgent        "what must the user DO?"       (gated)   (Phase 5)
       ├─ DeadlineAgent      "WHEN must the user do it?"    (gated)   (Phase 6)
       └─ PriorityAgent      "how important & urgent right now?"      (Phase 7)
  → merge + resolve conflicts + route
  → Final Decision Object (store / notify / monitor / folder_label)
```

The orchestrator is **deterministic** — it coordinates, validates, merges and
resolves conflicts; it does **not** re-run any agent's intelligence and it does
**not** act on the routing flags (no notification sending, no monitoring, no DB,
no Gmail writes). Gmail access is **read-only**.

The architecture contract lives in the Obsidian vault (`../AGENT-AMAR/`):

| Code | Vault source of truth |
|---|---|
| `app/models/email.py` | `04-Schemas/Email Schema.md` |
| `app/models/agent_output.py` | `04-Schemas/Agent Output Schema.md` |
| `app/models/triage.py` | `01-Agents/Triage Agent.md` |
| `app/models/action.py` | `04-Schemas/Action Schema.md` |
| `app/models/deadline.py` | `01-Agents/Deadline Agent.md` |
| `app/models/priority.py` | `01-Agents/Priority Agent.md` + `03-Memory/Priority Rules.md` |
| `app/agents/intake_agent.py` | `01-Agents/Mail Intake Agent.md` |
| `app/agents/triage_agent.py` + `triage_rules.py` | `01-Agents/Triage Agent.md` + `03-Memory/Classification Rules.md` + `03-Memory/Important Senders.md` |
| `app/agents/action_agent.py` + `action_rules.py` | `01-Agents/Action Agent.md` + `04-Schemas/Action Schema.md` |
| `app/agents/deadline_agent.py` + `app/utils/deadline_parsing.py` | `01-Agents/Deadline Agent.md` |
| `app/agents/priority_agent.py` + `app/utils/priority_scoring.py` + `app/services/priority_context.py` | `01-Agents/Priority Agent.md` + `03-Memory/Priority Rules.md` + `03-Memory/User Preferences.md` + `03-Memory/Important Senders.md` |
| `app/agents/amar_orchestrator.py` + `app/models/decision.py` | `01-Agents/AMAR Orchestrator.md` + `02-Workflows/New Email Processing.md` + `04-Schemas/Agent Output Schema.md` |
| `app/db/` + `app/repositories/` + `app/services/persistence_service.py` | `04-Schemas/Persistent Email State.md` + `02-Workflows/New Email Processing.md` §9-11 + `02-Workflows/Deadline Monitoring.md` |

---

## Layout

```
backend/
├── app/
│   ├── main.py                        FastAPI app + error handler
│   ├── api/
│   │   ├── deps.py                    dependency providers (DI seams)
│   │   ├── routes_auth.py             /api/v1/auth/google/*
│   │   └── routes_gmail.py            /api/v1/gmail/unread[/triage]
│   ├── agents/
│   │   ├── intake_agent.py            Mail Intake Agent (deterministic)
│   │   ├── triage_agent.py + triage_rules.py    Triage Agent (deterministic + LLM)
│   │   ├── action_agent.py + action_rules.py    Action Agent (deterministic + LLM)
│   │   ├── deadline_agent.py          Deadline Agent (deterministic + LLM)
│   │   ├── priority_agent.py          Priority Agent (deterministic + bounded LLM)
│   │   └── amar_orchestrator.py       AMAR Orchestrator (deterministic coordinator)
│   ├── db/                            Base + TZDateTime, session/engine, ORM models  (Phase 9/10)
│   ├── repositories/                  one repo per aggregate (email/action/deadline/reminder/notification)
│   ├── models/                        Pydantic: NormalizedEmail, AgentOutput, …, FinalDecision, persistence + monitoring DTOs
│   ├── services/
│   │   ├── gmail_auth_service.py      OAuth flow + credential lifecycle
│   │   ├── gmail_service.py           parsing helpers + authenticated client
│   │   ├── {gmail,triage,action,deadline,priority,amar}_pipeline.py   per-phase fetch pipelines
│   │   ├── persistence_service.py     Final Decision Object → persistent state  (Phase 9)
│   │   ├── escalation_policy.py       centralised escalation ladders + quiet hours  (Phase 10)
│   │   ├── deadline_monitor_service.py deterministic monitor → notification events  (Phase 10)
│   │   ├── reminder_service.py        user-scheduled reminders (≠ snooze)  (Phase 10)
│   │   ├── scheduler.py              in-process asyncio scheduler → monitor + gmail sync  (Phase 11B.1 / 12)
│   │   ├── gmail_sync_service.py     incremental Gmail sync + persistent baseline  (Phase 12)
│   │   ├── priority_context.py        memory adapter (senders + user prefs; DB-swappable)
│   │   ├── llm_service.py             provider-agnostic LLM abstraction (none/openai/anthropic/gemini/ollama)
│   │   └── token_store.py             TokenStore ABC + File/InMemory impls
│   ├── core/  {config.py, errors.py}
│   └── utils/ {text_cleaning, deadline_parsing, priority_scoring}
└── tests/                             518 tests, no network, LLM + OAuth mocked, temp SQLite (scheduler off)
```

---

## Quick start (no Gmail)

```bash
cd backend
python -m venv .venv
#   PowerShell:  .venv\Scripts\Activate.ps1
#   bash:        source .venv/Scripts/activate
pip install -r requirements.txt
cp .env.example .env            # optional

uvicorn app.main:app --reload    # creates ./agent_amar.db on first start
pytest -q                        # uses a throwaway temp SQLite, never the dev db
```

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/api/v1/auth/google/status
# {"connected": false, "provider": "gmail", ...}
```

**Database file:** `backend/agent_amar.db` (SQLite, git-ignored) by default —
set `DATABASE_URL` in `.env` to move it or point at PostgreSQL. Tables are
created automatically at startup (`init_db()` in `app/main.py`'s lifespan);
delete the file to reset. A migration tool (Alembic) can be added later without
changing the models — the constraint naming convention is already in place.

---

## Connecting a real Gmail account

### 1. Create a Google Cloud project

1. Go to <https://console.cloud.google.com/>.
2. Top bar → project dropdown → **New Project**. Name it (e.g. `agent-amar`) → **Create**.
3. Make sure the new project is selected in the dropdown.

### 2. Enable the Gmail API

1. Navigation menu → **APIs & Services → Library**.
2. Search **Gmail API** → open it → **Enable**.

### 3. Configure the OAuth consent screen

1. **APIs & Services → OAuth consent screen**.
2. User type: **External** → **Create**.
3. Fill in the required fields:
   - App name: `AGENT AMAR`
   - User support email: your email
   - Developer contact email: your email
4. **Scopes** page → **Add or remove scopes** → filter for `gmail.readonly`
   → select `.../auth/gmail.readonly` → **Update** → **Save and continue**.
5. **Test users** page → **Add users** → add the Gmail address you will connect
   (while the app is in "Testing", only listed test users can authorize it).
6. **Save and continue** → **Back to dashboard**. Leave publishing status as
   **Testing** — that is fine for development.

### 4. Create OAuth credentials

1. **APIs & Services → Credentials → Create Credentials → OAuth client ID**.
2. Application type: **Web application**.
3. Name: `agent-amar-backend`.
4. **Authorized redirect URIs → Add URI**:
   ```
   http://localhost:8000/api/v1/auth/google/callback
   ```
   (must match `GOOGLE_REDIRECT_URI` exactly).
5. **Create**. Copy the **Client ID** and **Client secret**.

### 5. Put the credentials in `.env`

```bash
cp .env.example .env
```

Edit `backend/.env`:

```env
APP_ENV=development
APP_NAME=AGENT_AMAR

GOOGLE_CLIENT_ID=1234567890-abcdef.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-your-secret
GOOGLE_REDIRECT_URI=http://localhost:8000/api/v1/auth/google/callback
GOOGLE_TOKEN_STORAGE_PATH=.tokens
```

`.env` and `.tokens/` are git-ignored — **never commit them**.

### 6. Run the server

```bash
uvicorn app.main:app --reload --port 8000
```

### 7. Connect Gmail

Open in a browser:

```
http://localhost:8000/api/v1/auth/google/login
```

You are redirected to Google's consent screen. Approve the read-only Gmail
permission. Google redirects back to `/api/v1/auth/google/callback`, which
stores the credentials and returns:

```json
{ "status": "connected", "connected": true, "provider": "gmail",
  "account_email": "you@gmail.com", "scopes": ["https://www.googleapis.com/auth/gmail.readonly"] }
```

> `http://localhost` redirects work because `APP_ENV=development` sets
> `OAUTHLIB_INSECURE_TRANSPORT` for you. Use HTTPS in production.

### 8. Check connection status

```bash
curl http://localhost:8000/api/v1/auth/google/status
# {"connected": true, "provider": "gmail", "account_email": "you@gmail.com", ...}
```

### 9. Fetch unread email through the pipeline

```bash
curl "http://localhost:8000/api/v1/gmail/unread?max_results=5"
```

```json
{
  "count": 2,
  "max_results": 5,
  "unread_ids_seen": 2,
  "emails": [
    {
      "summary": { "email_id": "gmail_...", "sender": {...}, "subject": "...",
                   "is_unread": true, "has_attachments": false },
      "intake": { "status": "ok", "confidence": 1.0, "needs_human_review": false,
                  "run_id": "run_..." },
      "email": { ...full NormalizedEmail... }
    }
  ],
  "errors": []
}
```

To disconnect during development:

```bash
curl -X POST http://localhost:8000/api/v1/auth/google/disconnect
```

### 10. Classify unread email (Triage Agent)

```bash
curl "http://localhost:8000/api/v1/gmail/unread/triage?max_results=5"
```

```json
{
  "count": 2,
  "emails": [
    {
      "email": { "...full NormalizedEmail..." },
      "triage": {
        "category": "INTERNSHIP",
        "subcategory": "application_form",
        "importance_estimate": "HIGH",
        "further_analysis_required": true,
        "confidence": 0.94,
        "needs_human_review": false,
        "classification_method": "deterministic",
        "reasoning_summary": "Matched internship, application form; classified as INTERNSHIP..."
      },
      "triage_envelope": { "...full AgentOutput..." }
    }
  ],
  "errors": []
}
```

`classification_method` is `deterministic`, `llm`, or `llm_fallback_deterministic`.
With `LLM_PROVIDER=none` (the default) it is always `deterministic`.

### 11. Detect required actions (Action Agent)

```bash
curl "http://localhost:8000/api/v1/gmail/unread/actions?max_results=5"
```

```json
{
  "count": 1,
  "emails": [
    {
      "email": { "...full NormalizedEmail..." },
      "triage": { "category": "INTERNSHIP", "confidence": 0.94, "classification_method": "deterministic", "needs_human_review": false },
      "action": {
        "action_required": true,
        "action_type": "FORM_SUBMISSION",
        "action_description": "Multiple actions required: FORM_SUBMISSION, DOCUMENT_UPLOAD",
        "actions": [
          { "action_type": "FORM_SUBMISSION", "action_description": "Fill and submit the form (re: Summer Internship 2026)",
            "blocking": true, "target_link": "https://forms.gle/abc",
            "raw_deadline_hint": "by 5 September", "confidence": 0.95,
            "evidence": "Fill the application form at https://forms.gle/abc before 5 September." },
          { "action_type": "DOCUMENT_UPLOAD", "...": "..." }
        ],
        "confidence": 0.95,
        "detection_method": "deterministic",
        "needs_human_review": false,
        "reasoning_summary": "Detected 2 explicit action(s): FORM_SUBMISSION, DOCUMENT_UPLOAD."
      },
      "action_envelope": { "...full AgentOutput..." }
    }
  ],
  "errors": []
}
```

---

## Persistence (Phase 9)

Every `/process` call maps its Final Decision Objects into SQLite via
`PersistenceService` (SQLAlchemy 2.x, sync). Tables: `emails` (idempotency key
`email_id`), `actions`, `deadlines`, `processing_runs` (append-only history),
`reminders` (user-scheduled — Phase 10), `notifications` (intended alert
events — nothing is sent). Tables are auto-created at startup (`init_db()` in
the app lifespan); `DATABASE_URL` (`.env`) switches SQLite → `postgresql://…`
with no code change.

**Idempotency & reprocessing** — reprocessing the same message updates the same
`emails` row and appends a `ProcessingRun`; it **preserves** `is_viewed`,
`is_completed`, `snoozed_until`, each action's `status`, and each deadline's
monitoring fields. Actions/deadlines match by the agent's `action_id` /
`deadline_id`; no-longer-detected ones are dropped only if untouched.

### State endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/emails` | persisted emails; filters: `priority` `category` `action_required` `needs_human_review` `viewed` `completed` `limit` `offset` |
| GET | `/api/v1/emails/human-review` | emails flagged for review |
| GET | `/api/v1/emails/{email_id}` | one email + actions + deadlines + notifications + latest processing |
| GET | `/api/v1/emails/{email_id}/processing` | full processing history |
| PATCH | `/api/v1/emails/{email_id}/viewed` | mark viewed |
| PATCH | `/api/v1/emails/{email_id}/snooze` | body `{ "snoozed_until": "<ISO>" }` |
| PATCH | `/api/v1/emails/{email_id}/actions/{action_ref}/complete` | mark an action done (recomputes `is_completed`) |
| PATCH | `/api/v1/emails/{email_id}/actions/{action_ref}/dismiss` | dismiss an action |
| GET | `/api/v1/actions/pending` | all `PENDING` actions across emails |
| GET | `/api/v1/deadlines/upcoming` | active future deadlines (`?within_hours=N`) |

```bash
curl "http://localhost:8000/api/v1/gmail/unread/process?max_results=5"     # fetch + persist
curl "http://localhost:8000/api/v1/emails?priority=CRITICAL"
curl -X PATCH "http://localhost:8000/api/v1/emails/gmail_ABC123/viewed"
```

---

## Deadline monitoring + reminder escalation (Phase 10)

`DeadlineMonitorService.run_deadline_check(now)` — deterministic, time-injectable.
Run automatically by the background scheduler (Phase 11B.1) and also on demand via
`POST /api/v1/monitor/deadlines/check`. It auto-starts monitoring for
`should_monitor` deadlines, then for each one computes time-remaining and the
escalation rung from the **centralised `escalation_policy.py`** ladders (mirror of
`02-Workflows/Reminder Escalation.md`):

| Rung | When | `requires_alarm` |
|---|---|---|
| `NORMAL` | at processing time, priority ≥ HIGH (Phase 9) | – |
| `REMINDER` | `CRITICAL` 30 m · `URGENT` 12 h · `HIGH` 24/6 h · `MEDIUM` 24 h | no |
| `URGENT` | `CRITICAL` 15 m · `URGENT` 3/1 h · `HIGH` 1 h | no |
| `ALARM` | `CRITICAL` ≤5 m · `URGENT` ≤15 m — **and** unviewed/action-pending, not snoozed, not done | **yes** |

Viewed ⇒ drop one rung. Snoozed ⇒ suppress (keep monitoring). Quiet hours
(23:00–07:00 IST) ⇒ `NORMAL`/`REMINDER`/`URGENT` held (`SKIPPED` row);
`ALARM` breaks through only for `CRITICAL`. Past deadline ⇒ one
`deadline_passed` event, stop after 24 h grace. Every rung fires **once** per
deadline — de-dup is derived from `notifications.highest_escalation_for()`.

**Snooze ≠ scheduled reminder.** `snoozed_until` suppresses; a `reminders` row
("remind me at T", many per email, optional `action_ref`) explicitly alerts and
is evaluated independently — it never blocks alarm escalation.

### Monitoring / reminder / notification endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/monitor/deadlines/check` | run the monitor **manually**; body `{ "now": "<ISO>" }` optional (testing/replay) |
| GET | `/api/v1/monitor/status` | background scheduler status (Phase 11B.1) |
| POST | `/api/v1/emails/{email_id}/reminders` | create a reminder — `{ "reminder_at": "<ISO>", "action_ref"?, "note"? }` |
| GET | `/api/v1/emails/{email_id}/reminders` | this email's reminders |
| GET | `/api/v1/reminders` | all reminders (filter `status`) |
| DELETE | `/api/v1/emails/{email_id}/reminders/{id}` | cancel a `PENDING` reminder |
| GET | `/api/v1/notifications` | query events; filters: `status` `severity` `type` `email_id` `requires_alarm` `created_after` `limit` `offset` |
| GET | `/api/v1/notifications/{id}` | one event |

```bash
curl "http://localhost:8000/api/v1/monitor/status"
curl -X POST "http://localhost:8000/api/v1/monitor/deadlines/check"   # scheduler does this automatically
curl -X POST "http://localhost:8000/api/v1/emails/gmail_ABC/reminders" \
     -H 'content-type: application/json' -d '{"reminder_at":"2026-09-02T09:00:00+05:30"}'
curl "http://localhost:8000/api/v1/notifications?requires_alarm=true"
```

### Incremental Gmail sync (Phase 12)

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/gmail/sync` | run incremental sync now (401 if Gmail not connected) — first call baselines, later calls process only new mail |
| GET | `/api/v1/gmail/sync/status` | `{ monitoring, account_email, monitoring_started_at, last_sync_at, last_history_id }` |

The monitoring baseline (`gmail_sync_state.last_history_id`) is persistent — it
survives restart / crash / scheduler reload. The historical unread inbox is
never ingested. Idempotent (dedup on `email_id`). `POST /api/v1/gmail/unread/process`
is unchanged. Full design: [`../docs/GMAIL_SYNC.md`](../docs/GMAIL_SYNC.md).

### Background scheduler (Phase 11B.1 / 12)

An in-process `asyncio` scheduler (`app/services/scheduler.py`) runs the
deadline, reminder **and Gmail-sync** checks automatically — starts/stops with
the app, no Celery/Redis, **single-instance only**. Config (`.env`):

| Env var | Default |
|---|---|
| `SCHEDULER_ENABLED` | `true` |
| `DEADLINE_CHECK_INTERVAL_SECONDS` | `60` |
| `REMINDER_CHECK_INTERVAL_SECONDS` | `60` |
| `GMAIL_SYNC_ENABLED` | `true` |
| `GMAIL_SYNC_INTERVAL_SECONDS` | `120` |
| `GMAIL_SYNC_MAX_MESSAGES` | `25` |

Full design, dedup guarantees, failure handling and manual test procedure:
[`../docs/BACKGROUND_SCHEDULER.md`](../docs/BACKGROUND_SCHEDULER.md) ·
[`../docs/GMAIL_SYNC.md`](../docs/GMAIL_SYNC.md).

---

## AMAR Orchestrator (Phase 8)

Answers **"what should the system finally decide and route?"** — the deterministic
coordinator. It runs the agents in order, validates each output, resolves the
documented cross-agent conflicts, and emits **one Final Decision Object**. It
does **not** reclassify / re-detect / re-score, and it does **not** send
notifications, monitor, write to Gmail. (Persisting the result is Phase 9, done
by the `/process` endpoint after the orchestrator returns.)

`GET /api/v1/gmail/unread/process` returns, per email:

```json
{
  "count": 1,
  "emails": [
    {
      "email_id": "gmail_...", "subject": "...", "sender": {...},
      "status": "ok",
      "final_decision": {
        "final_category": "PLACEMENT",
        "action_required": true,
        "primary_action_type": "FORM_SUBMISSION",
        "actions": [{"action_type": "FORM_SUBMISSION", "blocking": true, "confidence": 0.95}],
        "deadline": "2026-08-28T16:30:00+05:30",
        "deadline_ambiguous": false, "deadline_is_past": false,
        "proximity_bucket": "WITHIN_1H",
        "priority_level": "CRITICAL", "priority_score": 100,
        "routing": {"store": true, "notify": true, "monitor": true, "folder_label": "AMAR/Opportunities"},
        "needs_human_review": false, "review_reasons": [],
        "conflicts_resolved": [{"rule": "deterministic_deadline_authoritative", "detail": "..."}],
        "agent_trace": [
          {"agent": "Mail Intake Agent", "status": "ok", "confidence": 1.0, "method": "deterministic"},
          {"agent": "Triage Agent", "status": "ok", "confidence": 0.96, "method": "deterministic", "fallback_used": false, "duration_ms": 2, "error_codes": []},
          ...
        ]
      },
      "activity_log": "---\nTimestamp: ...\nAgent: Triage Agent\n..."
    }
  ],
  "errors": []
}
```

**Execution** — Triage → (Action + Deadline, *gated*: skipped for a confident
low-band category) → Priority (always). Any agent exception → a synthetic
`error` output + trace entry, the run continues; Priority failure → a
conservative `HIGH`/`LOW` fallback. The run only aborts if the email can't be
normalised.

**Routing** — `store` always `true`; `notify`/`monitor` from the Priority Agent
unless a conflict forces them on; `folder_label` = `final_category` → `AMAR/<Group>`.

**Conflict rules** (deterministic, each recorded in `conflicts_resolved[]`):
low combined confidence, low-confidence Triage + actionable content (CASE 2),
low-band category vs confident action (CASE 2 variant), ambiguous deadline +
action/priority (CASE 4), deadline-without-action (CASE 3), agent partial/error,
fallback-on-high-value. An upstream `needs_human_review` propagates **only when
consequential**; every `true` carries a `review_reasons[]` entry.

---

## Priority Agent (Phase 7)

Answers **"how important and urgent is this email right now?"** — combines the
Triage / Action / Deadline outputs into a score + level. It does **not**
reclassify, re-detect actions, re-extract deadlines, notify, schedule, or touch
Gmail.

```bash
curl "http://localhost:8000/api/v1/gmail/unread/priorities?max_results=5"
```

```json
{
  "priority": {
    "priority_level": "CRITICAL",
    "priority_score": 100,
    "proximity_bucket": "WITHIN_1H",
    "time_remaining_seconds": 1740,
    "deadline_is_past": false,
    "notify": true, "monitor": true,
    "score_breakdown": [
      {"factor": "action_required", "points": 30},
      {"factor": "deadline_within_1h", "points": 40},
      {"factor": "internship_placement_or_job", "points": 20},
      {"factor": "important_sender_critical", "points": 20}
    ],
    "factors": {"category": "PLACEMENT", "action_required": true, "deadline_proximity": "WITHIN_1H", "important_sender": "CRITICAL"},
    "overrides_applied": ["pref_internship_placement_min_urgent"],
    "scoring_method": "deterministic",
    "reasoning_summary": "PLACEMENT email — action required, deadline within 1h ... → CRITICAL (score 100).",
    "confidence": 0.9, "needs_human_review": false,
    "reference_time_used": "2026-08-28T16:00:00+05:30"
  }
}
```

**Levels** (`Priority Rules.md` §1): `CRITICAL` 90-100 · `URGENT` 75-89 ·
`HIGH` 55-74 · `MEDIUM` 30-54 · `LOW` 0-29.
**Proximity buckets** (§4): `OVERDUE` · `WITHIN_1H` · `WITHIN_24H` · `WITHIN_72H`
· `LATER` · `NONE` — computed by backend code from the deadline vs the current
time (timezone-aware only).

**Deterministic core** (`priority_scoring.py`) sums the `Priority Rules.md` §2
factor table. A **bounded LLM nudge** (`±10`, config `PRIORITY_LLM_MAX_ADJUSTMENT`)
is used *only* when signals conflict (urgency wording in a promo, important
sender on a social notification, …) and never breaks the pipeline. Override
precedence: important-sender floor → category band → explicit user preference
(§6) → `OVERDUE` ceiling `URGENT` → safety bias.

| Setting | Default | Effect |
|---|---|---|
| `PRIORITY_AMBIGUOUS_DEADLINE_FACTOR` | 0.7 | proximity points × this when the deadline is flagged but has a datetime |
| `PRIORITY_LLM_MAX_ADJUSTMENT` | 10 | hard cap on the optional LLM nudge |

The memory (`Important Senders.md`, `User Preferences.md`) is read through
`PriorityContext` — `StaticPriorityContext` today, DB-swappable later.

---

## Deadline Agent (Phase 6)

Answers **"does this email contain a deadline, and if so, when?"**. It detects,
extracts, normalises (to offset-aware ISO 8601) and flags ambiguity — and reports
`is_past`. It does **not** compute time-remaining, schedule reminders, notify, or
decide priority.

```bash
curl "http://localhost:8000/api/v1/gmail/unread/deadlines?max_results=5"
```

```json
{
  "count": 1,
  "emails": [
    {
      "email": { "...NormalizedEmail..." },
      "triage": { "category": "INTERNSHIP", "confidence": 0.94 },
      "action": { "action_required": true, "action_type": "FORM_SUBMISSION", "actions": [ ... ] },
      "deadline": {
        "has_deadline": true,
        "primary": {
          "raw_deadline_text": "by 5 September 2026, 6:00 PM IST",
          "normalized_deadline": "2026-09-05T18:00:00+05:30",
          "timezone": "Asia/Kolkata",
          "ambiguity_flag": false, "ambiguity_reason": null, "is_past": false
        },
        "deadlines": [
          { "deadline_id": "dl_001", "normalized_deadline": "2026-09-05T18:00:00+05:30",
            "date_only": false, "ambiguity_flag": false, "is_past": false, "confidence": 0.9,
            "action_context": "FORM_SUBMISSION", "related_action_id": "act_001",
            "source": "deterministic", "evidence": "...the deadline is 5 September 2026, 6:00 PM IST." }
        ],
        "event_dates": [],
        "monitoring_required": true,
        "reference_time_used": "2026-08-28T09:14:22+05:30",
        "detection_method": "deterministic",
        "needs_human_review": false
      },
      "deadline_envelope": { "...full AgentOutput..." }
    }
  ]
}
```

**Hybrid** (`deadline_agent.py` + `deadline_parsing.py`):

1. **Deterministic** — extract temporal phrases, classify each **DEADLINE /
   EVENT_DATE / IGNORE** (an interview date is not a deadline; dates in promos
   are ignored), normalise relatives against the email's `received_at`.
2. **LLM fallback** — only when uncertain, a numeric date is DD/MM-vs-MM/DD
   ambiguous, deadline language yielded nothing concrete, or linked deadlines
   conflict. Every LLM deadline must be backed by text in the email.

| Setting | Default | Effect |
|---|---|---|
| `DEADLINE_REVIEW_THRESHOLD` | 0.55 | below ⇒ `needs_human_review` |
| `DEADLINE_LLM_THRESHOLD` | 0.60 | below / on conflict / on unresolved cue ⇒ LLM |
| `DEADLINE_DATE_LOCALE` | `DMY` | reading of `05/09/2026` (`DMY` = 5 Sep, `MDY` = May 9) |

Conventions: EOD → 23:59:59 · noon → 12:00 · COB → 17:00 · "midnight" → end of
day · date-with-no-time → 23:59:59 (flagged) · "next Friday" → a week out
(flagged) · "soon"/"next week" → `null` (flagged). Reference time = `received_at`.

Multiple deadlines are kept separate and linked to the [[Action Agent]] action
they belong to; the singular top-level fields describe the **primary** deadline
(what the Priority Agent will consume).

---

## Action Agent (Phase 5)

Answers **"what must the user DO because of this email?"** — zero or more
actions, each of one of the 9 types in `04-Schemas/Action Schema.md`
(`FORM_SUBMISSION`, `REPLY`, `REGISTRATION`, `DOCUMENT_UPLOAD`, `PAYMENT`,
`ATTEND_EVENT`, `COMPLETE_ASSIGNMENT`, `READ_AND_ACKNOWLEDGE`, `OTHER`).

It does **not** classify the email, compute priority/urgency, normalise
deadlines (it only copies a verbatim `raw_deadline_hint`), schedule anything,
touch Gmail, or perform an action.

**Hybrid** (same shape as Triage):

1. **Deterministic** — explicit action-phrase matching on the cleaned body +
   subject, split into clauses; per-clause **negation** ("do not reply"),
   **completion** ("already submitted") and **conditional** ("reply only if…")
   context suppresses false actions; repeated language → one action.
2. **LLM fallback** — only when deterministic confidence `< ACTION_LLM_THRESHOLD`,
   signals conflict, or all actions are merely implied. Constrained + validated;
   falls back to deterministic on any failure.

| Setting | Default | Effect |
|---|---|---|
| `ACTION_REVIEW_THRESHOLD` | 0.55 | below ⇒ `needs_human_review = true` |
| `ACTION_LLM_THRESHOLD` | 0.65 | below / on conflict ⇒ escalate to the LLM |

The Triage category is supporting context only — an explicit instruction in the
email always wins over category.

---

## Triage Agent (Phase 3)

Answers **"what kind of email is this?"** — one of 15 categories from
`03-Memory/Classification Rules.md`. It does **not** compute priority, extract
deadlines, detect actions, or touch Gmail.

**Hybrid:**

1. **Deterministic** (`triage_agent.py` + `triage_rules.py`) — keyword / sender /
   structure scoring, then the documented precedence rules. Always runs.
2. **LLM fallback** (`llm_service.py`) — only when deterministic confidence is
   `< TRIAGE_LLM_THRESHOLD` **and** a provider is configured. The hard precedence
   rules still constrain the LLM's answer.

**Enable the LLM (optional)** in `.env`:

```env
LLM_PROVIDER=anthropic          # or: openai
LLM_MODEL=claude-sonnet-5       # openai e.g. gpt-4o-mini
LLM_API_KEY=sk-...
```

Confidence thresholds (all in `app/core/config.py` / `.env`):

| Setting | Default | Effect |
|---|---|---|
| `TRIAGE_REVIEW_THRESHOLD` | 0.55 | below ⇒ `needs_human_review = true` |
| `TRIAGE_LLM_THRESHOLD` | 0.70 | below ⇒ escalate to the LLM |
| `TRIAGE_UNKNOWN_OPPORTUNITY_CAP` | 0.70 | cap for opportunity mail from an unknown sender |

---

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness probe |
| GET | `/` | Service metadata + route list |
| POST | `/intake/gmail` | Normalize a raw Gmail payload (dev/testing) |
| GET | `/api/v1/auth/google/login` | Redirect to Google consent screen |
| GET | `/api/v1/auth/google/callback` | OAuth redirect target; stores credentials |
| GET | `/api/v1/auth/google/status` | `{ "connected": bool, "provider": "gmail" }` |
| POST | `/api/v1/auth/google/disconnect` | Forget stored credentials (dev) |
| GET | `/api/v1/gmail/unread?max_results=N` | Unread messages → `NormalizedEmail` |
| GET | `/api/v1/gmail/unread/triage?max_results=N` | ... → Triage Agent classification |
| GET | `/api/v1/gmail/unread/actions?max_results=N` | ... → Action Agent required actions |
| GET | `/api/v1/gmail/unread/deadlines?max_results=N` | ... → Deadline Agent extracted deadlines |
| GET | `/api/v1/gmail/unread/priorities?max_results=N` | ... → Priority Agent score + level |
| GET | `/api/v1/gmail/unread/process?max_results=N&persist=true` | ... → **Final Decision Object + persisted to SQLite** |

### Error responses

Typed, clean, and free of secrets:

| Situation | HTTP | `error` |
|---|---|---|
| Gmail not connected / token unusable | 401 | `GmailNotConnectedError` / `TokenRefreshError` |
| OAuth not configured on the server | 503 | `OAuthConfigError` |
| User denied consent | 400 | `OAuthAccessDeniedError` |
| Gmail API failure | 502 | `GmailApiError` |
| Message id not found | 404 | `MessageNotFoundError` |

---

## Gmail scope

Only **`https://www.googleapis.com/auth/gmail.readonly`**.

* It is the narrowest scope that can still read a message **body** (which the
  Mail Intake Agent needs). `gmail.metadata` is narrower but cannot read bodies.
* It grants **read-only** access: no send, no modify, no delete, no label
  changes, no settings, and no access to any other Google API.

---

## Token storage

Credentials are stored via the `TokenStore` interface
(`get` / `put` / `delete` / `list_accounts`), keyed by account id. Development
uses `FileTokenStore` — one JSON file per account under `GOOGLE_TOKEN_STORAGE_PATH`
(`.tokens/`, git-ignored, not encrypted). Swap in a database-backed
implementation later without touching the auth service or routes.

---

## Notes on the vault contract

`NormalizedEmail`, the Mail Intake Agent, and the 15-category list were **not**
redesigned. Clarifications recorded in the vault:

1. `Email Schema.md` — explicit "ID convention" rule (`gmail_` / `gmail_thread_`).
2. `Agent Output Schema.md` — a "Mail Intake Agent" section.
3. `Triage Agent.md` — "Backend implementation notes": hybrid design, thresholds,
   extra `signals` keys.
4. `Classification Rules.md` — "Backend deviations".
5. `Action Schema.md` — "Backend notes".
6. `Action Agent.md` — "Backend implementation notes".
7. `Deadline Agent.md` — "Backend implementation notes": singular fields = the
   *primary* deadline + additive `deadlines[]` / `event_dates[]` / `is_past`.
8. `Priority Agent.md` — "Backend implementation notes"; `Priority Rules.md` §6
   and `User Preferences.md` §6.1 clarified.
9. `AMAR Orchestrator.md` — "Backend implementation notes": execution + gate,
   the Final Decision Object fields, routing rules, the conflict-resolution
   table, the review-propagation rule, the folder-label mapping.
   `Agent Output Schema.md` — the orchestrator `data` example updated
   (structured `agent_trace`, `email_id`/`needs_human_review`/`actions`/
   `review_reasons` in the payload). `Agent Activity Log.md` — note on
   `to_activity_log()`.
10. `Deadline Monitoring.md` / `Reminder Escalation.md` — "Backend
    implementation notes" (Phase 10: the monitor, the 4-rung ladder,
    alarm eligibility, quiet-hours policy, derived escalation state). New
    `04-Schemas/Reminder Schema.md`. `User Preferences.md` §4 — snooze vs
    scheduled-reminder note.
11. Phase 10.5 froze the Flutter contract in `docs/API_CONTRACT.md`. Phase
    11B.1 added the background scheduler — `docs/BACKGROUND_SCHEDULER.md`.
12. Phase 12 added incremental Gmail sync — `docs/GMAIL_SYNC.md`, new
    `gmail_sync_state` table, `POST /api/v1/gmail/sync`.

---

## Next step

**Phase 11B.2 — notification delivery + device alarms.** A delivery layer turns
`PENDING` `notifications` rows into push / FCM / sound / full-screen alarms and
marks them `SENT`; the Flutter app wires the alarm UI to `requires_alarm`
notifications. Also later: rate-limit windows, the daily digest, muted-sender
suppression, learned escalation down-weighting, Gmail label mutation, PostgreSQL,
a Gmail `watch` push subscription (replace the sync poll), multi-account, and a
distributed-safe scheduler before scaling past one instance.
