# AGENT AMAR — Data Security (Phase 14)

Practical confidentiality for a single-instance prototype: **encryption at rest**,
**secret minimisation**, **log redaction**, and a **tamper-evident audit chain**.
No blockchain infrastructure, no KMS.

```
Gmail → Mail Intake → Sensitive-data sanitisation → Agent processing
                                                        ↓
                                        Encrypted persistence (AES-256-GCM)
                                                        ↓
                                        Hash-chained audit metadata
```

---

## 1. Encryption at rest — architecture

`app/core/crypto.py` + `EncryptedString` (a SQLAlchemy `TypeDecorator` in
`app/db/base.py`).

* **Cipher:** AES-256-GCM (`cryptography` library) — authenticated encryption.
* **Envelope stored in the column:**
  `ENC1:` + `base64url( nonce[12] ‖ ciphertext ‖ gcm_tag[16] )`,
  with a constant AAD (`agent-amar/phase14/aes-256-gcm`) binding the version.
* **Fresh random nonce per write** — the same plaintext encrypts to different
  ciphertext each time.
* **Transparent:** `process_bind_param` encrypts on write, `process_result_value`
  decrypts on read. Agents, pipelines and API responses keep seeing plaintext —
  only the bytes on disk are ciphertext. At the DB level the column is still
  `TEXT`, so there is **no schema migration**.
* **Fails closed:** a value with the `ENC1:` prefix that fails GCM
  authentication (wrong key / tampering) raises `DecryptionError`. It is never
  silently returned.

### What is encrypted

| Table | Column(s) | Why |
|---|---|---|
| `emails` | `subject`, `snippet`, `sender_name`, `sender_email` | email content + PII (the snippet is the most likely place an OTP lands) |
| `actions` | `description`, `target_link`, `raw_deadline_hint` | text derived from / quoting the email; links may carry tokens |
| `deadlines` | `source_text`, `ambiguity_reason` | verbatim phrases copied from the email |
| `reminders` | `note` | user-authored free text |
| `notifications` | `detail` | may be derived from a reminder note / email text (also **sanitised** first) |
| `gmail_sync_state` | `account_email` | PII — the connected Gmail address |

### What is deliberately NOT encrypted — and why

| Kept plaintext | Reason |
|---|---|
| primary keys, foreign keys, `email_id`, `thread_id` | identity / join keys; `email_id` is the idempotency key **and** an index. Opaque Gmail ids (`gmail_<id>`), not PII. |
| `action_ref`, `deadline_ref`, `run_id`, `audit_id`, `user_id`, `last_history_id` | opaque internal / Gmail cursors |
| `final_category`, `priority_level`, `priority_score`, `proximity_bucket`, `folder_label`, `severity`, `notification_type`, `reminder_type`, `status`, all booleans | system enums / scores used for **filtering, indexing and ordering** (`GET /api/v1/emails?priority=…&category=…`). Ciphertext is opaque to SQL. |
| all timestamps, `deadline_datetime` | chronological range queries (`deadline < now`) + ordering; not sensitive |
| `confidence`, `category_confidence` | numeric analysis scores |
| `source`, `timezone`, `action_context`, `related_action_ref`, `date_only` | tiny non-sensitive descriptors |
| `processing_runs.summary` / `agent_trace` / `conflicts_resolved` / `review_reasons` / `errors` | system-generated analysis metadata (a routing one-liner + structured trace). **Sanitised** on write (any quoted OTP/token is masked) rather than encrypted, so it stays queryable/loggable. |

The **full email body is never persisted at all** (Phase 9 design) — only the
≤240-char snippet, which is encrypted.

### Environment variables

| Var | Default | Meaning |
|---|---|---|
| `DATA_ENCRYPTION_ENABLED` | `true` | master switch. `false` ⇒ `EncryptedString` is a passthrough (plaintext at rest, exactly as before Phase 14). |
| `DATA_ENCRYPTION_KEY` | `""` | 32-byte key, **base64url or hex**. Never hardcode / commit. |
| `APP_ENV` | `development` | `production` enforces key validation at startup. |

**Generate a secure key:**
```bash
python -c "import secrets,base64;print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
```

### Key handling & validation (startup)

`crypto.configure(settings)` runs in the FastAPI `lifespan` startup:

| Situation | Behaviour |
|---|---|
| `DATA_ENCRYPTION_ENABLED=false` | passthrough; log "encryption DISABLED" |
| enabled, valid key | AES-256-GCM active |
| enabled, **production**, missing/invalid key | **`EncryptionConfigError` — the app refuses to start** (fail safe) |
| enabled, **development**, missing key | a built-in **deterministic INSECURE** key + a loud `WARNING`. Data is NOT confidential in this mode. |

