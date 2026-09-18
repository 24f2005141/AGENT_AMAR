# Real Google login on an Android device — HTTPS tunnel setup

Testing the **"Continue with Google"** flow (Phase 15) from a phone / tablet on the
LAN, against a FastAPI backend running on a dev PC.

## Why a tunnel is needed

The OAuth flow (from `backend/app/api/routes_auth.py` + `gmail_auth_service.py`):

```
Flutter (tablet)                     Browser (tablet)              Backend (PC)
──────────────────────────────────────────────────────────────────────────────
POST /api/v1/auth/google/start ───────────────────────────────────▶  builds Google
   ◀── { authorization_url, flow_id }                                consent URL with
                                                                     redirect_uri =
open authorization_url ──────────▶  accounts.google.com               GOOGLE_REDIRECT_URI
                                    user consents
                                         │
                                    302 to GOOGLE_REDIRECT_URI ─────▶  GET /api/v1/auth/
                                                                       google/callback
                                                                       · exchange code
                                                                       · upsert user
                                                                       · create session
                                                                       · park token in
                                                                         _PENDING[state]
                                    ◀── "You're signed in" HTML
poll GET /api/v1/auth/google/session?flow_id= ───────────────────▶  returns session_token
   ◀── { session_token, user }                                       once, then 410
```

**Only the browser → `GOOGLE_REDIRECT_URI` → callback hop needs a public URL.**
Everything Flutter does (`/start`, `/session`, and every `/api/v1/*` call after
login) stays on the LAN.

Google **rejects `http://` redirect URIs for any host except `localhost`**. On the
tablet, `localhost` is the tablet — not the PC. So the backend must be reachable
at a real `https://…` URL: a Cloudflare Tunnel.

## What changes

| Setting | Value | Why |
|---|---|---|
| `backend/.env` → `API_PUBLIC_BASE_URL` | `https://<fastapi-tunnel-domain>` | the backend's public origin; `GOOGLE_REDIRECT_URI` is derived from it |
| `backend/.env` → `GOOGLE_REDIRECT_URI` | **leave blank** (or set it explicitly to the same `…/api/v1/auth/google/callback`) | blank ⇒ `Settings.google_redirect_uri_resolved` = `{API_PUBLIC_BASE_URL}/api/v1/auth/google/callback`, used identically in the consent URL and the token exchange |
| Google Cloud Console → OAuth client → Authorized redirect URIs | add `https://<fastapi-tunnel-domain>/api/v1/auth/google/callback` | Google validates an exact match |
| `backend/.env` → `APP_ENV` | `development` (unchanged) | `OAUTHLIB_INSECURE_TRANSPORT` is irrelevant with an `https://` redirect |
| Flutter `--dart-define=API_BASE_URL` | `http://192.168.1.10:8000` for LAN, **or** the FastAPI tunnel URL if the device is off-LAN | every request (incl. `/auth/google/start` + `/session`) is a relative path on this origin; the app never calls the callback |
| `frontend/android/app/src/main/AndroidManifest.xml` | `<queries>` browser intent (already applied) | Android 11+ package visibility — `url_launcher` can't open the consent page without it |

The **FastAPI tunnel and the Ollama tunnel (`OLLAMA_BASE_URL`) are separate** — do
not point one at the other. FastAPI needs **no** `--proxy-headers` /
forwarded-header trust: it never derives URLs from the inbound request; the
redirect URI is purely configuration. No application source is hardcoded — the
tunnel hostname lives only in `.env` / the `--dart-define` flag.

---

## 1. Install cloudflared (Windows)

```powershell
winget install --id Cloudflare.cloudflared
# or: download cloudflared-windows-amd64.exe from
#     https://github.com/cloudflare/cloudflared/releases and put it on PATH
```

## 2. Start the backend (LAN + localhost)

```powershell
cd "c:\MIRTTUL\Projects\AGENT AMAR\backend"
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

* `--host 0.0.0.0` so the tablet reaches it on the LAN for normal API traffic.
* cloudflared connects to it via `localhost:8000`.
* Allow **inbound TCP 8000 on the Private network** in Windows Defender Firewall
  (one-time) so the tablet can reach the LAN IP.

## 3. Start the tunnel

### Option A — Quick tunnel (no account, URL changes each run)

```powershell
cloudflared tunnel --url http://localhost:8000
```

Prints something like:

```
+--------------------------------------------------------------------------------------------+
|  Your quick Tunnel has been created! Visit it at:                                           |
|  https://random-words-1234.trycloudflare.com                                                |
+--------------------------------------------------------------------------------------------+
```

Copy that `https://…trycloudflare.com` host. **It is different every time you
restart the tunnel** — each new URL must be re-added in Google Cloud Console and
re-set in `.env`.

### Option B — Named tunnel (Cloudflare account + a domain, stable URL)

```powershell
cloudflared tunnel login                          # opens browser, pick your zone
cloudflared tunnel create agent-amar
cloudflared tunnel route dns agent-amar amar-dev.example.com
cloudflared tunnel run --url http://localhost:8000 agent-amar
```

Stable host `https://amar-dev.example.com` — register it **once** in Google
Console and leave it in `.env`. Recommended if you'll test more than once.

## 4. Put the tunnel URL in `backend/.env`

```env
# the FastAPI tunnel origin — GOOGLE_REDIRECT_URI is derived from it
API_PUBLIC_BASE_URL=https://random-words-1234.trycloudflare.com
GOOGLE_REDIRECT_URI=
```

(use your actual tunnel host, no trailing slash, no path. Set `GOOGLE_REDIRECT_URI`
explicitly only if you need to override the derived
`https://random-words-1234.trycloudflare.com/api/v1/auth/google/callback`.)

