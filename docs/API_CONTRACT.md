# AGENT AMAR — Frontend API Contract

**Status:** FROZEN for Flutter integration (Phase 10.5); auth in Phase 15; push devices in Phase 16.
**Source of truth:** the running FastAPI app (`backend/app`), verified against the
test suite (`609 passed`). This document is generated from the actual code — no
field is invented.

- **Base URL (dev):** `http://localhost:8000`
- **Content type:** `application/json` for every request body and response.
- **Auth (Phase 15):** every `/api/v1/*` data route requires
  `Authorization: Bearer <session_token>` and is scoped to that user. Open
  routes: `GET /health`, `GET /`, `GET /api/v1/monitor/status`,
  `GET /api/v1/system/status`, and the
  `…/auth/google/start` · `…/login` · `…/callback` · `…/session` ·
  `…/auth/session/exchange` flow. A missing
  / expired / revoked token returns **401** with `error: "AuthRequiredError"`;
  a valid session whose *Gmail* token is gone returns **401** with
  `error: "GmailNotConnectedError"` (reconnect Gmail, no re-login). See
  [Authentication](#0-authentication-phase-15).
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

#### `final_category` — Category (Triage, 15) — *secondary metadata*
`INTERNSHIP` · `PLACEMENT` · `JOB_OPPORTUNITY` · `ASSIGNMENT` · `EXAM` ·
`FACULTY_ANNOUNCEMENT` · `REPLY_REQUIRED` · `ACADEMIC_INFORMATION` ·
`PROJECT_UPDATE` · `EVENT` · `PROMOTIONAL` · `NEWSLETTER` · `SPAM` · `SOCIAL` ·
`OTHER`

#### `primary_category` — the ONE inbox bucket (mutually exclusive) ⭐
`REPLY_REQUIRED` · `ACTION_REQUIRED` · `IMPORTANT` · `LOW_PRIORITY`

The backend collapses `final_category` + `action_required` + `priority_level`
into **exactly one** of these, priority order `REPLY_REQUIRED` > `ACTION_REQUIRED`
> `IMPORTANT` > `LOW_PRIORITY`. **The four inbox sections filter on this field**,
so an email is never listed twice — in particular a *Reply Required* email is
**not** also *Action Required* (even though `action_required` is still `true` and
a `REPLY` action still exists as secondary metadata). Filter with
`GET /api/v1/emails?primary_category=REPLY_REQUIRED` etc.
- `REPLY_REQUIRED` — `final_category == REPLY_REQUIRED`, or a `REPLY` action present.
- `ACTION_REQUIRED` — a non-reply action the user must take (`action_required == true`).
- `IMPORTANT` — `priority_level ∈ {HIGH, URGENT, CRITICAL}`, nothing actionable.
- `LOW_PRIORITY` — everything else.

It is set on every pipeline pass and **backfilled on startup** for rows stored
before the field existed (recomputed from the persisted `final_category` /
action rows / `priority_level` — same rule, no re-run of the agents).

**User correction (Phase 18).** `primary_category` is the **current** bucket: the
user's manual correction when they made one (via
`POST /api/v1/emails/{id}/classification-feedback`), else the automated
derivation. Two companion read-only fields:
* `auto_primary_category` — the latest **automated** derivation, kept even after a
  correction (the original prediction, for training / audit / a future "revert").
* `primary_category_source` — `"auto"` | `"user"`. When `"user"`, reprocessing
  refreshes `auto_primary_category` but does **not** move `primary_category`.

#### The homepage is an attention dashboard — `is_active` / `?active=`

AGENT AMAR is an action-management system, not a mail client: the homepage shows
only what **currently needs the user**, never every email ever processed. Each
`EmailStateOut` carries a derived **`is_active`** flag, and `GET /api/v1/emails`
takes **`?active=true`** (the homepage feed) / **`?active=false`** (the resolved /
acknowledged complement, for a history view). The rule:

| primary_category | stays on the feed until… | opening it (`is_viewed`) removes it? |
|---|---|---|
| `REPLY_REQUIRED` | reply sent / `is_completed` | **no** |
| `ACTION_REQUIRED` | action completed / `is_completed` | **no** |
| `IMPORTANT` (nothing actionable) | opened / acknowledged | **yes** |
| `LOW_PRIORITY` | opened / acknowledged | **yes** |

A completed email, or one snoozed into the future, is never active. Nothing is
deleted — `GET /api/v1/emails` with no `active` filter and
`GET /api/v1/emails/{id}` still return it.

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

## 0. Authentication (Phase 15)

"Continue with Google" is one OAuth grant covering `openid email profile`, `gmail.readonly`
**and** `gmail.send` (send a user-approved reply — it cannot read/modify/delete
mail; see §12) — signing in *is* connecting Gmail. The client gets back an opaque
application **session token** (a bearer); the Google client secret and OAuth
refresh tokens never leave the server. A user who connected before `gmail.send`
existed keeps working for everything except sending a reply, until they reconnect
once.

