"""FastAPI app with UTF-8 JSON response (ensure_ascii=False).

This ensures all Chinese characters are returned as UTF-8 strings,
not \\uXXXX escape sequences.
"""
import asyncio
import typing
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.config import get_settings
from app.database import engine
from app.models.base import Base
from app.models import Anchor, LiveSession, Danmaku, ScriptSegment, PipelineStep
from app.api.upload import router as upload_router
from app.api.sessions import router as sessions_router
from app.api.pipeline import router as pipeline_router
from app.api.embedding import router as embedding_router
from app.api.anchors import router as anchors_router
from app.api.debug import router as debug_router
from app.api.workspace import router as workspace_router
from app.api.dashboard import router as dashboard_router
from app.api.auth import router as auth_router
from app.api.admin_invitations import router as admin_invitations_router
from app.middleware.auth import validate_api_key
from app.middleware.rate_limit import limiter
from app.services.auth.current_user import get_admin_or_user
from slowapi.errors import RateLimitExceeded
from slowapi import _rate_limit_exceeded_handler

logger = logging.getLogger("uvicorn")
settings = get_settings()


def _setup_sentry() -> None:
    """Initialize Sentry if SENTRY_DSN is set; otherwise no-op.

    A5 (2026-05-30): default off in dev (empty DSN). Production deploys
    paste their DSN into .env to activate. Conservative settings:
      - send_default_pii=False: do not send IP/user info
      - with_locals=False: do not capture local variables (could leak
        danmaku content, video paths, API keys in stack traces)
      - traces_sample_rate=0: no perf monitoring yet (M2 if needed)
      - profiles_sample_rate=0: no profiling
    """
    dsn = (settings.sentry_dsn or "").strip()
    if not dsn:
        logger.info("[boot] Sentry disabled (SENTRY_DSN empty)")
        return
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration
        sentry_sdk.init(
            dsn=dsn,
            environment=settings.app_env,
            release=None,  # auto-detect from git if available
            send_default_pii=False,
            traces_sample_rate=0.0,
            profiles_sample_rate=0.0,
            integrations=[
                StarletteIntegration(transaction_style="endpoint"),
                FastApiIntegration(transaction_style="endpoint"),
            ],
            # Drop locals from stack frames — they may contain user content
            # (danmaku text, script segments, OCR raw text) that we should
            # not leak to Sentry.
            include_local_variables=False,
        )
        logger.info("[boot] Sentry initialized env=%s", settings.app_env)
    except Exception as e:
        logger.warning("Sentry init failed (non-fatal): %s", e)


_setup_sentry()


def _setup_pipeline_logger() -> None:
    """Attach a RotatingFileHandler to the `app` namespace so all business
    INFO/DEBUG logs land in backend/logs/pipeline.log.

    Why a dedicated file (not uvicorn stderr): uvicorn's reloader sometimes
    swallows reload-spawned worker stderr (we hit this on 2026-05-27 chasing
    the composite_score regression — `_step_scoring` printed nothing visible
    even though the code paths clearly called `_logger.info`). A real file
    handler decouples observability from uvicorn's stream plumbing and is
    rotation-safe across reloader restarts.

    The handler is attached to the `app` logger (parent of `app.tasks.*`,
    `app.services.*`, etc.), so all module-level `logging.getLogger(__name__)`
    inside the app inherit it without code changes.
    """
    log_path = Path(__file__).parent.parent / "logs" / "pipeline.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    app_logger = logging.getLogger("app")
    # If we've already attached the handler (e.g. uvicorn --reload re-import),
    # don't pile up duplicates.
    if any(getattr(h, "_livepulse_pipeline", False) for h in app_logger.handlers):
        return
    handler = RotatingFileHandler(
        str(log_path),
        maxBytes=50_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    handler._livepulse_pipeline = True  # marker for de-dup on reload
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    app_logger.addHandler(handler)
    app_logger.setLevel(logging.INFO)
    # Keep propagate=True so uvicorn stderr still shows lines for live debugging.
    # Smoke line — proves file handler is wired before any business code runs.
    app_logger.info("[boot] pipeline.log handler attached path=%s", log_path)
    # Boot fingerprint — single line that identifies THIS process for the
    # remainder of its lifetime. Restart scripts grep `boot-fingerprint`
    # to confirm a new worker came up; humans grep to find which worker
    # served a given request in historical logs.
    # See app/services/runtime_fingerprint.py for why this exists.
    from app.services.runtime_fingerprint import boot_log_line
    app_logger.info(boot_log_line())


_setup_pipeline_logger()


class UnicodeJSONResponse(JSONResponse):
    """JSON response that renders Chinese as UTF-8, not \\uXXXX escapes.

    By default, FastAPI/Starlette uses json.dumps with ensure_ascii=True,
    which converts all non-ASCII characters into \\uXXXX escape sequences.
    This class overrides that to output clean UTF-8.
    """
    def render(self, content: typing.Any) -> bytes:
        return json.dumps(
            content,
            ensure_ascii=False,
            allow_nan=False,
            indent=None,
            separators=(",", ":"),
        ).encode("utf-8")


async def _ensure_bucket() -> bool:
    try:
        from minio import Minio
        client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )
        found = client.bucket_exists(settings.minio_bucket)
        if not found:
            client.make_bucket(settings.minio_bucket)
        return True
    except Exception as e:
        logger.warning("MinIO not available: %s", e)
        return False


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # Ensure MinIO bucket exists (retry up to 3 times)
    for attempt in range(3):
        ok = await _ensure_bucket()
        if ok:
            break
        if attempt < 2:
            await asyncio.sleep(1)
    if not ok:
        logger.error("MinIO unavailable after 3 retries — upload API will fail")

    # Auto-recover zombie sessions (stuck in processing with no active worker)
    try:
        from app.services.session_recovery import recover_zombie_sessions
        recovered = await recover_zombie_sessions()
        if recovered:
            logger.info("Startup recovery: %d zombie sessions recovered", recovered)
    except Exception as e:
        logger.warning("Session recovery skipped: %s", e)

    # A1 2026-05-31: seed admin user from env if absent (idempotent).
    try:
        from app.services.auth.admin_seed import ensure_admin_seeded
        await ensure_admin_seeded()
    except Exception as e:
        logger.warning("Admin seed skipped (non-fatal): %s", e)

    # DEMO_MODE=1: seed synthetic sample sessions (no-op otherwise).
    try:
        from app.services.demo_seed import ensure_demo_seeded
        await ensure_demo_seeded()
    except Exception as e:
        logger.warning("Demo seed skipped (non-fatal): %s", e)

    yield
    await engine.dispose()


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
    default_response_class=UnicodeJSONResponse,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# A1 Day 3 P2: slowapi rate limiting — attach limiter to app state and