Leave everything else as-is — note the Ollama tunnel is a **different** host:

```env
APP_ENV=development
GOOGLE_CLIENT_ID=<your client id>
GOOGLE_CLIENT_SECRET=<your client secret>
OLLAMA_BASE_URL=https://mir.ollamaserver.com     # separate tunnel, separate box
```

## 5. Register the redirect URI in Google Cloud Console

**APIs & Services → Credentials →** your OAuth 2.0 Client ID (Web application) **→
Authorized redirect URIs → + ADD URI:**

```
https://random-words-1234.trycloudflare.com/api/v1/auth/google/callback
```

Exact match, no trailing slash. **Save.** Changes take effect within a minute or
two. Keep the old `http://localhost:8000/...` entry too — desktop testing still
uses it.

> Also confirm on the **OAuth consent screen** that your Google account is under
> **Test users** (if the app is in "Testing" publishing status).

## 6. Restart FastAPI — **required**

`get_settings()` is `@lru_cache`d, so `.env` changes are only picked up on a fresh
process:

```
Ctrl+C  →  uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Verify:

```powershell
curl.exe -s https://random-words-1234.trycloudflare.com/api/v1/system/status
# {"backend":{"status":"online"}, ...}
curl.exe -s -X POST https://random-words-1234.trycloudflare.com/api/v1/auth/google/start
# authorization_url should contain: redirect_uri=https%3A%2F%2Frandom-words-1234.trycloudflare.com%2F...
```

## 7. Full Flutter rebuild (manifest change needs it)

```powershell
cd "c:\MIRTTUL\Projects\AGENT AMAR\frontend"
flutter clean
flutter pub get
# LAN (tablet + PC on the same network):
flutter run -d RZ2Y100W00N --dart-define=API_BASE_URL=http://192.168.1.10:8000
# OR, if the device is off-LAN, point it at the FastAPI tunnel instead:
# flutter run -d RZ2Y100W00N --dart-define=API_BASE_URL=https://<fastapi-tunnel-domain>
```

* `flutter clean` + full run because the `<queries>` manifest change is native —
  hot reload / hot restart do **not** apply it.
* Always pass `--dart-define=API_BASE_URL` for a real device. Without it the app
  falls back to `ApiConfig._devFallbackBaseUrl` (`http://192.168.1.10:8000`) —
  fine only if that is actually your PC's current LAN address.
* On the **same LAN**, keep `API_BASE_URL` on the LAN address — Flutter never
  calls the OAuth callback, so routing normal traffic through the tunnel would
  only add latency. Use the tunnel URL only when the device can't reach the LAN.

---

## End-to-end test checklist

1. **Backend reachable both ways**
   - `curl http://192.168.1.10:8000/api/v1/system/status` (LAN) → `200`
   - `curl https://<tunnel>/api/v1/system/status` (tunnel) → `200`
2. **App launches** on the tablet → **Login screen** (`Continue with Google`).
   Any stale token from a previous DB is cleared automatically: `bootstrap()` →
   `GET /api/v1/auth/me` → `401` → session cleared → Login (no crash).
3. **Tap "Continue with Google"** → button shows *"Waiting for browser…"* and the
   **system browser opens** the Google consent screen.
   *If it does not open:* the manifest `<queries>` fix isn't in the running build
   — re-run `flutter clean` + `flutter run`.
4. **Consent** on Google → browser shows the dark **"You're signed in"** page.
   Backend log: `GET /api/v1/auth/google/callback ... 200`.
   *If browser shows "Sign-in failed":* `redirect_uri` mismatch — the `.env`
   value, the Google Console entry, and `authorization_url`'s `redirect_uri` must
   be byte-identical; restart FastAPI after editing `.env`.
5. **Return to the app** — within ~2 s the poll (`GET /api/v1/auth/google/session?flow_id=`,
   `202` → `200`) completes and the app moves to the **Home screen**.
6. **Status bar** at the top of Home: `🟢 Backend Online   🟢 AI Online`
   (`ollama` / your model). If the tablet ever loses the LAN: `🔴 Backend Offline
   ⚪ AI Unknown`, recovering on the next resume / pull-to-refresh.
7. **DB now populated** (on the PC):
   ```
   cd backend && python -c "import sqlite3;d=sqlite3.connect('agent_amar.db');\
   print('users',d.execute('select count(*) from users').fetchone()[0],\
   'sessions',d.execute('select count(*) from app_sessions').fetchone()[0],\
   'oauth',d.execute('select count(*) from oauth_credentials').fetchone()[0])"
   ```
   → `users 1  sessions 1  oauth 1`.
8. **Session persists** — kill and relaunch the app (`q` then `flutter run`) → it
   goes straight to Home (stored token + `GET /api/v1/auth/me` → `200`), no
   re-login.
9. **Pull-to-refresh** on Home → `POST /api/v1/gmail/sync` runs, status bar
   refreshes, no errors.
10. **Logout** (account icon → Log out) → `POST /api/v1/auth/logout` revokes the
    session → back to Login. Relaunch → stays on Login.

## Notes

* The tunnel is only in the path for the ~10 s OAuth browser dance and any future
  re-login / token refresh. All other traffic is LAN.
* Quick-tunnel URL changed? Redo steps 4–6 (edit `.env`, add the URI in Google
  Console, restart FastAPI). No rebuild needed — Flutter doesn't know about it.
* `trycloudflare.com` hosts are occasionally flagged by Google Safe Browsing; if
  the consent redirect is blocked, use a named tunnel on your own domain
  (Option B).
* Production uses a real HTTPS origin for `GOOGLE_REDIRECT_URI`; this doc is a
  dev-only shortcut.