A random key is **never auto-generated** at startup — that would silently orphan
previously-encrypted rows. The dev fallback is deterministic for the same reason
(dev data stays readable across restarts without a real key).

### Key rotation — limitation

There is a **single active key** (no key-id in the envelope). Changing
`DATA_ENCRYPTION_KEY` makes existing ciphertext unreadable. To rotate you must,
**offline**, decrypt every `EncryptedString` value with the OLD key and re-store
it with the NEW key (both keys available at once). This prototype ships **no**
multi-key envelope / KMS. Plan rotation as a maintenance window with a plaintext
export/import.

---

## 2. OTP & sensitive-data handling

`app/core/sanitization.py` — one reusable, deterministic (pattern-based) utility.

**Detects:** OTP / verification codes, passwords, API keys, bearer / access /
refresh tokens, JWTs, `sk-`/`rk-`/`pk-` secrets, AWS access-key ids, Google
`ya29.` / `1//` tokens.

**`mask_sensitive(text)`** replaces the *secret span* with `******`, keeping
context:

```
"Your verification code is 483921"   →   "Your verification code is ******"
```

Applied before data reaches:

| Sink | Where |
|---|---|
| **logs & exception messages** | `RedactingFilter` (`app/core/logging_setup.py`) attached to every `agent_amar*` logger + root handlers; runs every formatted line through `mask_sensitive`. |
| **notification payloads** | `NotificationRecord.detail` derived from a reminder note is masked in `DeadlineMonitorService._fire_reminder`. |
| **processing-run metadata** | `summary` / `review_reasons` / `conflicts_resolved` / `errors` masked in `PersistenceService._append_processing_run`. |
| **the audit chain** | `audit_record` runs `detail` through `redact_mapping` (defence in depth — callers pass only non-sensitive metadata anyway). |
| **OAuth error text** | the oauthlib exception string (can echo the auth code / a token) is masked before it is logged or returned in the dev-mode API detail. |

The system **may still retain the original email content encrypted at rest** when
the app needs it (subject/snippet) — but a **raw OTP never appears** in logs,
exception messages, notifications, the audit chain, or debug output.

Not a perfect security system — deterministic patterns, high-signal shapes only.

---

## 3. Tamper-evident audit chain

`app/services/audit_service.py` + the `audit_events` table. A lightweight
**hash-chained append-only ledger** — **not** Ethereum / Hyperledger / mining /
wallets / smart contracts / consensus.

### Record

| Field | |
|---|---|
| `audit_id` | uuid4 |
| `sequence` | 1, 2, 3, … (monotonic; unique) — gap = a deleted record |
| `timestamp` | UTC ISO 8601 |
| `event_type` | `EMAIL_INGESTED` · `EMAIL_PROCESSED` · `EMAIL_VIEWED` · `ACTION_CREATED` · `DEADLINE_CREATED` · `REMINDER_CREATED` · `NOTIFICATION_SENT` · `GMAIL_CONNECTED` · `GMAIL_SYNCED` |
| `resource_type` | `email` · `action` · `deadline` · `reminder` · `notification` · `gmail_account` |
| `resource_id` | opaque internal id (`gmail_<id>`, `gmail_<id>/act_001`, `"default"`) — **never** an email address |
| `detail` | tiny non-sensitive JSON (`{"category":"EXAM","priority":"HIGH"}`, `{"processed":2}`) — sanitised |
| `previous_hash` | the prior record's `record_hash` (genesis: `"0" * 64`) |
| `record_hash` | `SHA-256( canonical_json({audit_id, sequence, timestamp, event_type, resource_type, resource_id, detail}) + previous_hash )` |

**Never stored:** email body / subject / snippet, OTPs, sender/recipient
addresses, OAuth tokens, API keys, encryption keys, any plaintext confidential
data. The model has no column for them.

### How verification works

`AuditService(session).verify_chain(limit=None)` walks the ledger in `sequence`
order and for every record checks:

1. `sequence` is exactly `prior + 1` — **detects a deleted / missing record**.
2. `previous_hash` equals the prior record's stored `record_hash` — **detects a
   broken link**.
3. re-computing `record_hash` from the stored fields matches the stored value —
   **detects any modified field** (`event_type`, `detail`, `timestamp`, …).

Result (no sensitive data):

```json
{ "valid": true, "records_checked": 125, "first_invalid_record": null, "reason": null }
```

On failure, `valid=false`, `first_invalid_record` is the `audit_id` of the first
bad link, and `reason` says which check failed.

### Writing