**Public callback / URLs.** The client talks to the backend at a single
configured origin — `--dart-define=API_BASE_URL` on Flutter, `API_PUBLIC_BASE_URL`
on the backend — LAN (`http://…:8000`) in dev, an **HTTPS tunnel** in prod. Every
request (including `…/auth/google/*`) is a relative path on that origin. Google's
redirect target is `{GOOGLE_REDIRECT_URI}` — set explicitly, or derived as
`{API_PUBLIC_BASE_URL}/api/v1/auth/google/callback` — and must **exactly** match
an Authorized redirect URI on the OAuth client. The consent screen runs in a
secure system browser / Custom Tab (never an in-app WebView), so for a
phone/tablet the callback origin must be a real HTTPS host reachable from that
device, **never `localhost`**. FastAPI needs no proxy-header config: it never
derives URLs from the inbound request. See `docs/OAUTH_TUNNEL_TESTING.md`.

**Mobile sign-in flow (Phase 17 — deep-link handoff, the primary path).** Google
redirects to the **backend** callback (never a custom scheme). The backend
completes OAuth server-side, then `302`s the browser to the app's deep link with
a **short-lived, single-use handoff code** — no token ever appears in the URL:

```
App  "Continue with Google"
 → secure system browser / Custom Tab   (POST /api/v1/auth/google/start → authorization_url)
 → Google consent
 → GET {GOOGLE_REDIRECT_URI}?code=…&state=…          (backend: validate state → exchange code
                                                      → upsert user → store Gmail creds
                                                      → mint one-time handoff code)
 → 302  agentamar://auth/callback?code=<handoff>&state=<state>
 → app receives the deep link (running / backgrounded / cold start)
 → POST /api/v1/auth/session/exchange { code, state }   → { session_token, user }
 → app stores the bearer in the Keychain/Keystore and enters Home
```

The user never closes the browser manually, never sees `localhost`, and there is
no polling. `APP_AUTH_CALLBACK_URL` (default `agentamar://auth/callback`) is the
deep-link target; a future HTTPS Android App Link (`https://app.<domain>/auth/callback`)
is a config change only.

**Session lifecycle (client):**
1. On start, read the stored token. None → Login. Present → `GET /api/v1/auth/me`.
2. `200` → restore the user, go to Home. `401` → the session is gone (backend DB
   reset / migration / revoked / expired / user deleted): **delete the stored
   token, clear user + Gmail state, go to Login** — no crash, no retry loop.
3. Any protected request that returns `401 AuthRequiredError` mid-session does the
   same, handled **once** centrally even if several requests fail together.
4. `POST /api/v1/auth/logout` then clear local state; the old token is never sent
   again.

### `POST /api/v1/auth/google/start` *(open)*
**Success `200`:** `{ "authorization_url": "https://accounts.google.com/…",
"flow_id": "<nonce>" }`. Open `authorization_url` in a secure system browser /
Custom Tab. The backend then 302s the browser to the app deep link; the app
completes via `/session/exchange`. (`flow_id` is retained only for the
manual-browser `/session` poll fallback.)

### `GET /api/v1/auth/google/login` *(open)*
`307` redirect straight to Google (manual browser). The callback then renders a
page and the token is collected via `/session`.

