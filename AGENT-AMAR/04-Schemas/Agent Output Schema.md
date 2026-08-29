# 📦 Schema: Agent Output Schema

**Related:** [[AMAR Orchestrator]] · [[Triage Agent]] · [[Action Agent]] · [[Deadline Agent]] · [[Priority Agent]] · [[Action Schema]]

Every specialised agent returns the **same envelope**. Only the `data` object differs per agent. This lets the [[AMAR Orchestrator]] handle all outputs uniformly.

---

## Envelope

```json
{
  "agent": "Triage Agent",
  "agent_version": "0.1.0",
  "email_id": "gmail_18f0a1b2c3",
  "run_id": "run_2026-08-28T09:14:26Z_ab12",
  "status": "ok",
  "confidence": 0.96,
  "needs_human_review": false,
  "reasoning_summary": "Internship announcement from the placement cell with a form and a deadline.",
  "data": { },
  "errors": [],
  "started_at": "2026-08-28T09:14:26+05:30",
  "finished_at": "2026-08-28T09:14:28+05:30"
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `agent` | string | ✅ | Agent name, e.g. `"Triage Agent"` |
| `agent_version` | string | ✅ | Semver of the agent prompt/logic |
| `email_id` | string | ✅ | From [[Email Schema]] |
| `run_id` | string | ✅ | Unique per invocation (for the [[Agent Activity Log]]) |
| `status` | enum | ✅ | `ok` \| `partial` \| `error` |
| `confidence` | number | ✅ | 0.0–1.0 overall confidence |
| `needs_human_review` | boolean | ✅ | Agent requests a human look |
| `reasoning_summary` | string | ✅ | 1–2 human-readable sentences |
| `data` | object | ✅ | Agent-specific payload (below) |
| `errors` | object[] | ✅ | `[]` when none; else `{ code, message }` |
| `started_at` / `finished_at` | ISO 8601 | ✅ | Timing |

---

## `data` payload per agent

### Mail Intake Agent

The [[Mail Intake Agent]] is deterministic and produces the normalised email itself, so its `data` payload **is the full email object** defined in [[Email Schema]] (not a smaller summary). `confidence` is `1.0` for a clean parse and lower when `needs_human_review` is set.

```json
{
  "email_id": "gmail_18f0a1b2c3",
  "thread_id": "gmail_thread_18f0a1b2c3",
  "sender": { "name": "Placement Cell", "email": "placement@college.edu" },
  "to": ["students-2026@college.edu"],
  "subject": "Software Engineering Internship Application - Action Required",
  "body_format": "text",
  "received_at": "2026-08-28T09:14:22+05:30",
  "labels": ["INBOX", "UNREAD"],
  "is_unread": true,
  "attachments": [],
  "has_links": true,
  "body_parse_error": false,
  "needs_human_review": false,
  "source": "gmail",
  "ingested_at": "2026-08-28T09:14:25+05:30"
}
```

Downstream agents (Triage, Action, Deadline, Priority) receive this object as their **input**, and each wraps *their own* result in the envelope with an agent-specific `data` payload (below).

### Triage Agent

```json
{
  "category": "INTERNSHIP",
  "subcategory": "application_form",
  "importance_estimate": "HIGH",
  "further_analysis_required": true,
  "signals": { "keywords": ["internship", "form", "deadline"], "sender_in_important_list": true }
}
```

### Action Agent

Follows [[Action Schema]]:

```json
{
  "action_required": true,
  "actions": [
    {
      "action_type": "FORM_SUBMISSION",
      "action_description": "Submit the internship application form.",
      "target_link": "https://forms.college.edu/internship-2026",
      "related_email": "gmail_18f0a1b2c3",
      "blocking": true,
      "confidence": 0.94
    }
  ],
  "action_type": "FORM_SUBMISSION",
  "action_description": "Submit the internship application form with an updated resume.",
  "related_email": "gmail_18f0a1b2c3"
}
```

### Deadline Agent

```json
{
  "deadline_detected": true,
  "raw_deadline_text": "by 2 September 2026, 6:30 PM",
  "normalized_deadline": "2026-09-02T18:30:00+05:30",
  "timezone": "Asia/Kolkata",
  "ambiguity_flag": false,
  "ambiguity_reason": null,
  "monitoring_required": true,
  "reference_time_used": "2026-08-28T09:14:22+05:30"
}
```

### Priority Agent

```json
{
  "priority_score": 92,
  "priority_level": "CRITICAL",
  "score_breakdown": [
    { "factor": "action_required", "points": 30 },
    { "factor": "deadline_within_24h", "points": 25 },
    { "factor": "internship_or_placement", "points": 20 },
    { "factor": "important_sender_critical", "points": 20 },
    { "factor": "ai_judgement_adjustment", "points": -3 }
  ],
  "notify": true,
  "monitor": true
}
```

### AMAR Orchestrator (final decision)

The orchestrator emits the same envelope with `agent = "AMAR Orchestrator"` and this `data`:

```json
{
  "email_id": "gmail_18f0a1b2c3",
  "final_category": "INTERNSHIP",
  "action_required": true,
  "primary_action_type": "FORM_SUBMISSION",
  "actions": [{ "action_type": "FORM_SUBMISSION", "blocking": true, "confidence": 0.94 }],
  "deadline": "2026-09-02T18:30:00+05:30",
  "deadline_ambiguous": false,
  "deadline_is_past": false,
  "proximity_bucket": "WITHIN_24H",
  "priority_level": "CRITICAL",
  "priority_score": 92,
  "routing": {
    "store": true,
    "notify": true,
    "monitor": true,
    "folder_label": "AMAR/Opportunities"
  },
  "needs_human_review": false,
  "review_reasons": [],
  "conflicts_resolved": [
    { "rule": "deterministic_deadline_authoritative", "detail": "concrete deadline 2026-09-02T18:30:00+05:30 used as-is" }
  ],
  "agent_trace": [
    { "agent": "Mail Intake Agent", "status": "ok", "confidence": 1.0, "method": "deterministic" },
    { "agent": "Triage Agent", "status": "ok", "confidence": 0.96, "method": "deterministic", "fallback_used": false, "duration_ms": 2, "error_codes": [] }
  ]
}
```

> **Phase 8 backend:** `agent_trace` is a list of structured entries
> `{agent, status, confidence, method, fallback_used, duration_ms, error_codes}`
> (the earlier bare string list was illustrative). `email_id`,
> `needs_human_review`, `actions`, `deadline_is_past`, `proximity_bucket` and
> `review_reasons` are part of the payload. `conflicts_resolved` entries are
> `{rule, detail}`. See [[AMAR Orchestrator]] "Backend implementation notes".

---

## Rules

- Agents **must** return valid JSON in this envelope — no prose outside it.
- On failure: `status = "error"`, populate `errors[]`, still return the envelope.
- `status = "partial"` when some fields could be produced but not all.
- The orchestrator rejects any output missing required envelope fields and retries once, then flags `needs_human_review`.

---

## Envelope JSON Schema (draft)

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "AmarAgentOutput",
  "type": "object",
  "required": ["agent", "agent_version", "email_id", "run_id", "status",
               "confidence", "needs_human_review", "reasoning_summary",
               "data", "errors", "started_at", "finished_at"],
  "properties": {
    "agent": { "type": "string" },
    "agent_version": { "type": "string" },
    "email_id": { "type": "string" },
    "run_id": { "type": "string" },
    "status": { "type": "string", "enum": ["ok", "partial", "error"] },
    "confidence": { "type": "number", "minimum": 0, "maximum": 1 },
    "needs_human_review": { "type": "boolean" },
    "reasoning_summary": { "type": "string" },
    "data": { "type": "object" },
    "errors": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["code", "message"],
        "properties": {
          "code": { "type": "string" },
          "message": { "type": "string" }
        }
      }
    },
    "started_at": { "type": "string", "format": "date-time" },
    "finished_at": { "type": "string", "format": "date-time" }
  }
}
```
