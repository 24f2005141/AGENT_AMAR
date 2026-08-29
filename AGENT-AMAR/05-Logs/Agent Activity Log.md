# 📝 Agent Activity Log

**Related:** [[Agent Control Center]] · [[AMAR Orchestrator]] · [[New Email Processing]]

Human-readable trace of agent events for **debugging and understanding**.

> This is a **summary** log for humans. The backend keeps the full, queryable event history. Do not treat this file as the system of record.

---

## Entry template

```
---
Timestamp: 2026-08-28T16:30:12+05:30
Run ID: run_2026-08-28T11:00:12Z_ab12
Agent: Triage Agent
Event: Email Classified
Email ID: example_001
Details:
  Category: INTERNSHIP
  Subcategory: application_form
  Confidence: 0.96
Result: Further analysis required
Notes: Sender in Important Senders (Placement Cell)
---
```

### Fields

| Field | Meaning |
|---|---|
| `Timestamp` | ISO 8601 with offset |
| `Run ID` | Correlates all events for one email pass ([[Agent Output Schema]]) |
| `Agent` | Which agent (or `AMAR Orchestrator` / `Backend`) |
| `Event` | Short event name (see vocabulary below) |
| `Email ID` | `email_id` from [[Email Schema]] |
| `Details` | Key fields for this event |
| `Result` | One-line outcome |
| `Notes` | Anything useful for debugging (overrides applied, conflicts, low confidence) |

### Event vocabulary

`Email Received` · `Email Normalised` · `Intake Error` · `Classification Started` · `Email Classified` · `Action Analysis` · `Deadline Extracted` · `Deadline Ambiguous` · `Priority Scored` · `Final Decision` · `Stored` · `Notification Sent` · `Notification Suppressed` · `Monitoring Started` · `Reminder Fired` · `Reminder Suppressed` · `Monitoring Stopped` · `Deadline Passed` · `Human Review Flagged`

---

## Example trace — one email end to end

```
---
Timestamp: 2026-08-28T09:14:25+05:30
Run ID: run_2026-08-28T03:44:25Z_9f21
Agent: Mail Intake Agent
Event: Email Normalised
Email ID: gmail_18f0a1b2c3
Details:
  Sender: placement@college.edu
  Subject: Summer Internship 2026 - Application form (deadline Sep 2)
  Attachments: 1 (internship_brochure.pdf)
Result: OK
Notes: 1 link extracted
---
Timestamp: 2026-08-28T09:14:28+05:30
Run ID: run_2026-08-28T03:44:25Z_9f21
Agent: Triage Agent
Event: Email Classified
Email ID: gmail_18f0a1b2c3
Details:
  Category: INTERNSHIP
  Subcategory: application_form
  Confidence: 0.96
Result: Further analysis required
Notes: Sender in Important Senders (CRITICAL)
---
Timestamp: 2026-08-28T09:14:31+05:30
Run ID: run_2026-08-28T03:44:25Z_9f21
Agent: Action Agent
Event: Action Analysis
Email ID: gmail_18f0a1b2c3
Details:
  action_required: true
  actions: FORM_SUBMISSION, DOCUMENT_UPLOAD
  Confidence: 0.90
Result: 2 blocking actions
---
Timestamp: 2026-08-28T09:14:31+05:30
Run ID: run_2026-08-28T03:44:25Z_9f21
Agent: Deadline Agent
Event: Deadline Extracted
Email ID: gmail_18f0a1b2c3
Details:
  raw: "by 2 September 2026, 6:30 PM"
  normalized: 2026-09-02T18:30:00+05:30
  ambiguity_flag: false
  Confidence: 0.93
Result: Monitoring required
---
Timestamp: 2026-08-28T09:14:33+05:30
Run ID: run_2026-08-28T03:44:25Z_9f21
Agent: Priority Agent
Event: Priority Scored
Email ID: gmail_18f0a1b2c3
Details:
  score: 92
  level: CRITICAL
  notify: true
  monitor: true
Result: CRITICAL
---
Timestamp: 2026-08-28T09:14:34+05:30
Run ID: run_2026-08-28T03:44:25Z_9f21
Agent: AMAR Orchestrator
Event: Final Decision
Email ID: gmail_18f0a1b2c3
Details:
  final_category: INTERNSHIP
  priority_level: CRITICAL
  routing: store=true, notify=true, monitor=true
  conflicts_resolved: none
Result: Routed to store + notify + monitor
---
Timestamp: 2026-08-28T09:14:35+05:30
Run ID: run_2026-08-28T03:44:25Z_9f21
Agent: Backend
Event: Notification Sent
Email ID: gmail_18f0a1b2c3
Details:
  channel: push
  message: "Internship application due Sep 2, 6:30 PM - form + resume"
Result: Delivered
---
Timestamp: 2026-08-28T09:14:35+05:30
Run ID: run_2026-08-28T03:44:25Z_9f21
Agent: Backend
Event: Monitoring Started
Email ID: gmail_18f0a1b2c3
Details:
  monitor_id: mon_000123
  deadline: 2026-09-02T18:30:00+05:30
Result: ACTIVE
---
```

---

## Backend (Phase 8)

`backend/app/agents/amar_orchestrator.py::to_activity_log(envelope)` renders
exactly this text-block format from a Final Decision Object — one block per
agent in the trace plus a final `AMAR Orchestrator` / `Final Decision` block.
It is **dev/debug only** (returned by `GET /api/v1/gmail/unread/process` as
`activity_log`); it never contains email bodies. The structured `agent_trace`
inside the Final Decision Object is the primary runtime artifact — this file
stays documentation, not an event store.

---

## New entries

_Append below. Newest at the bottom, or use your preferred order — keep it consistent._
