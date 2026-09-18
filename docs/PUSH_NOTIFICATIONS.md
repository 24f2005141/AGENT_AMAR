# Push notifications — architecture (Phase 16)

AGENT AMAR notifies the user **even when the Flutter app is closed** by having the
backend push through Firebase Cloud Messaging. The pre-existing
`flutter_local_notifications` layer is kept for foreground presentation, local
reminders, the alarm dialog and tap handling — the two work together.

```
Gmail ─► per-user incremental sync ─► Triage/Action/Deadline/Priority agents
                                             │
                                    notification decision
                                             │
                                 persist  notifications  row
                                             │
                       PushNotificationService.dispatch_unpushed(user_id)
                                             │
                        Firebase Cloud Messaging  (HTTP v1, server-side)
                                             │
                     ┌───────────────────────┼───────────────────────┐
              User A's phone           User A's tablet          (never User B)
                     │
              Android/iOS system notification  (app open, backgrounded, or terminated)
                     │
                 user taps
                     │
     Flutter launches → session restored → EmailDetailScreen → fetches the
     authorised email from the backend (no email content was in the push)
```

The backend scheduler runs this loop for **every connected user** independently
of Flutter. The app never polls Gmail; pull-to-refresh stays a manual sync.

## Why both local notifications and FCM

| | Local (`flutter_local_notifications`) | Remote (FCM) |
|---|---|---|
| app **open** | shows the notification, updates UI | received, handed to the local layer to display |
| app **backgrounded / terminated** | cannot be triggered by the backend | system tray shows it; tap routes to the email |
| local reminders / alarm dialog / tap nav | yes | n/a |

## Backend

* **`device_registrations`** table — one row per FCM token (`user_pk`, `fcm_token`
  unique, `platform`, `device_label` *encrypted*, `active`, timestamps). A token
  that re-registers under a different user is **reassigned** (shared handset).
* **`notifications.pushed_at` / `push_status`** — a row is pushed **at most once**;
  repeated scheduler cycles never re-notify.
* **`app/services/fcm_client.py`** — `FirebaseFcmClient` (lazy `firebase-admin`) /
  `NullFcmClient` (unconfigured). Invalid tokens (`UNREGISTERED` /
  `SENDER_ID_MISMATCH`) are deactivated automatically.
* **`app/services/push_service.py`** — `PushNotificationService.send_to_user(...)`
  and `dispatch_unpushed(user_id)` (own short DB session, never raises). Called
  after `persist_decision`, `run_deadline_check`, `run_reminder_check`.
* **Payload** carries only ids — `notification_id`, `email_id`, `type`,
  `severity`, `requires_alarm`. **Never** OTPs, passwords, tokens, or the email
  subject/body. The title/body are generic per type (`"Important email" / "A new
  email needs your attention."`).

### Endpoints (bearer)

| Method | Path | |
|---|---|---|
| POST | `/api/v1/devices/register` | `{fcm_token, platform, device_label?, app_version?, previous_token?}` → device |
| POST | `/api/v1/devices/unregister` | `{fcm_token}` → deactivate |
| GET | `/api/v1/devices` | the current user's active devices |

`POST /api/v1/auth/logout` accepts an optional `{fcm_token}` and deactivates it.

### Configuration (server-side only — never in Flutter)

| Env | Meaning |
|---|---|
| `PUSH_ENABLED` | master switch (default `true`) |
| `FIREBASE_PROJECT_ID` | Firebase project id |
| `FIREBASE_CREDENTIALS_FILE` | path to the service-account JSON on the server |
| `FIREBASE_CREDENTIALS_JSON` | the JSON itself (alt; from a secret manager) |
| `PUSH_MAX_AGE_MINUTES` | don't push notifications older than this (default 120) |

Unconfigured ⇒ `NullFcmClient` ⇒ the pipeline runs, `push_status` is recorded as
`disabled`, local notifications still work when the app is open.

## Flutter

* **`lib/services/push_messaging_service.dart`** — `PushMessagingService` +
  `PushPlatform` abstraction (`FakePushPlatform` for tests).
* **`lib/services/firebase_push_platform.dart`** — real `PushPlatform` over
  `firebase_core` + `firebase_messaging`, plus the top-level
  `firebaseMessagingBackgroundHandler`. If the build has no `google-services.json`
  it degrades to *unavailable* and the app runs normally.
* Flow: `initialize()` → `Firebase.initializeApp()` (guarded) → request permission
  → `getToken()` → `POST /api/v1/devices/register` (after the user is
  authenticated) → listen `onTokenRefresh` (re-register with `previous_token`).
* Foreground messages are shown via `NotificationService.showRawPush(...)`.
* Background tap (`onMessageOpenedApp`) and terminated launch
  (`getInitialMessage`) route to `EmailDetailScreen`. Before the navigator/inbox
  exists the payload is held in `pendingPush` and replayed once the app is ready.
* Logout calls `PushMessagingService.onLogout()` (unregisters the token) **before**
  the session is revoked.

## Manual Firebase setup (required for real push)

1. Create a Firebase project; add an Android app with applicationId
   `com.amar.agent_amar`.
2. `flutterfire configure` (or download `google-services.json` into
   `frontend/android/app/`).
3. Uncomment the `com.google.gms.google-services` lines in
   `android/settings.gradle.kts` and `android/app/build.gradle.kts`.
4. Backend: **Project settings → Service accounts → Generate new private key**;
   put the JSON on the server and set `FIREBASE_CREDENTIALS_FILE` (+
   `FIREBASE_PROJECT_ID`). Do **not** commit it (`.gitignore` covers
   `google-services*.json` / `service-account*.json`).
5. iOS (later): add the app in Firebase, `GoogleService-Info.plist`, an APNs key.

## Security & privacy

* Every device token belongs to an authenticated user; a push is sent **only** to
  that user's active devices — never another user's token.
* No confidential content in the payload — the app fetches the authorised email
  after the tap.
* `device_label` is encrypted at rest (Phase 14).
* On logout the device is deactivated → no further pushes to that handset.
* When a different user signs in on the same device, the token is reassigned and
  the previous user stops receiving that device's pushes.
* Gmail disconnected ⇒ the scheduler skips that user ⇒ no new Gmail-driven pushes.

## Known limitations

* Single backend instance for the dedup guarantee (same as the scheduler / audit
  chain). Two instances could both attempt a push before `pushed_at` is written;
  the FCM message is idempotent enough that a rare duplicate is acceptable.
* No delivery receipts from FCM beyond accept/reject; `push_status` is
  `sent` / `partial` / `failed` / `no_devices` / `disabled`.
* iOS wiring is scaffolded but not exercised (no APNs key in this repo).
* Terminated-state data-only messages are not used — every push carries a
  `notification` block so the OS renders it without waking Dart.