### `GET /api/v1/auth/google/callback?code=…&state=…` *(open, browser only)*
Google's redirect target. **Validates `state`** against a flow started by
`/start` or `/login` (an unknown/expired state is rejected — CSRF /
session-fixation protection). Exchanges the code server-side, upserts the `User`,
stores **that user's** Gmail credentials (keyed by the resolved app user id —
never overwriting another user), then:
* **app flow** (`/start`): mints a one-time [handoff code](#handoff) and
  `302`s to `{APP_AUTH_CALLBACK_URL}?code=<handoff>&state=<state>`. On failure it
  302s with `?error=signin_failed` (or `?error=expired_state`).
* **manual browser** (`/login`): mints a session, parks it for `/session`, and
  renders a "return to the app" page.

The Google client secret, OAuth access/refresh tokens, and the app session token
**never** appear in the callback URL.

### `POST /api/v1/auth/session/exchange` *(open)* <a id="handoff"></a>
Swap a one-time browser→app **handoff code** for the application session.

**Request:** `{ "code": "<handoff>", "state": "<oauth state, optional>" }`

The code is cryptographically random, stored only as `sha256`, short-lived
(`AUTH_HANDOFF_TTL_SECONDS`, default 120s), **single-use** (consumed atomically),
and bound to the OAuth `state`. It carries no token — only a reference to the
user who just authenticated.

**Success `200`:** `{ "status": "ready", "session_token": "<bearer>",
"user": { "id", "google_email", "display_name" } }` — the same session shape as
`/session`; the bearer is opaque and hashed in `app_sessions`.

**`400 { "error": "invalid_handoff", "detail": "…" }`** — unknown / expired /
already-used code, or a `state` mismatch. Replay of a consumed code always 400s.

### `GET /api/v1/auth/google/session?flow_id=…` *(open — manual-browser fallback)*
Poll after the `/login` browser step. `202 {"status":"pending"}` until ready,
then **once** `200 { "status": "ready", "session_token": "…",
"user": { … } }`, then `410 {"status":"expired"}`. The mobile app uses
`/session/exchange` instead and does not poll.

### `GET /api/v1/auth/me` *(bearer)*
`200 { "user": { "id", "google_email", "display_name" }, "gmail_connected": bool }`

### `POST /api/v1/auth/logout` *(bearer)*
Revokes every session for the user. `200 { "status": "logged_out" }`.

### `GET /api/v1/auth/google/status` *(bearer)*
The **current user's** Gmail connection. `200 { "connected": bool,
"provider": "gmail", "account_email": string|null, "scopes": string[] }`
(`scopes` lists the granted Gmail scopes when connected — `["…/gmail.readonly",
"…/gmail.send"]`, or just `["…/gmail.readonly"]` for a connection made before the
reply feature — else `[]`).

### `POST /api/v1/auth/google/disconnect` *(bearer)*
Forgets the current user's Gmail credentials. App data + session are kept.
`200 { "status": "disconnected", "provider": "gmail" }`.

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
Gmail History API.

**The client does not need to call this to receive new mail.** The backend
scheduler runs the same incremental sync **automatically for every connected
user**, continuously, every `GMAIL_SYNC_INTERVAL_SECONDS` (default `300`;
configurable, e.g. `900` for 15 min). Each cycle: History-API diff → local ML
classifier → LLM only per the existing confidence/fallback logic → persist →
create notification (existing rules) → FCM push (existing dedup). New mail
therefore appears in `GET /api/v1/emails` — and as an Android notification — with
**no pull-to-refresh and no Flutter timer**. A per-user lock prevents a manual
call and a scheduler cycle from overlapping; `email_id` idempotency means a
message is never processed or notified twice.

This endpoint remains for an **explicit manual refresh** (pull-to-refresh): same
sync, same lock, returns the result inline. Added after the 10.5 freeze;
additive, no existing endpoint changed.

> **Pull-to-refresh never bulk-fetches the unread inbox.** It calls only this
> incremental endpoint. The legacy `GET /api/v1/gmail/unread/process` (which
> *does* walk every unread message) is a manual / compatibility path and is
> **not** called by the Flutter client anywhere.
>
> **Reconnecting Gmail re-anchors the baseline.** The OAuth callback runs
> `ensure_baseline(force=True)`: the cursor jumps to the mailbox's current
> `historyId`, so neither the historical inbox nor mail that arrived while the
> account was disconnected / while an old cursor was stale is replayed.
>
> **A sync never resets AGENT AMAR state.** Re-persisting an already-known
> `email_id` (history replay, cap-resume overlap, crash-retry) updates the
> system analysis and appends a `ProcessingRun`, but `is_viewed`, `is_completed`
> / `completion_source`, `snoozed_until`, `primary_category_source` and each
> action's `status` are all preserved.
>
> **Gmail Spam is never ingested.** Every fetch path (baseline, incremental
> sync — new mail and label-change scans — and the legacy manual endpoints)
> checks Gmail's own `SPAM` system label on the message metadata before doing
> anything else with it — never our own detection. A spam message is not
> normalized, classified, scored by the ML classifier or an LLM, persisted, or
> notified on, and it does not appear anywhere in `GET /api/v1/emails` (any
> filter) or `GET /api/v1/emails/{id}` (404, as if unknown). If an
> already-ingested email is later moved to Spam in Gmail, the existing row is
> kept (never deleted) but stops appearing anywhere and gets no further
> notifications; moving it back out of Spam re-processes it normally without
> creating a duplicate row.

**Request:** no params / body. **Bearer required** — scoped to the caller; only
that user's Gmail account is synced. **Errors:** `401` `AuthRequiredError` when
the app session is missing / expired / revoked (the client must clear the stored
token and return to Login); `401` `GmailNotConnectedError` when the session is
valid but that user's Gmail is not connected (prompt "reconnect Gmail", no
re-login).

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
| `primary_category` | string enum | **the canonical inbox bucket** — `REPLY_REQUIRED` / `ACTION_REQUIRED` / `IMPORTANT` / `LOW_PRIORITY`. Mutually exclusive: use THIS for the four inbox sections. |
| `priority` | string enum | filter by `priority_level` (`LOW`…`CRITICAL`) |
| `category` | string enum | filter by `final_category` (the 15-value Triage label — secondary metadata) |
| `action_required` | bool | secondary flag — still `true` for a Reply Required email; do not use it to build the "Action Required" section (use `primary_category` instead) |
| `needs_human_review` | bool | only low-confidence emails |
| `viewed` | bool | filter on `is_viewed` |
| `completed` | bool | filter on `is_completed` |
| `active` | bool | **attention-dashboard filter.** `true` = only emails that still need the user's attention right now (the homepage feed); `false` = the resolved / acknowledged complement (history / archive); omit for the full list. See [`is_active`](#is_active--attention-dashboard) below. Nothing is ever deleted — this only filters. |
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
    "primary_category": "ACTION_REQUIRED",
    "auto_primary_category": "ACTION_REQUIRED",
    "primary_category_source": "auto",
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
    "completion_source": "auto",
    "snoozed_until": null,
    "needs_human_review": false,
    "is_active": true,
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
| `final_category` | enum | no | the 15-value Triage label — **secondary metadata**. See [Category](#final_category--category-triage-15) |
| `primary_category` | enum | no | **the ONE inbox bucket** (`REPLY_REQUIRED`/`ACTION_REQUIRED`/`IMPORTANT`/`LOW_PRIORITY`) — mutually exclusive; the UI sections filter on this. The user's manual correction when they made one, else the automated derivation. See [primary_category](#primary_category--the-one-inbox-bucket-mutually-exclusive-) |
| `auto_primary_category` | enum | no | the latest **automated** derivation — kept even after a user correction (original prediction, for training / audit) |
| `primary_category_source` | string | no | `"auto"` \| `"user"` (manually corrected via classification-feedback) |
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
| `is_completed` | bool | no | resolved. Auto-set when every blocking action is `COMPLETED`/`DISMISSED` (**promote-only** — a reprocess never clears it), or set explicitly via `PATCH .../complete`. Persistent user state; a Gmail sync cannot reset it. |
| `completed_at` | datetime | yes | when `is_completed` became true |
| `completion_source` | string | no | `"auto"` (derived from action statuses) \| `"user"` (explicitly resolved — never reverted by a sync) |
| `snoozed_until` | datetime | yes | active snooze end; `null` = not snoozed |
| `needs_human_review` | bool | no | low confidence / unresolved conflict |
| <a id="is_active--attention-dashboard"></a>`is_active` | bool | no | **derived** — whether the email still needs the user's attention (the homepage feed). `false` once resolved (`is_completed`), snoozed, or — for a non-actionable `IMPORTANT` / `LOW_PRIORITY` email — once opened (`is_viewed`). A `REPLY_REQUIRED` / `ACTION_REQUIRED` email stays `true` until it is resolved (opening it is not enough). Filter the list with `?active=true`. |
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
| `method` | string | yes | free-form (`deterministic`, `ml`, `llm`, `llm_fallback_deterministic`, `deterministic+llm_adjustment`, …). **Opaque display string** — values are not fully normalised across agents. `ml` = a confident local model prediction (LLM call skipped). |
| `fallback_used` | bool | no | agent fell back to a safe default |
| `duration_ms` | int | yes | timing |
| `error_codes` | string[] | no | `[]` when none |

> There is no per-agent free-text "summary" in the trace — use the entry fields
> above plus the top-level `summary` / `reasoning_summary`.

### `GET /api/v1/emails/{email_id}/processing`
**Purpose:** Full processing history (Agent Activity → "all runs").
**Success `200`:** `ProcessingRunOut[]`, newest first (same shape as
`latest_processing`). `404` if the email is unknown.

### `POST /api/v1/emails/{email_id}/classification-feedback` *(bearer)*
**Purpose:** the user manually corrects an email's canonical primary category
(Phase 18). Ownership-checked.

**Request:** `{ "category": "IMPORTANT" }` — one of `REPLY_REQUIRED` /
`ACTION_REQUIRED` / `IMPORTANT` / `LOW_PRIORITY` (any other value → `422`).

**Behaviour (atomic, one transaction):**
* records the correction in an append-only history (`classification_feedback`),
* sets the email's `primary_category` to the correction and
  `primary_category_source = "user"` — the email leaves its old section and
  enters the new one **immediately** (all queries + the UI filter on
  `primary_category`; it is never in two buckets),
* keeps the automated prediction in `auto_primary_category` (unchanged) and
  snapshots the classifier provenance (`classifier_source`, `ml_confidence`,
  `llm_used`, `original_final_category`) onto the feedback row for a future
  controlled retraining loop. **The ML model is not retrained.**

Re-submitting the value the email already shows is a **no-op** (`200`, no new
history row).

**Success `200`** — the updated `EmailStateDetailOut` (`primary_category` now the
correction, `auto_primary_category` the original, `primary_category_source` =
`"user"`).

| Status | When |
|---|---|
| `401` | no / expired session |
| `404` | `email_id` unknown or not owned by the caller |
| `422` | `category` not one of the four primary buckets |

### `GET /api/v1/emails/{email_id}/classification-feedback` *(bearer)*
The correction history for one owned email (`ClassificationFeedbackOut[]`,
newest first). `404` if unknown / not owned.

### `GET /api/v1/emails/{email_id}/full` *(bearer)*
**Purpose:** the complete email for the authenticated owner (the stored record
keeps only a ≤240-char `snippet`).

The body is fetched **live from the owner's Gmail** and returned as **plain text**
— the Mail Intake normaliser flattens any `text/html` part (`<script>` / `<style>`
/ every tag dropped), so the payload can never carry executable markup. Raw Gmail
objects, headers and tokens are **not** exposed.

**Success `200`** — `FullEmailResponse`:
```json
{
  "email_id": "gmail_18f0a1b2c3",
  "thread_id": "18f0a1b2c3",
  "subject": "Project meeting tomorrow",
  "sender_name": "Prof. Rao",
  "sender_email": "rao@college.edu",
  "received_at": "2026-09-01T09:14:00Z",
  "body": "Hi,\n\nPlease confirm whether you can attend …",
  "body_format": "text",            // "text" | "html_converted"
  "is_truncated": false,
  "primary_category": "REPLY_REQUIRED",
  "final_category": "REPLY_REQUIRED",
  "priority_level": "HIGH",
  "action_required": true
}
```

| Status | When |
|---|---|
| `401` | no / expired session |
| `401` | `GmailNotConnectedError` — the user's Gmail is not connected |
| `404` | `email_id` unknown / not owned, **or** the message no longer exists in Gmail |

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

### `PATCH /api/v1/emails/{email_id}/complete` *(bearer)*
**Purpose:** Explicitly resolve the **whole email** — the "mark done" / tick
action. Completes *every* pending action (not just `act_001`), sets
`is_completed=true`, `completed_at`, and **`completion_source="user"`**. Because
the source is `"user"`, a later Gmail sync / reprocess can **never** revert it
(see below). The email drops out of `GET /api/v1/emails?active=true` at once.
No body. **Success `200`:** `EmailStateDetailOut`. Idempotent. `404` unknown / other-user.

### `PATCH /api/v1/emails/{email_id}/reopen` *(bearer)*
**Purpose:** Undo a completion. `is_completed=false`, `completion_source="auto"`
(back to derivation). `EmailStateDetailOut`. `404` unknown / other-user.

> **`is_completed` is persistent user state — a Gmail sync cannot reset it.**
> Auto-completion (all blocking actions `COMPLETED`/`DISMISSED`) is **promote-only**:
> once true it is never recomputed back to false by reprocessing. `completion_source`
> (`"auto"` \| `"user"`) records whether the user resolved it explicitly; when
> `"user"`, `_recompute_completion` is skipped entirely.

### `POST /api/v1/emails/clear-acknowledged` *(bearer)* — "Clear Resolved"
**Purpose:** Tidy the active dashboard. Marks every **active, non-actionable**
email (`IMPORTANT` / `LOW_PRIORITY`, not completed, not snoozed) as acknowledged
(`is_viewed=true`) so it leaves `?active=true`. **Never** touches
`REPLY_REQUIRED` / `ACTION_REQUIRED` — an unresolved task is never silently
completed. **Nothing is deleted; no Gmail call is made; Gmail messages are
untouched.** No body. Idempotent.
**Success `200`:** `{ "acknowledged": <int> }` — rows actually changed.

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
| `connected_users` | int | no | users with a live Gmail token (Phase 15) |
| `classification` | object | no | **additive** — in-process triage routing counters (`total`, `deterministic`, `ml`, `llm`, `llm_fallback_deterministic`, `ml_attempted`, `ml_accepted`, `ml_rejected_*`, `ml_average_confidence`, `llm_avoidance_rate`). Metadata only — never email content. Resets on restart. |

> The Triage payload's `signals.classification_routing` (additive) carries the
> per-email routing trace: `method`, `deterministic_confidence`, `ml_attempted`,
> `ml_confidence`, `ml_skipped`, `ml_reject_reason`, `llm_invoked`. Metadata only.

---

### `GET /api/v1/system/status` — backend + LLM connection status *(open, no auth)*
**Purpose:** power the compact status bar at the top of the Home screen
(`🟢 Backend Online   🟢 AI Online`). Read-only and lightweight — it never runs
an LLM completion, Gmail sync, or agent workflow, and never spends API tokens.
Added after the 10.5 freeze; additive — no existing contract changed.

**Request:** no params, no body, no auth header needed.

**Success `200`:**
```json
{
  "backend": { "status": "online" },
  "llm": {
    "status": "online",
    "provider": "ollama",
    "model": "qwen2.5:3b",
    "detail": "2 model(s) available"
  }
}
```

| Field | Type | Null? | Meaning |
|---|---|---|---|
| `backend.status` | string | no | always `"online"` — receiving this response *is* the backend liveness signal |
| `llm.status` | enum | no | `online` · `offline` · `unconfigured` · `unknown` |
| `llm.provider` | string | yes | `none` · `ollama` · `gemini` · `openai` · `anthropic` (the raw configured value; `null` only if unset) |
| `llm.model` | string | yes | effective model name when a provider is configured |
| `llm.detail` | string | yes | short human note (never a key, token, or stack trace) |

**`llm.status` meanings:**
- `online` — Ollama: server reachable and the configured model is present. API providers (openai/anthropic/gemini): an API key is configured (remote reachability is **not** probed — the existing client has no free check and a real call could cost/rate-limit).
- `offline` — Ollama: server unreachable, returned an error, or the configured model is not pulled.
- `unconfigured` — `LLM_PROVIDER=none`, or an API provider with no key. The pipeline still runs deterministically.
- `unknown` — unrecognised `LLM_PROVIDER` value, **or** (client-side) the backend was unreachable so the LLM state cannot be known.

**Backend unreachable:** the client shows `Backend Offline` and `AI Unknown` — it never guesses the LLM state. Never returns 5xx for a normal "LLM offline" condition.

**Frontend usage:** `EmailRepository.getSystemStatus()` → `SystemStatusDto` → `InboxController` (`backendOnline`, `llmStatus`, `llmProvider`, `llmModel`). Checked on app start, on app resume, and after pull-to-refresh — **no polling loop**. Flutter never contacts an LLM provider directly.

---

## 10. Reminders (user-scheduled)

> **⚠️ The Sorted mobile app no longer uses these endpoints.** Since the
> time-based redesign, user reminders and deadline alarms are scheduled on the
> device by the OS — see [§10.0](#100-who-schedules-what-device-vs-backend)
> below. The endpoints here remain implemented, tested and supported for other
> API consumers; they are simply not what makes a notification fire on the
> phone any more.

### 10.0 Who schedules what: device vs backend

Three different mechanisms, deliberately kept separate:

| # | What | Owner | Fires when | Needs network? |
|---|---|---|---|---|
| 1 | **New-mail / escalation events** — an email the device does not know about yet | Backend: Gmail monitoring → classification → `NotificationRecord` → FCM push (§11) | The backend detects it | Yes — this is exactly the case a device cannot schedule for itself |
| 2 | **User reminders** — "remind me at 6pm" | Device (`LocalScheduleService` → `flutter_local_notifications.zonedSchedule`) | Device clock | **No** |
| 3 | **Deadline warnings + alarms** — 24h / 1h before, and at the deadline | Device, reconciled from `deadline` / `next_deadline_at` in the email payload | Device clock | **No** |

For (2) and (3), once the device knows the time, the OS owns the firing: **no
API call, no poll, no Gmail sync, no pull-to-refresh, and no running app** is
required at the moment the notification fires, and it still fires with the
backend offline or Wi-Fi disabled.

The device treats the backend as authoritative for *email* state (deadlines,
classification, completion) and reconciles its local schedule after each
successful fetch: a deadline it already scheduled and that has not changed is
left untouched, a changed deadline is rescheduled under the same notification
id, and a completed/resolved email's alarms are cancelled. Repeated responses
from `GET /api/v1/emails` therefore never produce duplicate alarms.

Backend `notification_type` values `deadline_escalation` / `user_reminder`
(§9) still exist and are still pushed — they are the backend's own record and
its path for case (1). They are no longer the mechanism by which an
already-known reminder or deadline reaches the user.

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

## 11. Push devices (Phase 16)

All bearer-authenticated and scoped to the current user. Backend push (FCM) is
what notifies the user when the app is backgrounded / swiped away / terminated —
see [`PUSH_NOTIFICATIONS.md`](PUSH_NOTIFICATIONS.md).

**When a push is sent.** Only on a *meaningful backend event*, never because
Flutter refreshed. After the scheduler (or a manual sync) processes a new email
and the existing rules create a `notifications` row (priority `HIGH`/`URGENT`/
`CRITICAL`, or an escalation/reminder), that row is pushed **once**
(`notifications.pushed_at` is the dedup key; only rows newer than
`PUSH_MAX_AGE_MINUTES` are eligible). Low-priority mail follows the existing
policy — no row, no push. An FCM failure is caught and never breaks email
processing; a token FCM reports as invalid is deactivated.

**Payload — identifiers only, no secrets, no content:**
`{ "notification_id", "email_id", "type", "severity", "requires_alarm" }`. The
title/body are generic per `type`. Never the email subject/body, an OTP, a Gmail
token, or an LLM key. On tap, Flutter authenticates and fetches the email from
`GET /api/v1/emails/{email_id}` — a `404` (deleted / not owned) is handled
gracefully.

### `POST /api/v1/devices/register`
**Body:** `{ "fcm_token": string, "platform"?: "android"|"ios",
"device_label"?: string, "app_version"?: string, "previous_token"?: string }`
(`previous_token`, when the FCM token rotated, is deactivated first).
**Success `200`:** `{ "id": int, "platform": string, "device_label": string|null,
"app_version": string|null, "active": true, "last_seen_at": datetime|null }`.
Idempotent per `fcm_token`; a token that re-registers under a different user is
reassigned to the caller.

### `POST /api/v1/devices/unregister`
**Body:** `{ "fcm_token": string }`. **Success `200`:**
`{ "status": "unregistered" | "not_found" }`.

### `GET /api/v1/devices`
**Success `200`:** the caller's active devices (`DeviceOut[]`).

`POST /api/v1/auth/logout` additionally accepts an optional `{ "fcm_token": string }`
and deactivates that device.

---

## 12. AI reply suggestions & send

Both endpoints are **bearer-authenticated** and only operate on an email the
caller **owns** (another user's `email_id` → `404`, indistinguishable from
"unknown id"). Suggestions are **not persisted**. **The AI never sends anything** —
`/reply-suggestions` returns text only, and `/reply` sends exactly the `body` the
client submits, verbatim, never re-generated. Sending always requires an explicit
user action (the "Send Reply" button).

Suggestion generation uses the same server-side LLM abstraction as the agents
(`LLM_PROVIDER` = `none`|`ollama`|`openai`|`anthropic`|`gemini`). Sending uses the
authenticated user's own Gmail credentials (`gmail.send` scope — added alongside
the existing `gmail.readonly`; a user connected before this feature must reconnect
once, surfaced as `401 GmailNotConnectedError`).

### `POST /api/v1/emails/{email_id}/reply-suggestions` *(bearer)*

No request body. The backend reads the original message from Gmail for context,
then asks the LLM for **exactly 3** meaningfully different drafts (a concise/direct
one, a polished/professional one, and a genuine alternative — e.g. declining or
deferring; for a yes/no question they are never all "yes").

**Success `200`:**
```json
{
  "email_id": "gmail_18f0a1b2c3",
  "suggestions": [
    { "id": "option_1", "label": "Direct",       "body": "Yes, I will attend the project meeting tomorrow." },
    { "id": "option_2", "label": "Professional", "body": "Thank you for the note. I expect to be able to join…" },
    { "id": "option_3", "label": "Alternative",  "body": "Unfortunately I don't think I can make it tomorrow…" }
  ]
}
```
Always exactly 3 items; `id` is always `option_1..3`.

**Slow by design.** This call blocks on the LLM. A small local model (Ollama on
CPU) can take **over a minute** to draft 3 options. The backend allows up to
`LLM_REPLY_TIMEOUT_SECONDS` (default 120s, separate from the normal
`LLM_TIMEOUT_SECONDS`); the client should use a matching long read-timeout
(`ApiConfig.aiReceiveTimeout`, default 150s) and keep the "generating" state up.
A genuine timeout surfaces as `503`.

**Errors:**
| Status | `error` | When | Client |
|---|---|---|---|
| `401` | `AuthRequiredError` | no / expired session | clear token → Login |
| `401` | `GmailNotConnectedError` | Gmail not connected / `gmail.send` not granted | prompt "reconnect Gmail" |
| `404` | — | `email_id` unknown **or** not owned by the caller | "email not found" |
| `502` | `LLMResponseError` | the model replied but not with 3 usable, distinct options | show error + **Retry** |
| `503` | `LLMUnavailableError` | no provider configured / provider down | show error + **Retry** |

The `502`/`503` body shape is `{ "error": "...", "detail": "...", "provider": "llm" }`.
No fake / template replies are ever produced.

### `POST /api/v1/emails/{email_id}/reply` *(bearer)*

**Body:** `{ "body": string }` — the **final, user-approved** reply text (an
unmodified suggestion, an edited suggestion, or the user's own text). 1–25 000
chars. Sent **verbatim**.

The reply is **threaded**: the backend re-reads the original message and sets
`In-Reply-To` + `References` from its `Message-ID`/`References`, replies to the
original `Reply-To` (else `From`), reuses the original subject (`Re: …`, not
double-prefixed), and passes the original Gmail `threadId` on the send. `From` is
**never** set by AGENT AMAR — Gmail fills it with the authenticated account.

**Marked done on send.** Any pending `REPLY` action is set `COMPLETED`, and the
email itself is marked `is_completed` (the reply fulfils its obligation) — unless
it still has other pending non-reply actions the user must handle. The response
reports both via `reply_action_completed` and `email_marked_completed`. No new
"replied" status was invented; this reuses the existing completion model.

**Duplicate-send safe:** an identical `(user, email_id, body)` within ~2 minutes
returns the first result with `"duplicate_suppressed": true` and does **not** hit
Gmail again (covers double-tap / retry / concurrent submit). Flutter also disables
the Send button while a send is in flight.

**Success `200`:**
```json
{
  "email_id": "gmail_18f0a1b2c3",
  "thread_id": "18f0a1b2c3",
  "gmail_message_id": "18f0aa77…",
  "reply_action_completed": true,
  "email_marked_completed": true,
  "duplicate_suppressed": false
}
```

**Errors:**
| Status | `error` | When |
|---|---|---|
| `401` | `AuthRequiredError` | no / expired session |
| `401` | `GmailNotConnectedError` | Gmail not connected, or `gmail.send` not granted (reconnect) |
| `404` | — | `email_id` unknown / not owned; or the message no longer exists in Gmail |
| `422` | — | `body` empty / too long; no reply recipient could be determined |
| `502` | `GmailApiError` | Gmail rejected the send (transient) — safe to retry |

On a failure the client keeps the user's draft.

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

# Production behaviour

Everything below describes how the deployed API differs from a laptop run. No
endpoint shapes changed — these are transport, availability and safety rules.

## Health probes (public, unauthenticated)

| Endpoint | Purpose | Body |
|---|---|---|
| `GET /health` | Liveness. Dependency-free on purpose, so a database blip never makes the orchestrator kill a healthy container. | `{"status":"ok","service":"…"}` |
| `GET /health/ready` | Readiness. `503` when the database is unreachable, so a load balancer stops routing here. | `{"status":"ready\|degraded","checks":{"database":"ok","scheduler":"running\|stopped","llm":"online\|offline\|unknown"}}` |

`checks` values are coarse strings only — never a hostname, URL, driver
version or error text. `llm` is advisory: the app degrades to the local ML
classifier and readiness does not depend on it.

## Rate limits

Expensive endpoints are budgeted per session (defaults; configurable):

| Endpoint | Limit |
|---|---|
| `POST /api/v1/emails/{id}/reply-suggestions` | 6 / min |
| `POST /api/v1/emails/{id}/reply` | 6 / min |
| `POST /api/v1/gmail/sync` | 12 / min |

Exceeding one returns `429` with `Retry-After` and
`{"detail": "...", "retry_after_seconds": N}`. The limit protects the
single-box LLM and the Gmail API quota; clients should back off, not retry
immediately.

## Response headers

Every response carries `X-Request-ID` (echoed if the client supplies one) —
quote it in bug reports; server logs are correlated by it. Also
`X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
`Referrer-Policy: no-referrer`, `Cache-Control: no-store`.

