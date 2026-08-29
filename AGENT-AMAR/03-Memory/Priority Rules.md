# 🧠 Memory: Priority Rules

**Related:** [[Priority Agent]] · [[User Preferences]] · [[Important Senders]] · [[Reminder Escalation]] · [[Deadline Monitoring]]

The source of truth for how the [[Priority Agent]] scores urgency. All numbers here are **adjustable** — change them in this file.

---

## 1. Score → level mapping

| Level | Score band | Behaviour |
|---|---|---|
| `CRITICAL` | 90–100 | Escalating reminders ([[Reminder Escalation]]), break quiet hours for finals |
| `URGENT` | 75–89 | Immediate notification, monitor |
| `HIGH` | 55–74 | Single notification, monitor |
| `MEDIUM` | 30–54 | App only, appears in daily digest |
| `LOW` | 0–29 | Organise/label only, no notification |

---

## 2. Deterministic scoring factors

Applied by backend / agent as a rule table. Start at `0`, add/subtract, clamp to `0–100`.

| Factor | Points | Notes |
|---|---|---|
| Action required (from [[Action Agent]]) | **+30** | Any `action_required = true` |
| Deadline within 1 hour | **+40** | Proximity bucket `WITHIN_1H` |
| Deadline within 24 hours | **+25** | Bucket `WITHIN_24H` |
| Deadline within 72 hours | **+12** | Bucket `WITHIN_72H` |
| Deadline exists but `LATER` | +5 | — |
| Deadline overdue but still actionable | +35 | Bucket `OVERDUE` + action open |
| Ambiguous deadline (`ambiguity_flag`) | +10 | Instead of proximity points |
| Internship / placement / job opportunity | **+20** | Category-based |
| Assignment / exam | +18 | Category-based |
| Faculty announcement / important academic | +15 | Category-based |
| Reply explicitly required | +15 | Category `REPLY_REQUIRED` or clear ask |
| Event with registration | +8 | — |
| Important sender `HIGH` ([[Important Senders]]) | **+10** | — |
| Important sender `CRITICAL` | +20 | Also floors level at `HIGH` |
| Attachment is a form / official doc | +5 | — |
| Promotional email | **−30** | Category `PROMOTIONAL` |
| Newsletter | −20 | Category `NEWSLETTER` |
| Social notification | −20 | Category `SOCIAL` |
| Spam | −40 | Category `SPAM` |
| `LOW_TRUST` sender | −10 | — |

> These are **starting rules**. Tune them as real emails are processed.

---

## 3. AI judgement adjustment

The [[Priority Agent]] LLM may add **−10 to +10** for nuance the table misses:
- Emotional/urgent tone from a real person → up to +10
- "FYI", "no action needed", "for your records" → down to −10
- Must be explained in `reasoning_summary`.

---

## 4. Urgency thresholds (deadline proximity buckets)

Computed by **deterministic backend code**, never the LLM:

| Bucket | Definition |
|---|---|
| `OVERDUE` | `now > deadline` |
| `WITHIN_1H` | `0 < deadline - now <= 1h` |
| `WITHIN_24H` | `1h < deadline - now <= 24h` |
| `WITHIN_72H` | `24h < deadline - now <= 72h` |
| `LATER` | `deadline - now > 72h` |
| `NONE` | no deadline detected |

Buckets are re-evaluated on every [[Deadline Monitoring]] tick, so an email's priority can **rise over time**.

---

## 5. Escalation logic (summary)

Full ladders in [[Reminder Escalation]].

| Level | Reminder thresholds (time remaining) |
|---|---|
| `CRITICAL` | 30 min → 15 min → 5 min → passed |
| `URGENT` | 12 h → 3 h → 1 h → 15 min → passed |
| `HIGH` | 24 h → 6 h → 1 h → passed |
| `MEDIUM` | 24 h (once) |
| `LOW` | none |

Reminders fire **only if the task is still unhandled** and not snoozed/muted.

---

## 6. User preference adjustments

Applied **after** scoring, from [[User Preferences]]:

| Source | Effect |
|---|---|
| Explicit override (§6) | Can force a level regardless of score |
| Category priority band (§2) | Floor/ceiling — backend: HIGH-band categories → floor `MEDIUM`; LOW-band (`PROMOTIONAL`/`NEWSLETTER`/`SPAM`/`SOCIAL`) → ceiling `MEDIUM` (and never `notify`/`monitor`) |
| Muted category/sender | Force `LOW`, no notification, no monitor |
| Learned preference (§5) | Soft ±5 nudge only |
| Quiet hours | Does not change score; delays delivery (except `CRITICAL` finals) |

> **Backend additions** (`app/agents/priority_agent.py`): an `OVERDUE` deadline
> ceilings the level at `URGENT`; a high-value category with an action and an
> ambiguous/unresolved deadline is bumped to at least `HIGH` + flagged for review
> (safety bias); an ambiguous-but-resolved deadline scores proximity ×
> `PRIORITY_AMBIGUOUS_DEADLINE_FACTOR` (0.7). See [[Priority Agent]] "Backend
> implementation notes".

---

## 7. Recomputation

Priority is recomputed when:
- A monitoring tick moves the deadline into a closer bucket
- The user snoozes / views / partially completes an action
- A new reply arrives in the thread
- A related [[User Preferences]] or [[Important Senders]] entry changes

---

## 8. Worked example

> Internship application, form attached, deadline in 20 hours, from `placement@college.edu`.

| Factor | Points |
|---|---|
| Action required | +30 |
| Deadline within 24h | +25 |
| Internship opportunity | +20 |
| Important sender CRITICAL | +20 |
| Form attachment | +5 |
| AI adjustment (urgent tone) | +2 |
| **Total** | **102 → clamp 100** |

Level: `CRITICAL`. Notify immediately, start [[Deadline Monitoring]], escalation ladder armed.

---

## 9. Change log

| Date | Change |
|---|---|
| 2026-08-28 | Initial scoring rules created |
