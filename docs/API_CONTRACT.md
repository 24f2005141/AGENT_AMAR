# AGENT AMAR — Frontend API Contract

**Status:** FROZEN for Flutter integration (Phase 10.5).
**Source of truth:** the running FastAPI app (`backend/app`), verified against the
test suite (`441 passed`). This document is generated from the actual code — no
field is invented.

- **Base URL (dev):** `http://localhost:8000`
- **Content type:** `application/json` for every request body and response.
- **Auth:** none at the HTTP layer (single-user dev tool). Gmail access is
  gated server-side by the stored OAuth token — see [Auth](#1-auth--gmail-connection).
- **OpenAPI:** `GET /openapi.json` · Swagger UI `GET /docs`.

---

## Conventions

### Field Naming Rules

| Rule | Detail |
|---|---|
| Case | `snake_case` for every field, query param and JSON key. |
| Datetimes | ISO 8601 **UTC**, e.g. `2026-09-05T12:30:00Z` or `...+00:00`. Always timezone-aware. The backend stores everything as UTC; a `timezone` string field (where present) is informational only (the tz the user originally picked). |
| Datetime input | Any ISO 8601 with an explicit offset is accepted (`2026-09-02T09:00:00+05:30`); it is converted to UTC on write. A naive datetime is treated as UTC. |
| Null vs absent | Nullable fields are always present in the response with value `null` (never omitted). Optional request fields may be omitted. |
| Booleans | Independent flags, not a state enum. An email can be e.g. `is_unread=true` **and** `is_viewed=false` **and** `snoozed_until!=null` simultaneously. |
| IDs | `email_id` is a string (`"gmail_" + gmail message id`). `action_ref` / `deadline_ref` are short strings (`"act_001"`, `"dl_001"`). Reminder / notification ids are integers. |
| Numbers | `priority_score` is an integer `0–100`. `confidence` / `category_confidence` are floats `0.0–1.0`. |
| List ordering | `/emails` → `priority_score` desc, then newest. `/actions/pending` → priority desc. `/deadlines/upcoming` → soonest first. `/notifications` & `/reminders` → newest first. |
| Pagination | `limit` (default 100, max 500) + `offset` where supported. No total-count header. |
| Unknown enum values | Treat any enum below as **open** for forward-compat: render unknown values as-is, don't crash. The lists are complete as of this freeze. |

### Frozen Enums

The **actual** values in the code. Use exactly these strings.

#### `priority_level` — Priority
`CRITICAL` · `URGENT` · `HIGH` · `MEDIUM` · `LOW`
(bands: CRITICAL 90–100, URGENT 75–89, HIGH 55–74, MEDIUM 30–54, LOW 0–29)

#### `final_category` — Category (Triage, 15)
`INTERNSHIP` · `PLACEMENT` · `JOB_OPPORTUNITY` · `ASSIGNMENT` · `EXAM` ·
`FACULTY_ANNOUNCEMENT` · `REPLY_REQUIRED` · `ACADEMIC_INFORMATION` ·
`PROJECT_UPDATE` · `EVENT` · `PROMOTIONAL` · `NEWSLETTER` · `SPAM` · `SOCIAL` ·
`OTHER`

#### `action_type` / `primary_action_type` — ActionType (9)
`FORM_SUBMISSION` · `REPLY` · `REGISTRATION` · `DOCUMENT_UPLOAD` · `PAYMENT` ·
`ATTEND_EVENT` · `COMPLETE_ASSIGNMENT` · `READ_AND_ACKNOWLEDGE` · `OTHER`

#### `status` on an action — ActionStatus (API/persisted)
`PENDING` · `COMPLETED` · `DISMISSED`
> Note: the Action *Agent* has an internal `OPEN/IN_PROGRESS/DONE/SKIPPED` vocab.
> The **API only ever exposes** `PENDING/COMPLETED/DISMISSED`.

#### `proximity_bucket` — ProximityBucket
`OVERDUE` · `WITHIN_1H` · `WITHIN_24H` · `WITHIN_72H` · `LATER` · `NONE`

#### `notification_type` — NotificationType
`new_priority_email` — the initial "important email" alert (created at processing time)
`deadline_escalation` — a rung on the escalation ladder
`deadline_passed` — one-time "deadline has passed" notice
`ambiguous_deadline` — one-time "deadline unclear" notice
`user_reminder` — a user-scheduled reminder fired

#### `severity` / `reminder_level` on a notification — EscalationLevel
`NORMAL` · `REMINDER` · `URGENT` · `ALARM`
(`requires_alarm=true` only on an `ALARM`)

#### `status` on a notification — NotificationStatus
`PENDING` · `SENT` · `FAILED` · `SKIPPED`
> Phase 10.5: notifications are only ever created as `PENDING` (or `SKIPPED` when
> held by quiet hours). `SENT`/`FAILED` are reserved for the future delivery
> layer (Phase 11). Flutter should treat `PENDING` as "show me".

#### `status` on a reminder — ReminderStatus
`PENDING` · `TRIGGERED` · `CANCELLED` · `SKIPPED`

#### `reminder_type` — ReminderType
`USER_SCHEDULED` (only value)

#### `status` on a processing run / agent trace entry — AgentStatus
`ok` · `partial` · `error` · `skipped` (trace entries only)

#### `folder_label` — routing bucket
`AMAR/Opportunities` · `AMAR/Academics` · `AMAR/Replies` · `AMAR/Projects` ·
`AMAR/Events` · `AMAR/Promotions` · `AMAR/Newsletters` · `AMAR/Social` ·
`AMAR/Spam` · `AMAR/Other`

#### `decision` in a monitor-check result — MonitorDecision
`NO_CHANGE` · `REMINDER` · `URGENT` · `ALARM` · `COMPLETED` · `SNOOZED` ·
`DEADLINE_PASSED` · `AMBIGUOUS` · `QUIET_HOURS_DEFERRED` · `REMINDER_TRIGGERED` ·
`REMINDER_SKIPPED`

---

# Endpoints

## 1. Auth / Gmail connection

### `GET /api/v1/auth/google/status`
**Purpose:** Is Gmail connected? Drives the "Connect Gmail" screen.
**Request:** no params.
**Success `200`:**
```json
{ "connected": true, "provider": "gmail",
  "account_email": "student@gmail.com",
  "scopes": ["https://www.googleapis.com/auth/gmail.readonly"] }
```
| Field | Type | Null? | Meaning |
|---|---|---|---|
| `connected` | bool | no | `true` when a refresh token is stored |
| `provider` | string | no | always `"gmail"` |
| `account_email` | string | yes | connected account, `null` if not connected |
| `scopes` | string[] | no | `[]` when not connected |

### `GET /api/v1/auth/google/login`
**Purpose:** Start OAuth. **Response:** `307` redirect to Google. Open in a
browser / webview; Google redirects back to `/callback`.

### `GET /api/v1/auth/google/callback?code=…&state=…`
**Purpose:** OAuth redirect target (browser only — Flutter does not call this
directly). **Success `200`:** `{ "status": "connected", "message": "...", ... }`.

### `POST /api/v1/auth/google/disconnect`
**Purpose:** Forget stored credentials. **Success `200`:**
`{ "status": "disconnected", "provider": "gmail" }`

---

## 2. Process inbox (ingest)

> **Normal Gmail sync uses `POST /api/v1/gmail/sync`** (incremental, below).
> The endpoint in this section is a **manual / backward-compatibility** tool —
> it bulk-fetches the current unread page and can re-process historical mail, so
> the app does **not** use it for pull-to-refresh.

### `GET /api/v1/gmail/unread/process` — manual bulk ingest (compatibility)
**Purpose:** Fetch the current unread Gmail page (capped by `max_results`), run
the full agent pipeline, and **persist** each result. Idempotent (re-running
updates the same rows, never duplicates). It does **not** consult the Phase 12
sync baseline — use it only for a manual one-off sweep, not as the app's refresh
path (that is `POST /api/v1/gmail/sync`).

**Request**
| Query param | Type | Default | Notes |
|---|---|---|---|
| `max_results` | int `1–100` | `10` | how many unread messages to pull |
| `persist` | bool | `true` | keep `true` for the app |

**Success `200`** (abridged):
```json
{
  "count": 1,
  "max_results": 10,
  "unread_ids_seen": 1,
  "emails": [
    {
      "email_id": "gmail_18f0a1b2c3",
      "subject": "Summer Internship 2026 - applications open",
      "sender": { "name": "Placement Cell", "email": "placement@college.edu" },
      "received_at": "2026-08-28T03:44:22Z",
      "status": "ok",
      "final_decision": { "...": "the full Final Decision Object — see below" },
      "activity_log": "…human-readable multi-agent trace text…",
      "persisted": {
        "email_id": "gmail_18f0a1b2c3",
        "created": true,
        "is_viewed": false,
        "is_completed": false,
        "snoozed_until": null,
        "processing_run_count": 1,
        "notification_created": true
      }
    }
  ],
  "errors": []
}
```

> Flutter normally does **not** parse `final_decision` here — call
> `GET /api/v1/emails` / `GET /api/v1/emails/{id}` afterwards for the clean,
> stable persisted shape. `final_decision` is documented in
> [Appendix A](#appendix-a--final-decision-object) for completeness.

**Errors:** `401` if Gmail is not connected (see [Error responses](#error-responses)).

### `POST /api/v1/gmail/sync` — incremental sync (Phase 12)

**Purpose:** Process only **new** Gmail messages since the last sync, using the
Gmail History API. The **background scheduler runs this automatically** (default
every 120 s) — Flutter can also trigger it (e.g. on app foreground) and then
poll `GET /api/v1/emails`. Added after the 10.5 freeze; additive, no existing
endpoint changed.

**Request:** no params / body. **Errors:** `401` if Gmail not connected.

**Success `200`:**
```jsonc
// the very first call after connecting — records the baseline, processes nothing
{ "status": "baselined", "monitoring_started_at": "2026-08-29T06:00:00Z",
  "last_history_id": "184092", "processed": 0, "new_message_ids": [], "errors": [] }

// later calls
{ "status": "synced", "from_history_id": "184092", "last_history_id": "184310",
  "last_sync_at": "2026-08-29T06:02:00Z",
  "new_message_ids": ["18f...", "18a..."], "processed": 2,
  "results": [ { "email_id": "gmail_18f...", "created": true,
                 "priority_level": "URGENT", "final_category": "INTERNSHIP" } ],
  "errors": [] }
```
| Field | Type | Meaning |
|---|---|---|
| `status` | enum | `baselined` · `synced` · `history_expired_rebaselined` · `skipped_locked` |
| `processed` | int | messages successfully persisted this run |
| `new_message_ids` | string[] | raw Gmail ids seen this run |
| `results[]` | obj[] | `{email_id, created, priority_level, final_category}` per processed message |
| `last_history_id` | string | the new resume point (persisted) |
| `errors[]` | `{message_id, error}[]` | per-message failures (batch still advances) |

### `GET /api/v1/gmail/sync/status`

**Purpose:** The persistent monitoring baseline + progress. No Gmail call.
**Success `200`:**
```json
{ "monitoring": true, "account_email": "you@gmail.com",
  "monitoring_started_at": "2026-08-29T06:00:00Z",
  "last_sync_at": "2026-08-29T06:02:00Z", "last_history_id": "184310" }
```
| Field | Type | Null? | Meaning |
|---|---|---|---|
| `monitoring` | bool | no | `true` once a baseline exists |
| `account_email` | string | yes | connected address |
| `monitoring_started_at` | datetime | yes | when AGENT AMAR started watching this mailbox |
| `last_sync_at` | datetime | yes | last successful incremental sync |
| `last_history_id` | string | yes | Gmail `historyId` processed up to |

---

## 3. Smart Inbox

### `GET /api/v1/emails`
**Purpose:** The inbox list. One flat row per persisted email.

**Request**
| Query param | Type | Meaning |
|---|---|---|
| `priority` | string enum | filter by `priority_level` (`LOW`…`CRITICAL`) |
| `category` | string enum | filter by `final_category` |
| `action_required` | bool | only emails that need action |
| `needs_human_review` | bool | only low-confidence emails |
| `viewed` | bool | filter on `is_viewed` |
| `completed` | bool | filter on `is_completed` |
| `limit` | int `1–500` (def 100) | |
| `offset` | int (def 0) | |

**Success `200`** — `EmailStateOut[]`:
```json
[
  {
    "email_id": "gmail_18f0a1b2c3",
    "thread_id": "gmail_thread_18f0a1b2c3",
    "source": "gmail",
    "sender_name": "Placement Cell",
    "sender_email": "placement@college.edu",
    "subject": "Summer Internship 2026 - applications open",
    "snippet": "Please submit the application form and upload your resume by 5 September 2026",
    "received_at": "2026-08-28T03:44:22Z",
    "final_category": "INTERNSHIP",
    "category_confidence": 0.94,
    "priority_level": "URGENT",
    "priority_score": 75,
    "proximity_bucket": "LATER",
    "deadline_is_past": false,
    "primary_action_type": "DOCUMENT_UPLOAD",
    "next_deadline_at": "2026-09-05T12:30:00Z",
    "is_unread": true,
    "is_viewed": false,
    "viewed_at": null,
    "action_required": true,
    "is_completed": false,
    "completed_at": null,
    "snoozed_until": null,
    "needs_human_review": false,
    "folder_label": "AMAR/Opportunities",
    "should_notify": true,
    "should_monitor": true,
    "created_at": "2026-08-28T03:45:01Z",
    "updated_at": "2026-08-28T03:45:01Z",
    "processed_at": "2026-08-28T03:45:00Z"
  }
]
```

**Response Field Reference**
| Field | Type | Null? | Meaning |
|---|---|---|---|
| `email_id` | string | no | idempotency key / detail lookup key |
| `thread_id` | string | yes | Gmail thread id (`gmail_thread_…`) |
| `source` | string | no | always `"gmail"` currently |
| `sender_name` | string | yes | display name, may be `null` |
| `sender_email` | string | no | lower-cased address |
| `subject` | string | no | may be `""` |
| `snippet` | string | yes | ≤240-char single-line preview (Gmail's snippet, else a body head). **Not** the full body — the body is never persisted. |
| `received_at` | datetime | yes | when Gmail received the message |
| `final_category` | enum | no | see [Category](#final_category--category-triage-15) |
| `category_confidence` | float | yes | `0–1`; `null` if Triage errored |
| `priority_level` | enum | no | see [Priority](#priority_level--priority) |
| `priority_score` | int | no | `0–100` |
| `proximity_bucket` | enum | no | deadline nearness of the email's primary deadline; `NONE` if no deadline |
| `deadline_is_past` | bool | no | primary deadline already elapsed |
| `primary_action_type` | enum | yes | first blocking action's type (else first action); `null` if no actions. *Convenience projection.* |
| `next_deadline_at` | datetime | yes | earliest concrete deadline across the email; `null` if none. *Convenience projection.* |
| `is_unread` | bool | no | Gmail `UNREAD` label present (refreshed each fetch) |
| `is_viewed` | bool | no | user opened it in AMAR (user state, preserved) |
| `viewed_at` | datetime | yes | when `is_viewed` became true |
| `action_required` | bool | no | pipeline decided the user must do something |
| `is_completed` | bool | no | **derived**: every blocking action `COMPLETED`/`DISMISSED` |
| `completed_at` | datetime | yes | when `is_completed` became true |
| `snoozed_until` | datetime | yes | active snooze end; `null` = not snoozed |
| `needs_human_review` | bool | no | low confidence / unresolved conflict |
| `folder_label` | string | no | routing bucket, see [enum](#folder_label--routing-bucket) |
| `should_notify` | bool | no | routing flag (priority ≥ HIGH) |
| `should_monitor` | bool | no | routing flag → deadline monitoring eligible |
| `created_at` / `updated_at` / `processed_at` | datetime | yes | row lifecycle timestamps |

### `GET /api/v1/emails/human-review`
Same shape (`EmailStateOut[]`); only emails with `needs_human_review=true`.
Query: `limit` (1–500, def 100).

---

## 4. Email Intelligence Detail

### `GET /api/v1/emails/{email_id}`
**Purpose:** Everything AMAR knows about one email — analysis, actions,
deadlines, notifications, agent trace.

**Request:** path param `email_id` (string).

**Success `200`** — `EmailStateDetailOut` (all `EmailStateOut` fields **plus**):
```json
{
  "…all EmailStateOut fields…": "…",
  "reasoning_summary": "INTERNSHIP -> URGENT (score 75); route: store=True notify=True monitor=True label=AMAR/Opportunities; 1 conflict(s) resolved; review=False.",
  "actions": [
    {
      "action_ref": "act_001",
      "action_type": "DOCUMENT_UPLOAD",
      "description": "Upload the requested document(s) (re: Summer Internship 2026)",
      "blocking": true,
      "target_link": "https://forms.gle/abc",
      "confidence": 0.96,
      "status": "PENDING",
      "created_at": "2026-08-28T03:45:01Z",
      "completed_at": null
    }
  ],
  "deadlines": [
    {
      "deadline_ref": "dl_001",
      "deadline_datetime": "2026-09-05T12:30:00Z",
      "source_text": "5 September 2026",
      "timezone": "Asia/Kolkata",
      "date_only": false,
      "confidence": 0.95,
      "is_ambiguous": false,
      "ambiguity_reason": null,
      "is_past": false,
      "action_context": "DOCUMENT_UPLOAD",
      "related_action_ref": "act_001",
      "is_monitoring": false,
      "monitoring_started_at": null,
      "monitoring_stopped_at": null
    }
  ],
  "notifications": [
    {
      "id": 1,
      "notification_type": "new_priority_email",
      "severity": "NORMAL",
      "reminder_level": "NORMAL",
      "requires_alarm": false,
      "status": "PENDING",
      "detail": "INTERNSHIP / URGENT (score 75)",
      "deadline_id": null,
      "reminder_id": null,
      "created_at": "2026-08-28T03:45:01Z",
      "sent_at": null
    }
  ],
  "latest_processing": {
    "run_id": "run_2026-08-28T03:45:00Z_a721bd8f",
    "processed_at": "2026-08-28T03:45:00Z",
    "status": "ok",
    "pipeline_version": "0.1.0",
    "final_category": "INTERNSHIP",
    "priority_level": "URGENT",
    "priority_score": 75,
    "needs_human_review": false,
    "summary": "INTERNSHIP -> URGENT (score 75); route: …",
    "review_reasons": [],
    "conflicts_resolved": [
      { "rule": "deterministic_deadline_authoritative",
        "detail": "concrete deadline 2026-09-05T18:00:00+05:30 used as-is" }
    ],
    "agent_trace": [
      { "agent": "Mail Intake Agent", "status": "ok", "confidence": 1.0,
        "method": null, "fallback_used": false, "duration_ms": 2, "error_codes": [] },
      { "agent": "Triage Agent", "status": "ok", "confidence": 0.94,
        "method": "deterministic", "fallback_used": false, "duration_ms": 1, "error_codes": [] }
    ],
    "errors": []
  },
  "processing_run_count": 1
}
```

**Field Reference — detail-only fields**
| Field | Type | Null? | Meaning |
|---|---|---|---|
| `reasoning_summary` | string | yes | one-line human-readable summary of the routing decision (`= latest_processing.summary`). Debug-flavoured, safe to show. |
| `actions[]` | ActionState[] | no | `[]` if none |
| `deadlines[]` | DeadlineState[] | no | `[]` if none |
| `notifications[]` | NotificationState[] | no | every notification for this email, oldest first |
| `latest_processing` | ProcessingRun | yes | most recent pipeline pass; `null` only if never processed |
| `processing_run_count` | int | no | how many times this email was processed |

**`actions[]` — ActionStateOut**
| Field | Type | Null? | Meaning |
|---|---|---|---|
| `action_ref` | string | no | stable per-email id (`act_001`) — use in the complete/dismiss/reminder calls |
| `action_type` | enum | no | see [ActionType](#action_type--primary_action_type--actiontype-9) |
| `description` | string | yes | human-readable task |
| `blocking` | bool | no | must be done for the email to count as complete |
| `target_link` | string | yes | the relevant URL (form, portal…) — the "relevant links" for the detail screen |
| `confidence` | float | no | `0–1` |
| `status` | enum | no | `PENDING` / `COMPLETED` / `DISMISSED` (user state) |
| `created_at` / `completed_at` | datetime | yes | |

**`deadlines[]` — DeadlineStateOut**
| Field | Type | Null? | Meaning |
|---|---|---|---|
| `deadline_ref` | string | no | stable per-email id (`dl_001`) |
| `deadline_datetime` | datetime | yes | the concrete deadline (UTC); `null` if only an ambiguous phrase was found |
| `source_text` | string | yes | the phrase extracted from the email |
| `timezone` | string | no | tz the deadline was expressed in (informational; `deadline_datetime` is UTC) |
| `date_only` | bool | no | `true` = a date with no specific time |
| `confidence` | float | no | `0–1` |
| `is_ambiguous` | bool | no | the deadline text was unclear |
| `ambiguity_reason` | string | yes | why |
| `is_past` | bool | no | already elapsed |
| `action_context` | string | yes | what the deadline is for |
| `related_action_ref` | string | yes | links to an `actions[].action_ref` |
| `is_monitoring` | bool | no | the Deadline Monitor is watching this row |
| `monitoring_started_at` / `monitoring_stopped_at` | datetime | yes | |

**`latest_processing` / `agent_trace[]` — for the Agent Activity screen**
| Field | Type | Null? | Meaning |
|---|---|---|---|
| `run_id` | string | no | `run_<iso>_<hex>` |
| `processed_at` | datetime | no | when this pass ran |
| `status` | enum | no | `ok` / `partial` / `error` |
| `pipeline_version` | string | no | e.g. `"0.1.0"` |
| `summary` | string | yes | one-line orchestrator summary |
| `review_reasons` | string[] | no | why human review was/was not flagged |
| `conflicts_resolved` | `{rule,detail}[]` | no | cross-agent conflicts the orchestrator resolved |
| `agent_trace` | entry[] | no | per-agent execution trace, in run order |
| `errors` | `{code,message}[]` | no | pipeline-level errors (`[]` on success) |
| `processing_run_count` | int | no | (top level) total passes |

`agent_trace[]` entry:
| Field | Type | Null? | Meaning |
|---|---|---|---|
| `agent` | string | no | `Mail Intake Agent` / `Triage Agent` / `Action Agent` / `Deadline Agent` / `Priority Agent` |
| `status` | enum | no | `ok` / `partial` / `error` / `skipped` |
| `confidence` | float | yes | `0–1` |
| `method` | string | yes | free-form (`deterministic`, `deterministic+llm_adjustment`, …). **Opaque display string** — values are not fully normalised across agents. |
| `fallback_used` | bool | no | agent fell back to a safe default |
| `duration_ms` | int | yes | timing |
| `error_codes` | string[] | no | `[]` when none |

> There is no per-agent free-text "summary" in the trace — use the entry fields
> above plus the top-level `summary` / `reasoning_summary`.

### `GET /api/v1/emails/{email_id}/processing`
**Purpose:** Full processing history (Agent Activity → "all runs").
**Success `200`:** `ProcessingRunOut[]`, newest first (same shape as
`latest_processing`). `404` if the email is unknown.

---

## 5. Needs Attention

### `GET /api/v1/actions/pending`
**Purpose:** Every `PENDING` action across all emails, priority-ranked.
**Request:** `limit` (1–500, def 100).
**Success `200`** — `PendingActionOut[]` (all `ActionStateOut` fields **plus**
`email_id`, `subject`, `priority_level`):
```json
[
  {
    "action_ref": "act_001",
    "action_type": "FORM_SUBMISSION",
    "description": "Fill and submit the form (re: Summer Internship 2026)",
    "blocking": true,
    "target_link": "https://forms.gle/abc",
    "confidence": 0.91,
    "status": "PENDING",
    "created_at": "2026-08-28T03:45:01Z",
    "completed_at": null,
    "email_id": "gmail_18f0a1b2c3",
    "subject": "Summer Internship 2026 - applications open",
    "priority_level": "URGENT"
  }
]
```
> "Completed actions" for a given email come from
> `GET /api/v1/emails/{id}` → `actions[]` filtered by `status`. There is no
> global "completed actions" list.

For the Needs-Attention **email** view use `GET /api/v1/emails?action_required=true&completed=false`.

---

## 6. Deadlines

### `GET /api/v1/deadlines/upcoming`
**Purpose:** Upcoming, still-open deadlines.
**Request**
| Query param | Type | Meaning |
|---|---|---|
| `within_hours` | int `1–8760` | optional horizon; omit for all future deadlines |
| `limit` | int `1–500` (def 100) | |

**Behaviour:** returns deadlines where `deadline_datetime` is set, `is_past=false`,
and the **email is not completed**. (Completed-email deadlines are excluded — the
screen shows only things still needing action.)

**Success `200`** — `UpcomingDeadlineOut[]` (all `DeadlineStateOut` fields **plus**
`email_id`, `subject`, `priority_level`), soonest first:
```json
[
  {
    "deadline_ref": "dl_001",
    "deadline_datetime": "2026-09-05T12:30:00Z",
    "source_text": "5 September 2026",
    "timezone": "Asia/Kolkata",
    "date_only": false,
    "confidence": 0.95,
    "is_ambiguous": false,
    "ambiguity_reason": null,
    "is_past": false,
    "action_context": "DOCUMENT_UPLOAD",
    "related_action_ref": "act_001",
    "is_monitoring": true,
    "monitoring_started_at": "2026-08-28T04:00:00Z",
    "monitoring_stopped_at": null,
    "email_id": "gmail_18f0a1b2c3",
    "subject": "Summer Internship 2026 - applications open",
    "priority_level": "URGENT"
  }
]
```
> **Remaining time:** compute client-side as `deadline_datetime − now`. For the
> escalation bucket, read `proximity_bucket` from the parent email
> (`GET /api/v1/emails/{email_id}`).

---

## 7. Completion

### `PATCH /api/v1/emails/{email_id}/actions/{action_ref}/complete`
**Purpose:** Mark one action done. Recomputes the email's `is_completed`.
**Request:** path params only, no body.
**Success `200`:** the full `EmailStateDetailOut` (so the UI can re-render the
whole email). The action's `status` becomes `COMPLETED`, `completed_at` set.
**Errors:** `404` `"email or action not found"`.

### `PATCH /api/v1/emails/{email_id}/actions/{action_ref}/dismiss`
Same, but `status` → `DISMISSED` (counts as "handled" for `is_completed`).

> `is_completed` is **derived** from the blocking actions — there is no direct
> "mark whole email complete" endpoint. An email with `action_required=false`
> is never `is_completed` (nothing to complete).

---

## 8. Viewed / Snooze

### `PATCH /api/v1/emails/{email_id}/viewed`
**Purpose:** Mark the email as seen (call when the detail screen opens).
No body. **Success `200`:** `EmailStateDetailOut` (`is_viewed=true`, `viewed_at` set).
Idempotent.

### `PATCH /api/v1/emails/{email_id}/snooze`
**Purpose:** Suppress automatic escalation until a time.
**Request body:**
```json
{ "snoozed_until": "2026-09-01T16:30:00+05:30" }
```
| Field | Type | Req? | Notes |
|---|---|---|---|
| `snoozed_until` | datetime | yes | future instant; stored as UTC. `additionalProperties: false`. |
**Success `200`:** `EmailStateDetailOut` (`snoozed_until` set).

### `DELETE /api/v1/emails/{email_id}/snooze`
**Purpose:** Remove an active snooze (email becomes escalation-eligible again).
No body. **Success `200`:** `EmailStateDetailOut` (`snoozed_until=null`).
Idempotent (200 even if it was not snoozed).

> **Retrieve current snooze state:** it's the `snoozed_until` field on
> `GET /api/v1/emails` / `GET /api/v1/emails/{id}`. Expiry is automatic — the
> monitor ignores a snooze once `now ≥ snoozed_until` (the field is not cleared).
> **Snooze ≠ reminder** — see §10.

---

## 9. Notifications

### `GET /api/v1/notifications`
**Purpose:** The notification feed / in-app alarm source.
**Request**
| Query param | Type | Meaning |
|---|---|---|
| `status` | enum | `PENDING` / `SENT` / `FAILED` / `SKIPPED` |
| `severity` | enum | `NORMAL` / `REMINDER` / `URGENT` / `ALARM` |
| `type` | enum | a `notification_type` value |
| `email_id` | string | notifications for one email |
| `requires_alarm` | bool | `true` → only alarm-level events (drives the alarm UI) |
| `created_after` | datetime | only newer than this |
| `limit` / `offset` | int | def 100 / 0 |

**Success `200`** — `NotificationOut[]`, newest first:
```json
[
  {
    "id": 12,
    "email_id": "gmail_18f0a1b2c3",
    "notification_type": "deadline_escalation",
    "severity": "ALARM",
    "reminder_level": "ALARM",
    "requires_alarm": true,
    "status": "PENDING",
    "detail": "CRITICAL deadline in 4m; unviewed",
    "deadline_id": 3,
    "reminder_id": null,
    "created_at": "2026-09-05T11:56:00Z",
    "sent_at": null
  }
]
```
**Response Field Reference**
| Field | Type | Null? | Meaning |
|---|---|---|---|
| `id` | int | no | notification id (use in `/notifications/{id}`) |
| `email_id` | string | yes | related email (`null` only if the email row was deleted) |
| `notification_type` | enum | no | see [NotificationType](#notification_type--notificationtype) |
| `severity` | enum | no | `NORMAL`/`REMINDER`/`URGENT`/`ALARM` — the display urgency |
| `reminder_level` | enum | yes | same value as `severity` for escalations; `NORMAL` for `new_priority_email`/`user_reminder` |
| `requires_alarm` | bool | no | `true` ⇒ the client should raise a full alarm UI (sound/full-screen). Backend only flags it. |
| `status` | enum | no | `PENDING` = unshown. `SKIPPED` = held by quiet hours. |
| `detail` | string | yes | the message body. **There is no separate `title`** — derive one from `notification_type` + `severity`. |
| `deadline_id` | int | yes | related `deadlines` row (for `deadline_*` types) — resolve via the email detail |
| `reminder_id` | int | yes | related `reminders` row (for `user_reminder`) |
| `created_at` | datetime | no | |
| `sent_at` | datetime | yes | always `null` in Phase 10.5 (no delivery yet) |

### `GET /api/v1/notifications/{notification_id}`
**Success `200`:** one `NotificationOut`. `404` `"notification not found"`.

> **Marking notifications read/sent is not available yet** (Phase 11 delivery
> layer). Flutter should track "seen" locally or simply show `PENDING` ones.

### `POST /api/v1/monitor/deadlines/check` — run the monitor (manual)
**Purpose:** Evaluate every monitored deadline + fire due reminders, creating
notification rows.
**Phase 11B.1:** a **background scheduler now does this automatically** on an
interval (default 60 s). Flutter does **not** need to call this — just poll
`GET /api/v1/notifications`. This endpoint stays for manual/debug use and its
contract is unchanged.
**Request body (optional):**
```json
{ "now": "2026-09-05T11:56:00Z" }
```
`now` overrides "current time" (testing/replay). Omit the body entirely for real time.
**Success `200`** — `MonitorCheckResult`:
```json
{
  "checked_at": "2026-09-05T11:56:00Z",
  "deadlines_evaluated": 4,
  "reminders_evaluated": 1,
  "notifications_created": 2,
  "results": [
    { "email_id": "gmail_18f0a1b2c3", "deadline_ref": "dl_001",
      "decision": "ALARM", "reason": "deadline in 4m, priority CRITICAL",
      "notification_id": 12, "requires_alarm": true },
    { "email_id": "gmail_abc", "deadline_ref": null,
      "decision": "REMINDER_TRIGGERED", "reason": "user-scheduled reminder fired",
      "notification_id": 13, "requires_alarm": false }
  ]
}
```
| Field | Type | Null? | Meaning |
|---|---|---|---|
| `checked_at` | datetime | no | the instant used |
| `deadlines_evaluated` / `reminders_evaluated` / `notifications_created` | int | no | counters |
| `results[].email_id` | string | no | |
| `results[].deadline_ref` | string | yes | `null` for reminder rows |
| `results[].decision` | enum | no | see [MonitorDecision](#decision-in-a-monitor-check-result--monitordecision) |
| `results[].reason` | string | no | human-readable |
| `results[].notification_id` | int | yes | set when this decision created a notification |
| `results[].requires_alarm` | bool | no | |

### `GET /api/v1/monitor/status` — background scheduler status
**Purpose:** Is the automatic monitoring loop running? (Phase 11B.1. Added
after the 10.5 freeze; read-only, additive — no existing contract changed.)
**Request:** no params.
**Success `200`:**
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
| Field | Type | Null? | Meaning |
|---|---|---|---|
| `scheduler` | string | no | `"running"` / `"stopped"` |
| `enabled` | bool | no | `SCHEDULER_ENABLED` |
| `started_at` | datetime | yes | when the scheduler started |
| `deadline_check_interval_seconds` / `reminder_check_interval_seconds` | int | no | configured intervals |
| `last_deadline_check` / `last_reminder_check` | datetime | yes | `null` before the first cycle |
| `deadline_cycles` / `reminder_cycles` | int | no | completed cycles this process |
| `deadline_failures` / `reminder_failures` | int | no | cycles that raised (retried next tick) |
| `last_error` | string | yes | last failure message, if any |

---

## 10. Reminders (user-scheduled)

> **Snooze vs reminder:** *snooze* (`snoozed_until` on the email) = "don't alert
> me until T". A *reminder* = "**do** alert me at T". Many reminders per email;
> optionally tied to an action. A reminder never disables deadline-alarm
> protection.

### `POST /api/v1/emails/{email_id}/reminders`
**Request body:**
```json
{ "reminder_at": "2026-09-02T09:00:00+05:30",
  "action_ref": "act_001",
  "note": "apply after class" }
```
| Field | Type | Req? | Notes |
|---|---|---|---|
| `reminder_at` | datetime | yes | must be in the future and ≤ 365 days out |
| `action_ref` | string | no | must be an existing `actions[].action_ref` on this email |
| `note` | string | no | shown when the reminder fires |
`additionalProperties: false`.

**Success `201`** — `ReminderOut`:
```json
{
  "id": 1,
  "email_id": "gmail_18f0a1b2c3",
  "action_ref": "act_001",
  "reminder_at": "2026-09-02T03:30:00Z",
  "reminder_type": "USER_SCHEDULED",
  "status": "PENDING",
  "timezone": "UTC+05:30",
  "note": "apply after class",
  "created_at": "2026-08-28T04:10:00Z",
  "triggered_at": null,
  "cancelled_at": null
}
```
**Errors:** `404` `"email not found"` · `400` `"reminder_at must be in the future"` /
`"reminder_at is more than 365 days away"` / `"action 'act_9' not found on this email"`.

### `GET /api/v1/emails/{email_id}/reminders`
**Success `200`:** `ReminderOut[]` for that email (scheduled time asc). `404` if email unknown.

### `GET /api/v1/reminders`
**Purpose:** The **Reminders screen** — every reminder across all emails.
**Request:** `status` (`PENDING`/`TRIGGERED`/`CANCELLED`/`SKIPPED`), `limit` (1–500), `offset`.
**Success `200`:** `ReminderOut[]`, newest scheduled first, each with `email_id`.

### `DELETE /api/v1/emails/{email_id}/reminders/{reminder_id}`
**Purpose:** Cancel a reminder.
**Success `200`** — the `ReminderOut` with `status="CANCELLED"`, `cancelled_at` set
(no-op if it was already `TRIGGERED`). `404` `"reminder not found"`.

**`ReminderOut` Field Reference**
| Field | Type | Null? | Meaning |
|---|---|---|---|
| `id` | int | no | reminder id |
| `email_id` | string | yes | owning email |
| `action_ref` | string | yes | linked action, if any |
| `reminder_at` | datetime | no | when it fires (UTC) |
| `reminder_type` | enum | no | always `USER_SCHEDULED` |
| `status` | enum | no | `PENDING`→`TRIGGERED`\|`CANCELLED`\|`SKIPPED` (`SKIPPED` = email/action already done at fire time) |
| `timezone` | string | no | tz label the user picked (informational) |
| `note` | string | yes | |
| `created_at` / `triggered_at` / `cancelled_at` | datetime | yes | lifecycle |

---

# Frontend Coverage Matrix

| Flutter feature / data need | Endpoint(s) | Status |
|---|---|---|
| **Smart Inbox** — list, sender, subject, category, priority, score, action-required, viewed/unread, completion, snooze | `GET /api/v1/emails` | **READY** |
| Smart Inbox — snippet / preview | `GET /api/v1/emails` → `snippet` | **ADDED** (new `snippet` field) |
| Smart Inbox — deadline + action type per row | `GET /api/v1/emails` → `next_deadline_at`, `primary_action_type` | **ADDED** (convenience projections) |
| **Needs Attention** — emails needing action | `GET /api/v1/emails?action_required=true&completed=false` | **READY** |
| Needs Attention — pending actions (global) | `GET /api/v1/actions/pending` | **READY** |
| Needs Attention — completed actions | `GET /api/v1/emails/{id}` → `actions[]` (filter `status`) | **READY** (per-email) |
| Needs Attention — priority / deadline / urgency | fields on the rows above | **READY** |
| **Deadlines** — upcoming, datetime, completion, priority | `GET /api/v1/deadlines/upcoming` | **READY** |
| Deadlines — remaining time | client-computed from `deadline_datetime` | **READY** |
| **Email Detail** — full analysis, category, priority, score, confidence, action, deadline, links | `GET /api/v1/emails/{id}` | **READY** |
| Email Detail — reasoning summary | `GET /api/v1/emails/{id}` → `reasoning_summary` | **ADDED** (persist + hoist orchestrator summary) |
| Email Detail — full email body | *(not persisted by design)* | **NOT PLANNED** — see [Known limitations](#known-limitations) |
| **Agent Activity** — trace, agent names, status, timing, errors | `GET /api/v1/emails/{id}` → `latest_processing.agent_trace` · `GET /api/v1/emails/{id}/processing` | **READY** |
| Agent Activity — per-agent free-text summary | *(only entry fields + overall `summary`)* | **PARTIAL** — see [Known limitations](#known-limitations) |
| **Reminders** — create | `POST /api/v1/emails/{id}/reminders` | **READY** |
| Reminders — list (per email) | `GET /api/v1/emails/{id}/reminders` | **READY** |
| Reminders — list (all, for the Reminders screen) | `GET /api/v1/reminders` | **ADDED** |
| Reminders — cancel | `DELETE /api/v1/emails/{id}/reminders/{rid}` | **READY** |
| Reminders — status / scheduled datetime / related email+action | `ReminderOut` (+ new `email_id`) | **ADDED** (`email_id` field) |
| **Snooze** — create/update | `PATCH /api/v1/emails/{id}/snooze` | **READY** |
| Snooze — retrieve current state | `snoozed_until` on the email | **READY** |
| Snooze — remove / expire | `DELETE /api/v1/emails/{id}/snooze` (remove) · auto-expiry (monitor) | **ADDED** (`DELETE`) |
| **Notifications** — list pending, type, severity, message, related email | `GET /api/v1/notifications` | **READY** |
| Notifications — related deadline / reminder | `NotificationOut.deadline_id` / `reminder_id` | **ADDED** |
| Notifications — title | derive from `notification_type` + `severity` (`detail` is the message) | **READY** (no `title` field — client-derived) |
| Notifications — creation time / status | `created_at` / `status` | **READY** |
| Notifications — mark read/sent | *(Phase 11 delivery layer)* | **NOT PLANNED** for 10.5 |
| **In-app deadline alarm** — which notifications need an alarm | `GET /api/v1/notifications?requires_alarm=true` | **READY** |
| Trigger monitor / escalation on demand | `POST /api/v1/monitor/deadlines/check` | **READY** |
| Automatic monitoring (no client action) | background scheduler (Phase 11B.1) · `GET /api/v1/monitor/status` | **READY** |
| **Completion** — mark action complete, reflect state | `PATCH …/actions/{ref}/complete` · `…/dismiss` | **READY** |
| **Connect Gmail** — status / login / disconnect | `GET/POST /api/v1/auth/google/*` | **READY** |
| Pull-to-refresh / incremental Gmail sync | `POST /api/v1/gmail/sync` then refresh `GET /api/v1/emails` etc. | **READY** |
| Gmail monitoring state | `GET /api/v1/gmail/sync/status` | **READY** |
| Manual bulk ingest (one-off, not pull-to-refresh) | `GET /api/v1/gmail/unread/process` | **READY** (compat only) |

**Legend:** READY = worked as-is · ADDED = minimal additive change in Phase 10.5 ·
PARTIAL = usable, some sub-field not available · NOT PLANNED = deliberately out of
scope for this phase.

---

# New in Phase 10.5

All changes are **additive** — no field renamed, removed, or retyped; no business
logic changed; no existing test modified.

### New endpoints
| Endpoint | Why |
|---|---|
| `GET /api/v1/reminders` | The Reminders screen needs every reminder; only a per-email list existed. |
| `DELETE /api/v1/emails/{email_id}/snooze` | The Snooze interaction needs an explicit "un-snooze"; no mechanism existed. |

### New response fields
| Model | Field | Why |
|---|---|---|
| `EmailStateOut` / `…DetailOut` | `snippet` | inbox preview line (was not persisted anywhere) |
| `EmailStateOut` / `…DetailOut` | `primary_action_type`, `next_deadline_at` | inbox rows need action-type + deadline without an N+1 detail fetch |
| `EmailStateDetailOut` | `reasoning_summary` | Email Detail "reasoning summary" (orchestrator produced it, wasn't persisted) |
| `ProcessingRunOut` | `summary` | same, per historical run |
| `NotificationOut` / `NotificationStateOut` | `deadline_id`, `reminder_id` | link an alarm/notification to its deadline/reminder |
| `NotificationStateOut` | `id`, `severity`, `requires_alarm` | align the embedded notification shape with the standalone one |
| `ReminderOut` | `email_id` | required by the global reminders list |

### DB (additive columns, auto-created at startup)
`emails.snippet` (Text, nullable) · `processing_runs.summary` (Text, nullable).

### Unchanged
Gmail OAuth & fetch, Mail Intake / Triage / Action / Deadline / Priority agents,
AMAR Orchestrator, the Final Decision Object, `PersistenceService` idempotency &
reprocessing rules, `DeadlineMonitorService` escalation logic, `ReminderService`
validation, `escalation_policy` ladders, all existing endpoints & their existing
fields, all 435 pre-existing tests.

---

# Error responses

| Status | When | Body shape |
|---|---|---|
| `400` | invalid reminder time / unknown `action_ref` in a reminder | `{ "detail": "<message>" }` |
| `401` | a Gmail endpoint called while Gmail is not connected | `{ "error": "GmailNotConnectedError", "detail": "…", "provider": "gmail" }` |
| `404` | unknown `email_id` / `action_ref` / `reminder_id` / `notification_id` | `{ "detail": "email not found" }` (message varies) |
| `422` | request body / query param fails validation | `{ "detail": [ { "type": "...", "loc": ["body","reminder_at"], "msg": "Field required", "input": {} } ] }` |
| `5xx` | Gmail upstream / unexpected | `{ "error": "<ClassName>", "detail": "…", "provider": "gmail" }` for Gmail errors |

Flutter should branch on **status code first**, then read `detail` (string for
4xx business errors; array for 422). The `401` shape uses `error`+`detail`, not a
bare `detail`.

---

# Sample integration flow

Real request/response JSON (mocked Gmail, deterministic).

**1 — Pull-to-refresh: incremental Gmail sync, then reload data**
```
POST /api/v1/gmail/sync
→ 200  { "status": "synced", "from_history_id": "184092",
         "last_history_id": "184310", "processed": 1,
         "new_message_ids": ["gmail_contract1"],
         "results": [ { "email_id": "gmail_contract1", "created": true,
                        "priority_level": "URGENT" } ], "errors": [] }
# then GET /api/v1/emails (+ /actions/pending, /deadlines/upcoming,
#                            /reminders, /notifications) to refresh the UI
```

**2 — Load the Smart Inbox**
```
GET /api/v1/emails
→ 200
[ { "email_id": "gmail_contract1",
    "sender_name": "Placement Cell", "sender_email": "placement@college.edu",
    "subject": "Summer Internship 2026 - applications open",
    "snippet": "Please submit the application form and upload your resume by 5 September 2026",
    "final_category": "INTERNSHIP", "priority_level": "URGENT", "priority_score": 75,
    "action_required": true, "primary_action_type": "DOCUMENT_UPLOAD",
    "next_deadline_at": "2026-09-05T12:30:00Z",
    "is_unread": true, "is_viewed": false, "is_completed": false, "snoozed_until": null } ]
```

**3 — Open the email (detail + trace)**
```
PATCH /api/v1/emails/gmail_contract1/viewed          → 200  (is_viewed: true)
GET   /api/v1/emails/gmail_contract1
→ 200
{ "email_id": "gmail_contract1", "priority_level": "URGENT", "priority_score": 75,
  "category_confidence": 0.94,
  "reasoning_summary": "INTERNSHIP -> URGENT (score 75); route: store=True notify=True monitor=True label=AMAR/Opportunities; 1 conflict(s) resolved; review=False.",
  "actions": [ { "action_ref": "act_001", "action_type": "DOCUMENT_UPLOAD",
                 "blocking": true, "target_link": "https://forms.gle/abc",
                 "status": "PENDING" } ],
  "deadlines": [ { "deadline_ref": "dl_001", "deadline_datetime": "2026-09-05T12:30:00Z",
                   "is_ambiguous": false, "is_past": false, "is_monitoring": false } ],
  "notifications": [ { "id": 1, "notification_type": "new_priority_email",
                       "severity": "NORMAL", "requires_alarm": false, "status": "PENDING",
                       "deadline_id": null, "reminder_id": null } ],
  "latest_processing": { "run_id": "run_2026-…_a721bd8f", "status": "ok",
                         "agent_trace": [ { "agent": "Mail Intake Agent", "status": "ok", "duration_ms": 2 },
                                          { "agent": "Triage Agent", "status": "ok", "confidence": 0.94 },
                                          { "agent": "Priority Agent", "status": "ok", "confidence": 0.9 } ] },
  "processing_run_count": 1 }
```

**4 — "Remind me" bottom sheet**
```
POST /api/v1/emails/gmail_contract1/reminders
     { "reminder_at": "2026-09-02T09:00:00+05:30", "action_ref": "act_001" }
→ 201  { "id": 1, "email_id": "gmail_contract1", "action_ref": "act_001",
         "reminder_at": "2026-09-02T03:30:00Z", "status": "PENDING",
         "reminder_type": "USER_SCHEDULED" }

GET /api/v1/reminders
→ 200  [ { "id": 1, "email_id": "gmail_contract1", "status": "PENDING",
           "reminder_at": "2026-09-02T03:30:00Z" } ]
```

**5 — Later: run the monitor, then read notifications**
```
POST /api/v1/monitor/deadlines/check
→ 200  { "checked_at": "2026-09-05T11:56:00Z", "deadlines_evaluated": 1,
         "reminders_evaluated": 0, "notifications_created": 1,
         "results": [ { "email_id": "gmail_contract1", "deadline_ref": "dl_001",
                        "decision": "URGENT", "notification_id": 5, "requires_alarm": false } ] }

GET /api/v1/notifications?email_id=gmail_contract1
→ 200
[ { "id": 5, "email_id": "gmail_contract1", "notification_type": "deadline_escalation",
    "severity": "URGENT", "reminder_level": "URGENT", "requires_alarm": false,
    "status": "PENDING", "detail": "URGENT deadline in 3h; unviewed",
    "deadline_id": 1, "reminder_id": null, "created_at": "2026-09-05T11:56:00Z" },
  { "id": 1, "email_id": "gmail_contract1", "notification_type": "new_priority_email",
    "severity": "NORMAL", "status": "PENDING" } ]
```

**6 — Complete the action**
```
PATCH /api/v1/emails/gmail_contract1/actions/act_001/complete
→ 200  { "email_id": "gmail_contract1", "is_completed": false,   // act_002 still pending
         "actions": [ { "action_ref": "act_001", "status": "COMPLETED",
                        "completed_at": "2026-09-05T12:00:00Z" }, … ] }
```

---

# Appendix A — Final Decision Object

`emails[].final_decision` in the `/process` response. Flutter does **not** need to
parse this — the persisted `/api/v1/emails/{id}` shape is the stable surface — but
it is documented here since `/process` returns it.

| Field | Type | Meaning |
|---|---|---|
| `email_id`, `thread_id`, `source` | string | identity |
| `final_category` | enum | category |
| `category_confidence` | float\|null | |
| `action_required` | bool | |
| `primary_action_type` | enum\|null | |
| `actions[]` | `{action_id, action_type, action_description, blocking, confidence, target_link, raw_deadline_hint}` | |
| `deadline` | string\|null | primary normalised deadline (ISO, original offset) |
| `deadline_ambiguous`, `deadline_is_past` | bool | |
| `deadlines[]` | `{deadline_id, raw_deadline_text, normalized_deadline, timezone, date_only, ambiguity_flag, ambiguity_reason, is_past, confidence, action_context, related_action_id}` | |
| `proximity_bucket` | enum | |
| `priority_level` | enum | |
| `priority_score` | int | |
| `routing` | `{store, notify, monitor, folder_label}` | |
| `needs_human_review` | bool | |
| `review_reasons[]` | string[] | |
| `conflicts_resolved[]` | `{rule, detail}[]` | |
| `agent_trace[]` | `{agent, status, confidence, method, fallback_used, duration_ms, error_codes}[]` | |

---

# Known limitations (documented, not gaps to fix in 10.5)

1. **Full email body is not available.** Phase 9 deliberately never persists the
   body. `snippet` (≤240 chars) is the only preview. A future
   `GET /api/v1/emails/{id}/body` (live Gmail fetch) would be a separate feature.
2. **No per-agent free-text summary** in `agent_trace`. Only structured fields
   (`status`, `method`, `confidence`, `duration_ms`, `error_codes`) plus the
   overall `reasoning_summary`. The per-agent envelope summaries are not persisted.
3. **`agent_trace[].method`** is a free-form string and not fully normalised
   across agents (e.g. Triage may emit `"ClassificationMethod.DETERMINISTIC"`
   while others emit `"deterministic"`). Treat it as an opaque label.
4. **Notifications have no `title`** and cannot be marked read/sent — the delivery
   layer (Phase 11B.2) owns that. `detail` is the message; `status` stays `PENDING`.
5. **`reasoning_summary`** is a routing-decision one-liner (`"INTERNSHIP -> URGENT
   (score 75); route: …"`), useful but not a polished user-facing paragraph.
6. **No global "completed actions" list** — query per email via
   `GET /api/v1/emails/{id}`.
7. **`/deadlines/upcoming` hides completed-email deadlines** — by design; there is
   no flag to include them.
8. **`GET /api/v1/gmail/unread/process` is a `GET` that mutates** (pre-existing
   convention). Safe to call repeatedly (idempotent).
9. **Background scheduler is single-instance** (Phase 11B.1) — monitoring now
   advances automatically (default every 60 s), but only one backend process
   should run it. See `docs/BACKGROUND_SCHEDULER.md`.
10. **Gmail sync is incremental from a persistent baseline** (Phase 12) — the
    historical unread inbox is not ingested; only mail arriving after connect.
    After an outage longer than Gmail's history window (~1 week), the sync
    re-baselines and skips the gap. `GET /api/v1/gmail/unread/process` still
    sweeps the current unread page on demand (manual / compat only — not the
    app's pull-to-refresh path). See `docs/GMAIL_SYNC.md`.
11. **The Flutter app never calls `GET /api/v1/gmail/unread/process`** (Phase 13)
    — pull-to-refresh is `POST /api/v1/gmail/sync` then a data refresh; the
    backend scheduler owns continuous monitoring, so the app has no Gmail poll
    timer.

---

*Frozen at Phase 10.5; Phase 11B.1 added `GET /api/v1/monitor/status` +
automatic monitoring; Phase 12 added `POST /api/v1/gmail/sync` +
`GET /api/v1/gmail/sync/status` + incremental sync; Phase 13 pointed the Flutter
pull-to-refresh at `POST /api/v1/gmail/sync` (all additive — no existing
endpoint, method, or field changed). Backend test suite: `python -m pytest` →
**518 passed**. Flutter: `flutter test` → **38 passed**.*