## What production does NOT expose

* `POST /intake/gmail` — an unauthenticated development helper. Not registered
  unless `ENABLE_DEBUG_INTAKE_ENDPOINT=true`, and production refuses to start
  with that flag on.
* `GET /api/v1/system/status` stays public (the app's status bar renders before
  login) but never names the inference host: an unreachable LLM reports
  `"AI service is not reachable"`, not a URL or port.
* OAuth tokens, Gmail credentials, connection strings and stack traces never
  appear in a response body.

## CORS

None by default, deliberately: the client is a native mobile app, which sends
no `Origin`. `CORS_ALLOW_ORIGINS` adds an explicit allowlist if a browser
client is ever introduced; it is never `*`.

## Scheduling boundary (device vs backend)

| Event | Owner | Delivery |
|---|---|---|
| New important / action / reply email the device does not know about | Backend (Gmail monitoring → classification) | FCM push |
| Deadline escalation computed server-side | Backend | FCM push |
| User reminder, deadline warning (24h/1h), deadline alarm | **Device** | OS-scheduled local notification |

The backend is never polled at the moment a reminder or deadline alarm fires;
those are scheduled locally the moment the device learns the time. See the
frontend `LocalScheduleService`.

---

# Error responses

| Status | When | Body shape |
|---|---|---|
| `400` | invalid reminder time / unknown `action_ref` in a reminder | `{ "detail": "<message>" }` |
| `401` | a Gmail endpoint called while Gmail is not connected | `{ "error": "GmailNotConnectedError", "detail": "…", "provider": "gmail" }` |
| `404` | unknown `email_id` / `action_ref` / `reminder_id` / `notification_id` | `{ "detail": "email not found" }` (message varies) |
| `422` | request body / query param fails validation | `{ "detail": [ { "type": "...", "loc": ["body","reminder_at"], "msg": "Field required", "input": {} } ] }` |
| `502` / `503` | AI reply suggestions: model returned unusable output (`502 LLMResponseError`) / no provider or provider down (`503 LLMUnavailableError`) | `{ "error": "<ClassName>", "detail": "…", "provider": "llm" }` |
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

