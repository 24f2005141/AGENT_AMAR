# 📦 Schema: Email Schema

**Related:** [[Mail Intake Agent]] · [[AMAR Orchestrator]] · [[Agent Output Schema]] · [[New Email Processing]]

The normalised email object produced by the [[Mail Intake Agent]] and consumed by every downstream agent.

> This describes the **message payload**, not database columns. The backend may store more.

---

## JSON example

```json
{
  "email_id": "gmail_18f0a1b2c3",
  "thread_id": "gmail_thread_9981",
  "message_id_header": "<CAF=abc123@mail.gmail.com>",
  "sender": {
    "name": "Placement Cell",
    "email": "placement@college.edu"
  },
  "to": ["student@college.edu"],
  "cc": [],
  "reply_to": "placement@college.edu",
  "subject": "Summer Internship 2026 - Application form (deadline Sep 2)",
  "body": "Dear students, applications for the summer internship program are now open. Fill the form by 2 September 2026, 6:30 PM. Attach an updated resume.",
  "body_format": "text",
  "snippet": "Applications for the summer internship program are now open...",
  "received_at": "2026-08-28T09:14:22+05:30",
  "labels": ["INBOX", "UNREAD", "CATEGORY_UPDATES"],
  "is_unread": true,
  "attachments": [
    {
      "filename": "internship_brochure.pdf",
      "mime_type": "application/pdf",
      "size_bytes": 482113,
      "attachment_id": "att_001"
    }
  ],
  "links": [
    "https://forms.college.edu/internship-2026"
  ],
  "has_links": true,
  "language": "en",
  "body_parse_error": false,
  "needs_human_review": false,
  "source": "gmail",
  "ingested_at": "2026-08-28T09:14:25+05:30"
}
```

---

## Field reference

| Field | Type | Required | Description |
|---|---|---|---|
| `email_id` | string | ✅ | Stable unique id (Gmail message id or internal) |
| `thread_id` | string | ✅ | Conversation/thread id |
| `message_id_header` | string | – | RFC `Message-ID` header |
| `sender` | object | ✅ | `{ name, email }` |
| `to` | string[] | ✅ | Recipient addresses |
| `cc` | string[] | – | CC addresses |
| `reply_to` | string | – | Reply-To address |
| `subject` | string | ✅ | Subject line (may be empty string) |
| `body` | string | ✅ | Cleaned plain-text body (quotes/signatures stripped) |
| `body_format` | enum | ✅ | `text` \| `html_converted` |
| `snippet` | string | – | Short preview (~200 chars) |
| `received_at` | string (ISO 8601) | ✅ | When the mail server received it; **reference time** for [[Deadline Agent]] |
| `labels` | string[] | ✅ | Gmail labels |
| `is_unread` | boolean | ✅ | Convenience flag |
| `attachments` | object[] | ✅ | Metadata only — `filename`, `mime_type`, `size_bytes`, `attachment_id` |
| `links` | string[] | – | URLs found in the body |
| `has_links` | boolean | ✅ | — |
| `language` | string | – | ISO 639-1 detected language |
| `body_parse_error` | boolean | ✅ | True if decoding failed |
| `needs_human_review` | boolean | ✅ | Set by intake on missing/invalid headers |
| `source` | string | ✅ | `gmail` (future: `outlook`, …) |
| `ingested_at` | string (ISO 8601) | ✅ | When AGENT AMAR processed intake |

---

## Rules

- All timestamps are **ISO 8601 with timezone offset**.
- `body` never contains raw HTML — either `text/plain` or converted.
- Attachment **contents are not included** — only metadata.
- Downstream agents must not assume optional fields exist; check first.
- If validation fails, [[Mail Intake Agent]] still emits the object with `needs_human_review = true`.
- **ID convention:** `email_id` = `gmail_` + the raw Gmail message id; `thread_id` = `gmail_thread_` + the raw Gmail thread id. This keeps ids stable, unique, and source-identifiable (matches the examples above).

---

## JSON Schema (draft)

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "AmarEmail",
  "type": "object",
  "required": ["email_id", "thread_id", "sender", "to", "subject", "body",
               "body_format", "received_at", "labels", "is_unread",
               "attachments", "has_links", "body_parse_error",
               "needs_human_review", "source", "ingested_at"],
  "properties": {
    "email_id": { "type": "string" },
    "thread_id": { "type": "string" },
    "message_id_header": { "type": "string" },
    "sender": {
      "type": "object",
      "required": ["email"],
      "properties": {
        "name": { "type": "string" },
        "email": { "type": "string", "format": "email" }
      }
    },
    "to": { "type": "array", "items": { "type": "string" } },
    "cc": { "type": "array", "items": { "type": "string" } },
    "reply_to": { "type": "string" },
    "subject": { "type": "string" },
    "body": { "type": "string" },
    "body_format": { "type": "string", "enum": ["text", "html_converted"] },
    "snippet": { "type": "string" },
    "received_at": { "type": "string", "format": "date-time" },
    "labels": { "type": "array", "items": { "type": "string" } },
    "is_unread": { "type": "boolean" },
    "attachments": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["filename", "mime_type", "size_bytes"],
        "properties": {
          "filename": { "type": "string" },
          "mime_type": { "type": "string" },
          "size_bytes": { "type": "integer" },
          "attachment_id": { "type": "string" }
        }
      }
    },
    "links": { "type": "array", "items": { "type": "string" } },
    "has_links": { "type": "boolean" },
    "language": { "type": "string" },
    "body_parse_error": { "type": "boolean" },
    "needs_human_review": { "type": "boolean" },
    "source": { "type": "string" },
    "ingested_at": { "type": "string", "format": "date-time" }
  }
}
```
