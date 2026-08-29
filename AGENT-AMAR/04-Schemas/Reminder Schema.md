# 📦 Schema: Reminder & Notification State

**Related:** [[Persistent Email State]] · [[Deadline Monitoring]] · [[Reminder Escalation]] · [[User Preferences]]

Two Phase 10 tables in `backend/app/db/models.py`. Both are consumed by the
future Flutter notification layer — the backend decides *what* should happen,
the app decides *how* the user experiences it.

---

## `reminders` — user-scheduled reminders

"Remind me about this email at 09:00 tomorrow." **Not** a snooze.

| Concept | Snooze (`emails.snoozed_until`) | Reminder (`reminders` row) |
|---|---|---|
| Meaning | *suppress* automatic escalation until `T` | *explicitly alert me* at `T` |
| Count | one per email | many per email |
| On expiry / trigger | evaluation resumes | one `user_reminder` notification is created |

| Field | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `email_pk` | FK → `emails.id` | cascade delete |
| `action_ref` | string / null | optional link to one `actions.action_ref` |
| `reminder_at` | datetime (UTC) | when to fire |
| `reminder_type` | `USER_SCHEDULED` | `SYSTEM_*` reminders are `notifications` rows, not here |
| `status` | `PENDING` → `TRIGGERED` \| `CANCELLED` \| `SKIPPED` | `SKIPPED` = email/action already handled at fire time |
| `timezone` | string | the tz the user picked (informational) |
| `note` | text / null | shown in the notification |
| `created_at` / `triggered_at` / `cancelled_at` | datetime | |

**Execution** (in `run_deadline_check`): `status = PENDING` and
`reminder_at <= now` ⇒ create a `user_reminder` notification and set
`TRIGGERED` + `triggered_at`; if the email is complete / the linked action is
done, set `SKIPPED` instead. Never fires twice. Evaluated **independently** of
deadline escalation — a custom reminder never disables alarm protection.

---

## `notifications` — intended alert events (Phase 9 table, extended)

One row per intended alert. No sender, no delivery.

| Field | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `email_pk` | FK → `emails.id` | |
| `deadline_pk` | FK → `deadlines.id` / null | set for `deadline_escalation` / `deadline_passed` / `ambiguous_deadline` |
| `reminder_pk` | FK → `reminders.id` / null | set for `user_reminder` |
| `notification_type` | `new_priority_email` \| `deadline_escalation` \| `deadline_passed` \| `ambiguous_deadline` \| `user_reminder` | |
| `reminder_level` | `NORMAL` \| `REMINDER` \| `URGENT` \| `ALARM` / null | the escalation rung |
| `severity` | same vocab | mirrors `reminder_level` for query convenience |
| `requires_alarm` | bool | `true` only on an `ALARM` rung |
| `status` | `PENDING` → `SENT` \| `FAILED` \| `SKIPPED` | `SKIPPED` = held back (quiet hours) |
| `detail` | text | human-readable reason |
| `created_at` / `sent_at` | datetime | |

**De-duplication.** `deadline_escalation` events are keyed on
`(deadline_pk, reminder_level)`; the monitor issues a rung only when it
outranks `highest_escalation_for(deadline_pk)`. `deadline_passed` /
`ambiguous_deadline` / `new_priority_email` fire at most once per email/deadline.

---

## Not in scope (Phase 11)

Actual push / sound / vibration / full-screen alarm, a background scheduler,
rate-limit windows, the daily digest, learned escalation down-weighting.
