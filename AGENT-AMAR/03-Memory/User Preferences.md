# 🧠 Memory: User Preferences

**Related:** [[Priority Agent]] · [[Priority Rules]] · [[Triage Agent]] · [[Reminder Escalation]] · [[Important Senders]]

Human-editable preferences that agents read at decision time. Edit this file directly to change system behaviour.

> The backend keeps the machine-readable copy of these settings. This file is the **source of truth for humans** and the seed for that copy.

---

## 1. Identity & defaults

| Setting | Value |
|---|---|
| User | Student (undergraduate) |
| Primary email | `student@college.edu` |
| Default timezone | `Asia/Kolkata` (UTC+05:30) |
| Locale / date format | `DD Month YYYY`, 24h + 12h accepted |
| Academic context | College / university student seeking internships & placements |

---

## 2. Priority categories

How much the user cares about each [[Triage Agent]] category. Feeds the [[Priority Agent]] base score.

### HIGH priority (protect aggressively)
- Internship opportunities
- Internship application forms
- Placement opportunities
- Job opportunities
- Assignment deadlines
- Exam information (schedule, hall ticket, results)
- Faculty announcements
- Important academic communication
- Emails requiring an urgent reply

### MEDIUM priority
- Academic information (general)
- Project updates
- Event registrations
- Club announcements
- General university communication

### LOW priority (never notify)
- Promotional emails
- Shopping offers
- Marketing emails
- Newsletters
- Social notifications

---

## 3. Notification preferences

| Setting | Value |
|---|---|
| Notify at level | `HIGH`, `URGENT`, `CRITICAL` |
| Do **not** notify at level | `MEDIUM`, `LOW` (show in app only) |
| Channels | Push (primary), email digest (daily 08:00), chat (CRITICAL only) |
| Quiet hours | 23:00–07:00 `Asia/Kolkata` |
| Quiet-hours behaviour | Queue `HIGH`/`URGENT`; allow `CRITICAL` final alerts to break through |
| Daily digest | 08:00 — summary of all `MEDIUM+` emails from the last 24 h |
| Max notifications/hour | 6 (excluding `CRITICAL` finals) |
| Weekend mode | Same rules; digest at 10:00 instead of 08:00 |

---

## 4. Monitoring preferences

| Setting | Value |
|---|---|
| Auto-monitor when action required | Yes |
| Auto-monitor `HIGH+` even with no explicit action | Yes |
| Post-deadline grace window | 24 h |
| Default snooze options | +15m, +1h, +1d, "until tonight" |
| Muted categories | _(none yet)_ |
| Muted senders | _(none yet — see [[Important Senders]] for the opposite list)_ |

> **Snooze vs scheduled reminder** (Phase 10). *Snooze* (`snoozed_until`)
> **suppresses** automatic escalation until a time — one per email. A
> *scheduled reminder* (`reminders` row, [[Reminder Schema]]) **explicitly
> alerts** the user at a time — many per email, optionally tied to an action.
> They are independent: a scheduled reminder never disables alarm-level
> deadline protection.

> **Quiet-hours policy** (Phase 10, centralised in `escalation_policy.py`):
> `NORMAL` / `REMINDER` / `URGENT` events are held back during quiet hours (a
> single `SKIPPED` record is kept); an `ALARM` breaks through **only for a
> `CRITICAL` deadline**. Muted category/sender suppression is documented but
> not yet enforced by the monitor.

---

## 5. Learned preferences

> Filled in over time by the backend from user behaviour. Agents may read this but should treat it as **soft** signal (lower weight than Section 6).

_Examples of what will appear here:_
- `EVENT` reminders are dismissed ~80% of the time → reduce EVENT escalation aggressiveness.
- User always opens emails from `placement@college.edu` within 10 minutes → keep as `CRITICAL` sender.
- User frequently acts on `ASSIGNMENT` emails only in the evening → prefer evening reminder slots.

_(No learned entries yet — system is at Stage 0.)_

---

## 6. Explicit user overrides

> **Highest precedence.** These beat category defaults, learned preferences, and agent judgement. The [[AMAR Orchestrator]] applies these last.

| # | Rule | Effect | Added |
|---|---|---|---|
| 1 | Any email mentioning "internship" or "placement" — **except** LOW-band categories (`PROMOTIONAL`/`NEWSLETTER`/`SPAM`/`SOCIAL`), where the word alone is not an opportunity | Force minimum level `URGENT` | 2026-08-28 |
| 2 | Sender domain `@college.edu` | Never classify as `PROMOTIONAL`/`SPAM`; minimum `MEDIUM` | 2026-08-28 |
| 3 | Emails from `noreply@` marketing domains | Force `LOW`, no notification | 2026-08-28 |
| 4 | Category `EXAM` | Always notify, always monitor | 2026-08-28 |

_Add new overrides as a new row. Keep them specific and testable._

---

## 7. Change log

| Date | Change | By |
|---|---|---|
| 2026-08-28 | Initial preferences created | System setup |