# Appendix B — Audit chain (Phase 14, ops/diagnostics)

Read-only, additive. **Not** a Flutter surface — for operators / monitoring.
Metadata only: no email content, OTPs, addresses, tokens or keys. Phase 14 also
added transparent encryption at rest, which changes **no** request or response
shape (agents and API responses still see plaintext).

### `GET /api/v1/audit/verify`
Recompute the tamper-evident hash chain.
| Query | Type | |
|---|---|---|
| `limit` | int 1–100000 | check only the first N records (optional) |

**`200`:**
```json
{ "valid": true, "records_checked": 125, "first_invalid_record": null, "reason": null }
```
`valid=false` ⇒ `first_invalid_record` is the `audit_id` of the first broken
link and `reason` names the failed check (`record_hash mismatch` /
`previous_hash does not match` / `sequence gap`).

### `GET /api/v1/audit/events`
| Query | Type | Default |
|---|---|---|
| `limit` | int 1–500 | 100 |
| `offset` | int ≥0 | 0 |

**`200`:**
```json
{ "total": 125,
  "events": [
    { "audit_id": "…uuid…", "sequence": 125,
      "timestamp": "2026-08-29T11:45:20Z",
      "event_type": "GMAIL_SYNCED", "resource_type": "gmail_account",
      "resource_id": "default", "detail": { "new": 0, "processed": 0, "errors": 0 },
      "previous_hash": "…64 hex…", "record_hash": "…64 hex…" } ] }
```
`event_type` ∈ `EMAIL_INGESTED` · `EMAIL_PROCESSED` · `EMAIL_VIEWED` ·
`ACTION_CREATED` · `DEADLINE_CREATED` · `REMINDER_CREATED` · `NOTIFICATION_SENT` ·
`GMAIL_CONNECTED` · `GMAIL_SYNCED`. `resource_id` is an opaque internal id
(`gmail_<id>`, `gmail_<id>/act_001`, `"default"`) — never an email address.

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
pull-to-refresh at `POST /api/v1/gmail/sync`; Phase 14 added `GET /api/v1/audit/verify`
· `/events` (ops-only) + transparent encryption at rest.
**Phase 15** made the API multi-user: every `/api/v1/*` data route now requires a
`Bearer` session token and is scoped to that user; new `…/auth/google/start` ·
`/session` · `/api/v1/auth/me` · `/api/v1/auth/logout`; `…/auth/google/status`
and `…/disconnect` are now per-user. `GET /api/v1/gmail/unread/process` is NOT
restored as an app path.
**Phase 16** added `POST /api/v1/devices/register` · `/unregister` · `GET
/api/v1/devices` for FCM push (§11); `/api/v1/auth/logout` now takes an optional
`fcm_token`. No other endpoint or field changed.
**Phase 17** made mobile sign-in a production-style deep-link handoff: the
`/callback` now validates the OAuth `state`, mints a short-lived single-use
handoff code and 302s to `agentamar://auth/callback?code=…&state=…`; new open
endpoint `POST /api/v1/auth/session/exchange { code, state }` → `{ session_token,
user }`. No token/credential ever appears in a URL. `/session` polling is
retained as the manual-browser (`/login`) fallback only. No `/api/v1/*` data
contract changed.
**Phase 18** added user classification feedback + full email viewing:
`POST` / `GET /api/v1/emails/{id}/classification-feedback` (manual primary-category
correction; `primary_category` becomes the current bucket, `auto_primary_category`
keeps the original, mutual exclusivity preserved) and `GET /api/v1/emails/{id}/full`
(complete plain-text body, fetched live from Gmail, HTML flattened server-side).
`EmailStateOut` gained `auto_primary_category` + `primary_category_source`. The ML
model is **never** auto-retrained — feedback feeds a deliberate `python -m
app.ml.retrain` candidate/promote flow.
Backend test suite: `python -m pytest` → **609 passed**. Flutter: `flutter test`
→ **54 passed**.*
