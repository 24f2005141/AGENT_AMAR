# Sorted — Production Deployment

This is the deployment design for Sorted, written against what the repository
actually contains. It deliberately does **not** add infrastructure that the
workload does not need.

---

## 1. Architecture

```
                     INTERNET
                        │  HTTPS only
                        ▼
              ┌───────────────────┐
              │  TLS reverse proxy │   Caddy / Nginx / provider LB
              │  (certs, HTTP→S)   │
              └─────────┬─────────┘
                        │  127.0.0.1:8000
                        ▼
              ┌───────────────────┐
              │  API  (gunicorn +  │   SCHEDULER_ENABLED=false
              │  uvicorn workers)  │   stateless, scale horizontally
              └─────────┬─────────┘
                        │
        ┌───────────────┼────────────────┐
        │               │                │
        ▼               ▼                ▼
  ┌──────────┐   ┌────────────┐   ┌──────────────┐
  │PostgreSQL│   │  WORKER    │   │ FCM (Google) │
  │ private  │   │ exactly 1  │   │  outbound    │
  └──────────┘   │ SCHEDULER_ │   └──────────────┘
                 │ ENABLED=   │
                 │ true       │
                 └─────┬──────┘
                       │ private network only
                       ▼
                 ┌──────────┐        ┌──────────┐
                 │  Ollama  │        │ Local ML │ (in-process, joblib)
                 │ private  │        └──────────┘
                 └──────────┘
```

**Nothing but the API is reachable from the internet.** The mobile app talks
only to the API and to FCM. PostgreSQL, the worker and Ollama have no public
listener.

### Why no Redis/Celery
The scheduler already is the job pump: it walks connected users on an interval,
`GmailSyncService` is checkpointed (per-user history id), per-user locked, and
`persist_decision` is idempotent on `email_id`. For a handful of users on a
periodic tick, adding a broker would add two failure modes and an ops burden
for no throughput gain. The single place to swap the pump is `worker.py`.

---

## 2. Processes

| Process | Command | Scheduler | Replicas |
|---|---|---|---|
| API | `gunicorn app.main:app -k uvicorn.workers.UvicornWorker` (image default) | `SCHEDULER_ENABLED=false` | 1..N |
| Worker | `python -m worker` | `SCHEDULER_ENABLED=true` | **exactly 1** |
| Migrations | `alembic upgrade head` | — | once per deploy |

Running two workers would double-sync Gmail and double-send push. If you ever
need HA there, add a database advisory lock first.

---

## 3. Deploy sequence

```bash
# 1. Migrate (never done automatically by the app in production)
docker compose run --rm migrate

# 2. Start / update the services
docker compose up -d --build api worker

# 3. Verify
curl -fsS https://api.<domain>/health          # {"status":"ok",...}
curl -fsS https://api.<domain>/health/ready    # database: ok
```

The API **refuses to start** if the production database has not been migrated
(no `alembic_version` row) or if any required production setting is missing.

---

## 4. Ollama: keeping it private

Ollama has **no authentication of its own**. It must never be published.
Pick one:

1. **Same host / same private network (simplest).** Ollama binds
   `127.0.0.1:11434`; the worker reaches it over loopback or the private Docker
   network. Nothing to expose.
2. **Tailscale (recommended when the GPU box is elsewhere).** Put the worker
   and the Ollama host on one tailnet; set
   `OLLAMA_BASE_URL=http://<tailscale-name>:11434`. Traffic is WireGuard
   encrypted and the port stays off the public internet.
3. **Cloudflare Tunnel + Access** with a service token, if you must traverse
   NAT without a VPN.

The startup validator rejects an `OLLAMA_BASE_URL` that is plain HTTP to a
non-loopback address, so option 2/3 must be a private or TLS endpoint.

**Concurrency:** `LLM_MAX_CONCURRENT_REQUESTS=1` on limited hardware. Callers
queue up to `LLM_QUEUE_TIMEOUT_SECONDS` and then fall back to the local ML
classification — degraded, never down.

---

## 5. Google Cloud Console

1. APIs & Services → Credentials → your OAuth 2.0 **Web application** client.
2. **Authorized redirect URIs** — add exactly:
   `https://api.<your-domain>/api/v1/auth/google/callback`
3. Remove every `http://localhost:*`, `http://192.168.*` and old tunnel URI.
4. OAuth consent screen: publish it, and list the scopes actually requested
   (`openid`, `userinfo.email`, `userinfo.profile`, `gmail.readonly`,
   `gmail.send`). Gmail scopes are **restricted** — production access to real
   users requires Google's verification/security assessment.
5. Set `API_PUBLIC_BASE_URL=https://api.<your-domain>` and leave
   `GOOGLE_REDIRECT_URI` empty so it is derived (one source of truth).

---

## 6. Backups & recovery

Practical, not enterprise:

```bash
# Nightly logical backup (cron on the DB host)
docker compose exec -T db pg_dump -U sorted sorted | gzip \
  > /backups/sorted-$(date +%F).sql.gz
find /backups -name 'sorted-*.sql.gz' -mtime +14 -delete   # 14-day retention
```

* **Restore:** `gunzip -c backup.sql.gz | psql -U sorted sorted` on an empty DB.
* **Verify quarterly** by restoring into a scratch database — an untested
  backup is not a backup.
* **Off-host copy:** sync `/backups` to object storage; a disk failure that
  takes the DB also takes backups stored beside it.
* **Migration rollback:** `alembic downgrade -1`. Review the generated
  `downgrade()` before relying on it; destructive column drops are not
  reversible without the backup above.
* **Credential recovery:** `DATA_ENCRYPTION_KEY` is **not** in the database.
  If it is lost, every stored OAuth credential is unrecoverable and all users
  must reconnect Gmail. Store it in the platform secret manager plus one
  offline copy.

---

## 7. Hosting

Requirements: long-running processes (the worker is not serverless-friendly),
outbound access to Google APIs, a private path to Ollama, and PostgreSQL.

A single small VPS (2 vCPU / 4 GB) running this compose file behind Caddy meets
all of it and is the cheapest reliable option. Managed platforms (Fly.io,
Render, Railway) work equally well — run API and worker as separate services
and use their managed Postgres. Serverless (Vercel/Lambda) is **not** suitable
for the worker.

The LLM box stays wherever the hardware is, joined by Tailscale.
