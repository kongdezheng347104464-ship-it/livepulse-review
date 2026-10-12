"""Safe OCR wrappers — always return structured result, never crash pipeline.

Three wrappers for three OCR extractors:
  safe_extract_danmaku       — legacy floating-danmaku region (pre-2026-05-28)
  safe_extract_viewer_count  — top-right "在线观众·N" badge
  safe_extract_interactions  — right-side interaction list (the new primary path)

All three: failure is logged + degraded to empty data, never raised. Pipeline
steps that depend on these should treat empty data as "OCR layer reported
nothing" and degrade gracefully (lower analysis_confidence, banner notes,
etc.) rather than aborting.
"""
import asyncio
import logging

from app.services.pipeline_safety import SafeResult

logger = logging.getLogger(__name__)


async def safe_extract_danmaku(frame_paths: list, interval_sec: int = 30) -> SafeResult:
    """Legacy floating-danmaku-region extractor. Kept for backfill scenarios.

    New code should use safe_extract_interactions instead — the interaction
    list is the structured event stream; the floating layer was the source
    of gift/system noise that we explicitly walked away from.
    """
    try:
        from app.services.ocr_service import extract_danmaku
        records = await asyncio.to_thread(
            extract_danmaku, frame_paths, interval_sec
        )
        return SafeResult.success(records)
    except Exception as e:
        logger.warning("OCR (danmaku) failed (non-fatal, pipeline continues): %s", e)
        return SafeResult.failure(str(e), data=[])


async def safe_extract_viewer_count(frame_paths: list, interval_sec: int = 30) -> SafeResult:
    """Extract per-frame viewer count from the "在线观众·N" badge frames.

    Returns SafeResult with data=list[{timestamp_sec, viewer_count}] on
    success, data=[] on failure. Failure here is non-fatal because the
    normalization layer can fall back to using session totals when the
    series is unavailable (degraded mode, not aborted).
    """
    try:
        from app.services.aliyun_ocr_service import extract_viewer_count
        records = await asyncio.to_thread(
            extract_viewer_count, frame_paths, interval_sec
        )
        return SafeResult.success(records)
    except Exception as e:
        logger.warning("OCR (viewer_count) failed (non-fatal, pipeline continues): %s", e)
        return SafeResult.failure(str(e), data=[])


async def safe_extract_all(frame_paths: list, interval_sec: int = 30) -> SafeResult:
    """2026-06-03 Day 1 — combined single-OCR extractor.

    Returns SafeResult with data = {"interactions": [...], "viewer": [...]}.
    Single OCR call per frame replaces the 2× calls that
    safe_extract_interactions + safe_extract_viewer_count used to make.

    Failure semantics match the two split wrappers: any uncaught error
    degrades to data with empty lists, never raises. Per-frame OCR
    failures are absorbed inside extract_all_per_frame (logged WARNING).
    """
    try:
        from app.services.aliyun_ocr_service import extract_all_per_frame
        bundle = await asyncio.to_thread(
            extract_all_per_frame, frame_paths, interval_sec
        )
        return SafeResult.success(bundle)
    except Exception as e:
        logger.warning("OCR (all) failed (non-fatal, pipeline continues): %s", e)
        return SafeResult.failure(str(e), data={"interactions": [], "viewer": []})


async def safe_extract_interactions(frame_paths: list, interval_sec: int = 30) -> SafeResult:
    """Extract structured event lines from the right-side interaction list.

    Returns SafeResult with data=list[{timestamp_sec, username, content,
    source_type, raw_ocr_text}] on success. Failure here is non-fatal but
    leaves the session with no live_events at all, which the downstream
    Q1 invariant correctly treats as "nothing to evaluate" — better than
    silently passing in garbage from a different region.
    """
    try:
        from app.services.aliyun_ocr_service import extract_interactions
        records = await asyncio.to_thread(
            extract_interactions, frame_paths, interval_sec
        )
        return SafeResult.success(records)
    except Exception as e:
        logger.warning("OCR (interactions) failed (non-fatal, pipeline continues): %s", e)
        return SafeResult.failure(str(e), data=[])
