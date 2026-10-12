# tasks: Celery tasks & analysis pipeline
# run_analysis_sync is the main entry point for non-Celery environments
# run_analysis_pipeline is the Celery task (only available if celery is installed)
import asyncio
import logging as _logging

from app.tasks.pipeline import run_analysis_sync, run_analysis_async

try:
    from app.tasks.pipeline import run_analysis_pipeline, _get_celery_app
    celery_app = _get_celery_app()
except Exception:
    run_analysis_pipeline = None
    celery_app = None

_logger = _logging.getLogger(__name__)


def trigger_analysis(session_id: str) -> str:
    """Dispatch pipeline analysis for a session.

    Single entry point for both upload and re-trigger paths. Picks the
    backend based on settings.use_celery so callers stay unaware of the
    transport (asyncio in-process vs Celery worker process).

    Returns the dispatch mode string ("celery" / "asyncio") for log
    correlation. Never blocks — both paths return immediately and the
    pipeline runs in the background.

    Why a single helper:
      * upload.py and pipeline.py both trigger analysis; we don't want
        the asyncio-vs-Celery check duplicated (and silently diverging).
      * Falls back to asyncio when use_celery=True but the celery app is
        not initialized (e.g. redis offline) — better degraded service
        than a hard fail at trigger time. The fallback path keeps the
        Semaphore(1) backpressure so even degraded mode is safe.
    """
    from app.config import get_settings
    settings = get_settings()

    if settings.use_celery and run_analysis_pipeline is not None:
        try:
            run_analysis_pipeline.delay(session_id)
            _logger.info("[trigger][%s] dispatched via celery", session_id)
            return "celery"
        except Exception as e:
            # Don't lose the trigger — fall through to asyncio. Logged so
            # ops can see "celery dispatch failed, degraded to in-process".
            _logger.warning(
                "[trigger][%s] celery .delay() failed (%s); falling back to asyncio",
                session_id, e,
            )

    asyncio.create_task(run_analysis_async(session_id))
    _logger.info("[trigger][%s] dispatched via asyncio.create_task", session_id)
    return "asyncio"


__all__ = [
    "run_analysis_sync",
    "run_analysis_async",
    "run_analysis_pipeline",
    "celery_app",
    "trigger_analysis",
]
