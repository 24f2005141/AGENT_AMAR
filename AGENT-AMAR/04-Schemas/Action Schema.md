# 📦 Schema: Action Schema

**Related:** [[Action Agent]] · [[Agent Output Schema]] · [[Deadline Agent]] · [[Deadline Monitoring]]

The shape of a single extracted **action** and the [[Action Agent]]'s `data` payload.

---

## Action types

| Type | Meaning |
|---|---|
| `FORM_SUBMISSION` | Fill and submit a form |
| `REPLY` | Send a reply / confirmation |
| `REGISTRATION` | Register / sign up |
| `DOCUMENT_UPLOAD` | Upload or attach a document |
| `PAYMENT` | Make a payment |
| `ATTEND_EVENT` | Be present at a scheduled event |
| `COMPLETE_ASSIGNMENT` | Do and submit coursework |
| `READ_AND_ACKNOWLEDGE` | Read carefully / acknowledge receipt |
| `OTHER` | Anything else |

---

## Single action object

```json
{
  "action_id": "act_001",
  "action_type": "FORM_SUBMISSION",
  "action_description": "Fill and submit the summer internship application form.",
  "target_link": "https://forms.college.edu/internship-2026",
  "related_email": "gmail_18f0a1b2c3",
  "blocking": true,
  "raw_deadline_hint": "by 2 September 2026, 6:30 PM",
  "confidence": 0.94,
  "status": "OPEN"
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `action_id` | string | ✅ | Unique within the email |
| `action_type` | enum | ✅ | One of the types above |
| `action_description` | string | ✅ | One-line, imperative ("Submit…", "Reply…") |
| `target_link` | string \| null | – | URL to act on, if present in the email |
| `related_email` | string | ✅ | `email_id` from [[Email Schema]] |
| `blocking` | boolean | ✅ | Must be done before a deadline / another action |
| `raw_deadline_hint` | string \| null | – | Deadline phrase copied verbatim for context (normalisation is the [[Deadline Agent]]'s job) |
| `confidence` | number | ✅ | 0.0–1.0 |
| `status` | enum | ✅ | `OPEN` \| `IN_PROGRESS` \| `DONE` \| `SKIPPED` — set by backend/user over time |

---

## Action Agent `data` payload

```json
{
  "action_required": true,
  "actions": [
    {
      "action_id": "act_001",
      "action_type": "FORM_SUBMISSION",
      "action_description": "Fill and submit the summer internship application form.",
      "target_link": "https://forms.college.edu/internship-2026",
      "related_email": "gmail_18f0a1b2c3",
      "blocking": true,
      "raw_deadline_hint": "by 2 September 2026, 6:30 PM",
      "confidence": 0.94,
      "status": "OPEN"
    },
    {
      "action_id": "act_002",
      "action_type": "DOCUMENT_UPLOAD",
      "action_description": "Attach an updated resume (PDF) to the form.",
      "target_link": null,
      "related_email": "gmail_18f0a1b2c3",
      "blocking": true,
      "raw_deadline_hint": "by 2 September 2026, 6:30 PM",
      "confidence": 0.8,
      "status": "OPEN"
    }
  ],
  "action_type": "FORM_SUBMISSION",
  "action_description": "Submit the internship application form with an updated resume.",
  "related_email": "gmail_18f0a1b2c3",
  "confidence": 0.9
}
```

| Field | Description |
|---|---|
| `action_required` | Boolean — false ⇒ `actions` is `[]` |
| `actions[]` | Array of single-action objects |
| `action_type` | The **primary** action type (summary of the set) |
| `action_description` | One-line summary of the whole task |
| `related_email` | `email_id` |
| `confidence` | Overall confidence |

---

## Rules

- Split compound requests into separate `actions[]` entries.
- `action_type` (top-level) = the most important / blocking action's type.
- Never normalise dates here — only copy `raw_deadline_hint`.
- `status` starts `OPEN`; the backend updates it as the user progresses; [[Deadline Monitoring]] stops when all blocking actions are `DONE`.
- If `action_required = false`, still return the payload with an empty `actions` array and `confidence` for that judgement.

---

## JSON Schema (draft)

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "AmarActionData",
  "type": "object",
  "required": ["action_required", "actions", "related_email", "confidence"],
  "properties": {
    "action_required": { "type": "boolean" },
    "actions": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["action_id", "action_type", "action_description",
                     "related_email", "blocking", "confidence", "status"],
        "properties": {
          "action_id": { "type": "string" },
          "action_type": {
            "type": "string",
            "enum": ["FORM_SUBMISSION", "REPLY", "REGISTRATION", "DOCUMENT_UPLOAD",
                     "PAYMENT", "ATTEND_EVENT", "COMPLETE_ASSIGNMENT",
                     "READ_AND_ACKNOWLEDGE", "OTHER"]
          },
          "action_description": { "type": "string" },
          "target_link": { "type": ["string", "null"] },
          "related_email": { "type": "string" },
          "blocking": { "type": "boolean" },
          "raw_deadline_hint": { "type": ["string", "null"] },
          "confidence": { "type": "number", "minimum": 0, "maximum": 1 },
          "status": { "type": "string", "enum": ["OPEN", "IN_PROGRESS", "DONE", "SKIPPED"] }
        }
      }
    },
    "action_type": { "type": ["string", "null"] },
    "action_description": { "type": ["string", "null"] },
    "related_email": { "type": "string" },
    "confidence": { "type": "number", "minimum": 0, "maximum": 1 },
    "detection_method": {
      "type": "string",
      "enum": ["deterministic", "llm", "llm_fallback_deterministic"]
    }
  }
}
```

---

## Backend notes (Phase 5)

The machine copy lives in `backend/app/models/action.py` +
`backend/app/agents/action_agent.py` (+ `action_rules.py`).

- **`action_type` / `action_description`** (top level) are `null` when
  `action_required = false` — the JSON-Schema draft above was widened to
  `["string", "null"]` to match.
- **Two additive fields** (the draft sets no `additionalProperties: false`, so
  these do not break the contract):
  - per-action **`evidence`** — the concise quote the action was detected from.
  - payload **`detection_method`** — `deterministic` \| `llm` \| `llm_fallback_deterministic`.
- **`raw_deadline_hint`** is populated by a **verbatim copy** of the deadline
  phrase (e.g. `"by Friday"`) — no parsing or normalisation; the
  [[Deadline Agent]] does the real work in Phase 6.
- **Confidence thresholds** (adjustable — `backend/app/core/config.py`):
  `ACTION_REVIEW_THRESHOLD` = 0.55 (below ⇒ `needs_human_review`),
  `ACTION_LLM_THRESHOLD` = 0.65 (below, or on conflicting signals ⇒ LLM).