`audit_record(...)` / `audit_record_many([...])` append in **their own short
transaction** (serialised by a process lock) so the chain stays consistent
regardless of the caller's transaction. A failure to record is **logged, never
raised** into the business flow. One pipeline pass emits one batched append.

### Endpoints (read-only, non-sensitive)

| Method | Path | |
|---|---|---|
| GET | `/api/v1/audit/verify` | `{valid, records_checked, first_invalid_record, reason}` |
| GET | `/api/v1/audit/events` | the ledger, newest first (`limit`, `offset`) |

### Concurrency limitation

Single-instance only. The lock serialises appends within one process; two
backend processes could still race two appends into the same `sequence`
(the unique constraint rejects the loser). Run one instance with the scheduler
enabled (already the guidance in `docs/BACKGROUND_SCHEDULER.md`).

---

## 4. Backward compatibility / migration

* **Schema:** none. `EncryptedString` is `TEXT` at the DB level; `init_db()`
  (`create_all`) only adds the new `audit_events` table.
* **Existing plaintext rows:** read back **unchanged** — `decrypt()` returns any
  value that lacks the `ENC1:` prefix as-is. The app does not crash.
* **Lazy re-encryption:** the next time a legacy row is written (reprocessed),
  its `EncryptedString` columns are stored encrypted.
* **Eager migration** (optional): `python -c "from app.db.session import db_session;
  from app.core import crypto; import app.db.session as s;
  # (after configuring the engine)
  \nwith db_session() as db: print(crypto.reencrypt_all(db))"` re-writes every
  `EncryptedString` column so legacy plaintext becomes ciphertext under the
  current key. Safe to run repeatedly; a no-op when encryption is disabled.
  **It is not a key-rotation tool** (it can't read data written under a *different*
  key).

**Limitation:** rows written with the **insecure dev fallback key** become
unreadable once a real `DATA_ENCRYPTION_KEY` is set. For a real deployment, set a
key from day one.

---

## 5. Logging safety

The `RedactingFilter` guarantees email bodies, OTPs, passwords, OAuth access /
refresh tokens, LLM API keys and encryption keys are masked in every
`agent_amar*` log line and in root handlers (uvicorn access/error). Useful
operational logging (counts, ids, history cursors, statuses) is kept — sensitive
*values* are redacted, not the whole line.

`DATABASE_ECHO=true` would log SQL bind params; with encryption **enabled** those
show ciphertext. Keep `DATABASE_ECHO=false` in any environment with real data.

---

## Threat model — what this does and does not cover

**Covers:** an attacker with read access to the SQLite file / a DB backup / a
disk image sees ciphertext for email content + PII, not plaintext. Log
scraping / crash reports do not surface OTPs or tokens. Silent modification or
deletion of audit history is detectable.

**Does not cover:** an attacker who also has the `DATA_ENCRYPTION_KEY` (env / a
memory dump of the running process); the audit chain being *append-only* is
enforced only in code (a direct SQL `DELETE` of the whole tail is detectable but
not prevented); a stolen application session token (it grants that one user's
data until it expires or is revoked via logout).

---

## 6. Multi-user isolation (Phase 15)

* **Identity.** One `users` row per person, keyed by the stable Google subject
  (`google_sub`). `google_email` / `display_name` are `EncryptedString`.
* **Sessions.** `POST /api/v1/auth/google/start` → browser consent →
  `/callback` mints an opaque bearer token; only `sha256(token)` is stored
  (`app_sessions.token_hash`). Logout / expiry / revocation are row state. The
  Google **client secret and OAuth refresh tokens never reach the client.**
* **Per-user Gmail credentials.** `oauth_credentials`, one row per user, the whole
  blob stored via `EncryptedString` (AES-256-GCM). Replaces the old plaintext
  `.tokens/default.json`. `FileTokenStore` remains only as a documented dev
  fallback.
* **Data scoping.** `emails.user_pk` (+ `gmail_sync_state.user_pk`,
  `audit_events.user_pk`). Every repository read the routes call takes a
  `user_pk` and filters by it; children (`actions` / `deadlines` / `reminders` /
  `notifications` / `processing_runs`) are reached only through an
  ownership-checked email. Cross-user ids return `404`, never another user's row.
* **Audit chain.** Still a single global chain; each row carries `user_pk`
  (inside the hash, so tampering with ownership is detected).
  `GET /api/v1/audit/events` returns only the caller's rows;
  `GET /api/v1/audit/verify` checks the whole chain.
* **Scheduler.** The Gmail-sync cycle iterates every connected user and runs a
  per-user sync under a per-user lock.
* **Not covered:** RBAC / roles / admin, a distributed session store, and
  deleting user data on logout (logout only ends the session).
