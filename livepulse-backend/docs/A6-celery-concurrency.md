# A6 Celery Concurrency — dev & ops guide

**Status**: ship 2026-06-03 (3-day implementation, M1 internal beta scope).
**Scope**: replace single-process `asyncio.Semaphore(1)` with Celery worker
process pool so multiple uploads no longer serialize behind each other.

---

## Mental model

```
upload.py / pipeline.py
        │
        │ trigger_analysis(session_id)   ← single entry point, app/tasks/__init__.py
        ▼
   USE_CELERY=?
   ┌─true ──────────────────────────────────────────┐
   │  run_analysis_pipeline.delay(sid)              │
   │       │                                        │
   │       ▼ (over redis://localhost:6379/0)        │
   │  Celery worker process (separate from uvicorn) │
   │       │                                        │
   │       ▼                                        │
   │  _async_pipeline(...)                          │
   └─false ─────────────────────────────────────────┘
   │  asyncio.create_task(run_analysis_async(sid))  │
   │       │                                        │
   │       ▼ Semaphore(1) backpressure              │
   │  _async_pipeline(...)                          │
   └────────────────────────────────────────────────┘
```

**Key property**: the *pipeline itself* (`_async_pipeline`) is identical in
both modes. Only the dispatch transport changes.

---

## Local dev — single-command flows

```bash
# Full dev stack: postgres + minio + redis + backend (asyncio mode by default)
make up
make backend-bg

# Enable Celery mode (only when you want to validate the worker path)
echo "USE_CELERY=true" >> backend/.env
make worker-bg        # → /tmp/celery.log
make backend-bg       # restart backend so config reload picks up USE_CELERY

# Watch it work
make worker-logs      # tail -f /tmp/celery.log
make backend-logs     # tail -f /tmp/uvicorn.log
```

To go back to asyncio mode: delete the `USE_CELERY=true` line from
`backend/.env` and restart backend. Worker can keep running idle — costs nothing.

---

## Makefile targets

| Target | What |
|---|---|
| `make redis-up`     | start `workbuddy_redis` container (port 6379, no volume) |
| `make redis-down`   | stop it |
| `make redis-cli`    | interactive redis-cli shell |
| `make redis-ping`   | `PONG` if redis is reachable |
| `make worker`       | foreground celery worker (Ctrl-C to stop) |
| `make worker-bg`    | background worker → `/tmp/celery.log` |
| `make worker-stop`  | kill background worker |
| `make worker-logs`  | tail celery log |
| `make up` / `make down` | redis is now included alongside postgres/minio |

---

## Concurrency knob

```bash
WORKER_CONCURRENCY=4 make worker-bg     # override default 2
```

| Where | Recommended | Why |
|-------|-------------|-----|
| Laptop dev | 1-2 | Each worker loads whisper + sentence-transformers ≈ 2 GB RAM |
| A3 prod (2c4g ECS) | 2 | Box is tight; bumping past 2 risks OOM during peak |
| Larger prod box (8GB+) | 4 | Comfortable headroom |

---

## macOS fork-safety gotcha

Mac dev workers will hard-crash with `SIGABRT` (signal 6) on the first
task if you don't set `OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES`. This is
already baked into `make worker` / `make worker-bg`.

Background: macOS Objective-C runtime aborts when `fork()` is called
after certain runtimes (CoreFoundation, paddle, torch with MPS) have
initialized. Celery's default `prefork` pool hits this. Linux/Docker
deploy ignores the env var harmlessly.

---

## Retry policy

The celery task is configured as:

```python
@celery_app.task(
    max_retries=2,                # 3 total attempts
    autoretry_for=(Exception,),
    retry_backoff=30,             # 1st retry after 30s
    retry_backoff_max=300,        # cap at 5min
    retry_jitter=True,            # avoid thundering herd
)
```

Schedule: fail → wait 30s → fail → wait ~5min → fail → give up.

