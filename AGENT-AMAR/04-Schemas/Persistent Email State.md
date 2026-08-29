# 📦 Schema: Persistent Email State

**Related:** [[AMAR Orchestrator]] · [[New Email Processing]] · [[Deadline Monitoring]] · [[Email Schema]] · [[Agent Output Schema]]

The operational state AGENT AMAR remembers **after** the intelligence pipeline
runs. Added in Phase 9 (`backend/app/db/`).

> **Separation of concerns.** The agents produce a [[Agent Output Schema|Final Decision Object]].
> The persistence layer only **maps** that object into durable state — it holds
> **no** classification / action / deadline / priority logic.
>
> ```
> AGENT INTELLIGENCE → Final Decision Object → PersistenceService → Persistent Email State
> ```

---

## Tables

| Table | Rows | Purpose |
|---|---|---|
| `emails` | one per Gmail message | identity + latest analysis + user state + routing |
| `actions` | 0..N per email | one per detected action; carries the **user's** status |
| `deadlines` | 0..N per email | extracted deadline + its **separate** monitoring state |
| `processing_runs` | 1..N per email | one per pipeline pass — **append-only history** |
| `reminders` | 0..N per email | **user-scheduled** reminders (Phase 10; ≠ snooze) — see [[Reminder Schema]] |
| `notifications` | 0..N per email | *intended* alert events; no sender here ([[Reminder Escalation]] · [[Reminder Schema]]) |

`email_id` (`gmail_…`, from [[Email Schema]]) is the **idempotency key** — unique.

---

## `emails` — field groups

| Group | Fields | Owner |
|---|---|---|
| Identity | `email_id`, `thread_id`, `source` | Gmail |
| Metadata | `sender_name`, `sender_email`, `subject`, `received_at` | Gmail (refreshed each fetch) |
| Classification | `final_category`, `category_confidence` | **system** (overwritten each run) |
| Priority | `priority_level`, `priority_score`, `proximity_bucket`, `deadline_is_past` | **system** |
| Gmail state | `is_unread` | Gmail (refreshed) |
| **User state** | `is_viewed` / `viewed_at`, `is_completed` / `completed_at`, `snoozed_until` | **user — preserved across reprocessing** |
| System flags | `action_required`, `needs_human_review` | system |
| Routing | `folder_label`, `should_notify`, `should_monitor` | system (from `routing`) |
| Timestamps | `created_at`, `updated_at`, `processed_at` | system |

`actions.status` ∈ `PENDING | COMPLETED | DISMISSED` (user-owned).
`deadlines`: extraction fields are system; `is_monitoring` / `monitoring_started_at` /
`monitoring_stopped_at` are **preserved** (Phase 10 drives them).
`notifications.status` ∈ `PENDING | SENT | FAILED | SKIPPED`.

---

## State model (independent fields — not one enum)

An email is a **combination** of independent axes, per [[Deadline Monitoring]]:

```
Gmail:   is_unread ────────────────┐
User:    is_viewed ────────────────┤   e.g. "unread + pending",
User:    is_completed ─────────────┤        "viewed + snoozed",
User:    snoozed_until (nullable) ─┤        "viewed + completed"
System:  needs_human_review ───────┘
Deadline (per row): is_monitoring
```

### Transitions

```
NEW ── /process ──▶ PROCESSED ── PATCH /viewed ──▶ VIEWED ── all blocking actions COMPLETED ──▶ ACTION_COMPLETED
                        │
                        └── PATCH /snooze ──▶ SNOOZED ── snoozed_until passes ──▶ ACTIVE AGAIN
```

- `is_completed` is **derived**: `action_required` and every blocking action
  (or every action, if none are blocking) is `COMPLETED`/`DISMISSED`. Recomputed
  on every reprocess and on every action-status change.
- Deadline monitoring runs independently of the email's viewed/completed state.

---

## Idempotency & reprocessing

1. Look up `emails` by `email_id`. Missing → INSERT. Present → UPDATE.
2. **Refresh** all system-analysis fields from the Final Decision Object.
3. **Preserve** every user-state field (`is_viewed`, `viewed_at`, `is_completed`,
   `completed_at`, `snoozed_until`) and every action's `status` and every
   deadline's monitoring fields.
4. `actions` / `deadlines` are matched by the agent's `action_id` / `deadline_id`
   (`(email_pk, ref)` is unique). New refs → INSERT. Missing refs → DELETE
   **only if the user never touched them** (a `PENDING` action / a
   non-monitored deadline); otherwise kept.
5. **Always** INSERT a new `processing_runs` row (history is never rewritten).
6. Create a `notifications` row **only** when `routing.notify` is true, priority
   ≥ HIGH, and no `PENDING`/`SENT` notification of that type already exists.

Reprocessing the same message a hundred times ⇒ **one** `emails` row, N
`processing_runs` rows, and the user's viewed/completed/snoozed state intact.

---

## Phase 10 additions

* `deadlines.is_monitoring` is now **driven**: `DeadlineMonitorService`
  auto-starts it for `should_monitor` emails and stops it on completion /
  post-deadline grace. See [[Deadline Monitoring]].
* Escalation state is **derived** from `notifications`, not stored on the email
  (no "current level" column). See [[Reminder Escalation]] · [[Reminder Schema]].
* `emails.snoozed_until` = *suppress until*; a `reminders` row = *alert me at*.
  Kept strictly separate.

## Not in scope (Phase 11)

Notification **delivery** (push / sound / vibration), a **background
scheduler** (the monitor runs on demand), Gmail label mutation, PostgreSQL
deployment. The persistence layer is engine-agnostic (SQLAlchemy 2.x);
`DATABASE_URL` switches SQLite → Postgres with no code change.