# register the 429 handler. Per-route @limiter.limit("…") decorators
# only fire when this wiring is in place.
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# A1 2026-05-31 / Day 3 P0-2: business routers now accept EITHER
#   * X-API-Key (admin scope, gated by ALLOW_ADMIN_API_KEY env) OR
#   * Bearer JWT (user scope, sub-only token).
# /auth/* stays open (register/login don't require pre-shared secret).
# /_debug/* keeps the strict X-API-Key gate — those are operator-only
# tools and JWT users should never reach them.
app.include_router(auth_router, prefix="/api/v1")
# A4 (2026-06-01): admin invitation management. router-level
# require_admin dep enforces admin-only; no business endpoint needs to
# repeat the check.
app.include_router(admin_invitations_router, prefix="/api/v1")
app.include_router(upload_router, prefix="/api/v1", dependencies=[Depends(get_admin_or_user)])
app.include_router(sessions_router, prefix="/api/v1", dependencies=[Depends(get_admin_or_user)])
app.include_router(pipeline_router, prefix="/api/v1", dependencies=[Depends(get_admin_or_user)])
app.include_router(embedding_router, prefix="/api/v1", dependencies=[Depends(get_admin_or_user)])
app.include_router(anchors_router, prefix="/api/v1", dependencies=[Depends(get_admin_or_user)])
app.include_router(workspace_router, prefix="/api/v1", dependencies=[Depends(get_admin_or_user)])
app.include_router(dashboard_router, prefix="/api/v1", dependencies=[Depends(get_admin_or_user)])
# Debug-only — operator endpoints for observability/recovery. Strict
# X-API-Key only; JWT users never get here.
app.include_router(debug_router, prefix="/api/v1", dependencies=[Depends(validate_api_key)])


@app.get("/api/v1/health")
async def health():
    """Structured health check — returns service status + runtime identity.

    Runtime fields (boot_id / git_sha / started_at) let restart scripts and
    operators confirm WHICH process is serving traffic right now — not just
    "uvicorn says ready". boot_id changes on every fresh process; git_sha
    changes when HEAD moves (suffixed `-dirty` when there are uncommitted
    changes). See runtime_fingerprint.py for why.
    """
    from app.database import engine
    from app.services.runtime_fingerprint import get_fingerprint

    # DB check
    db_status = "down"
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        db_status = "up"
    except Exception as e:
        logger.error("DB health check failed: %s", e)

    # MinIO check
    minio_status = "down"
    try:
        from minio import Minio
        client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )
        minio_status = "up" if client.bucket_exists(settings.minio_bucket) else "bucket_missing"
    except Exception as e:
        logger.error("MinIO health check failed: %s", e)

    overall = "ok" if db_status == "up" and minio_status == "up" else "degraded"

    # Surface a curated subset of the fingerprint. The full set is at
    # /api/v1/_debug/runtime — /health is the public-ish probe, _debug is
    # for operators. boot_id is the key field: restart scripts poll until
    # this changes to confirm a new process replaced the old.
    fp = get_fingerprint()
    return {
        "code": 0,
        "data": {
            "status": overall,
            "services": {
                "db": db_status,
                "minio": minio_status,
            },
            "runtime": {
                "boot_id": fp["boot_id"],
                "git_sha": fp["git_sha"],
                "started_at": fp["started_at"],
                "pid": fp["pid"],
            },
        },
        "message": "ok",
    }