On final give-up, `_async_pipeline` writes `session.status='error'` +
`error_message` to DB so the UI shows the failure. The worker does NOT
emit a deadletter — M2 will add one when credits/refunds need it.

**What retries**: transient errors (aliyun OCR 5xx, asyncpg disconnect,
network hiccups). **What does not**: bad video file / missing model
weights — those raise immediately during step setup and waste worker
capacity if retried.

---

## Rate limit moved to Redis (same flag)

`USE_CELERY=true` also flips the slowapi rate limiter from in-memory to
Redis storage. Reason: with multiple uvicorn workers behind nginx, an
in-memory limiter is per-process and `5/min × 4 workers = 20/min real
limit` (= no limit). Coupling the two behind one flag keeps M1 simple.

See `app/middleware/rate_limit.py`.

---

## Trigger semantics

`trigger_analysis(session_id)` (in `app/tasks/__init__.py`):

1. If `USE_CELERY=true` AND celery app is initialized → `.delay()` to broker
2. Else → `asyncio.create_task(run_analysis_async(sid))` in current event loop

The function **never blocks** and returns one of `"celery"` / `"asyncio"`
for log correlation. The `/api/v1/pipeline/{sid}/trigger` endpoint
surfaces this via `response.data.mode` so you can verify mode end-to-end
without grepping logs.

**Degraded mode**: if `USE_CELERY=true` but `.delay()` raises (e.g. redis
went down), trigger_analysis logs a warning and falls back to asyncio.
Pipeline still runs, just on the uvicorn process — uptime preserved over
strict transport guarantee.

---

## Production deploy (A3 preview)

Production `.env`:
```
USE_CELERY=true
REDIS_URL=redis://redis:6379/0    # docker-compose service name
```

systemd units (one per process):
- `livepulse-backend.service` — uvicorn workers (gunicorn -k uvicorn.workers.UvicornWorker -w 4)
- `livepulse-worker.service`  — celery worker (concurrency=2)
- `livepulse-redis.service`   — redis container (via docker-compose)

Restart policy: both backend and worker use `Restart=on-failure`. Worker
crash on a poison-pill task → systemd respawns → task retry kicks in.

Full systemd unit files: TBD in A3 (`deploy/prod/systemd/*.service`).

---

## Troubleshooting

**Task triggered but never runs**
- Worker running? `ps aux | grep "celery -A app.tasks"` or `make worker-logs`
- Redis up? `make redis-ping` should return `PONG`
- Worker registered the task? Look for `[tasks] . app.tasks.pipeline.run_analysis_pipeline` in `/tmp/celery.log`

**Worker SIGABRTs immediately on first task (Mac)**
- The Makefile sets `OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES` automatically; if you're invoking celery directly, prepend it

**`/sessions/{id}/status` shows 0% forever in Celery mode**
- The step_tracker uses `await db.commit()` (not flush) since QA 2026-05-29 P-1, so cross-process visibility works. If it's truly stuck, check `pipeline_steps` table — empty rows means the worker died before the first step started

**rate limit feels too tight / loose after enabling celery**
- Storage backend changed memory → redis. Counters reset on the toggle. Limit values themselves (`5/minute`, etc.) didn't change.

---

## Rollback

Anything in this rollout is reversible:

| Change | Rollback |
|--------|----------|
| USE_CELERY flag | Delete the line from `.env`, restart backend |
| Rate-limit Redis swap | Same — `_build_limiter()` reads `settings.use_celery` |
| Retry policy | `tasks/pipeline.py` `_get_celery_task` — remove `autoretry_for` and friends |
| Worker entirely | `make worker-stop && make redis-down` |
| Makefile targets | revert Makefile diff |

Trigger helper (`app/tasks/__init__.py` `trigger_analysis`) is the only
code shape that callers depend on; reverting it means restoring the two
direct `asyncio.create_task(run_analysis_async(...))` lines in
`upload.py:188` and `api/pipeline.py:39`.
