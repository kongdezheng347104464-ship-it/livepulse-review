"""Analysis pipeline with PipelineContext step orchestration.

Each step is an independent async function that receives and returns PipelineContext.
Fatal errors raise StepError. Non-fatal errors raise StepSkipped.
The orchestrator iterates PIPELINE_STEPS and handles error propagation.
"""
import asyncio
import json
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from decimal import Decimal
from collections import Counter, defaultdict
import logging as _logging

_celery_app = None
_logger = _logging.getLogger(__name__)

# Single-slot semaphore: only one pipeline runs at a time per uvicorn worker.
# Prevents OOM when multiple uploads land concurrently (each pipeline spawns
# whisper / paddle / sentence-transformers and would otherwise contend for
# the same GPU/CPU/RAM). Queueing is implicit — `asyncio.create_task` returns
# immediately and the task waits on this semaphore before doing real work.
_PIPELINE_SEMAPHORE: "asyncio.Semaphore | None" = None


def _get_pipeline_semaphore() -> "asyncio.Semaphore":
    """Lazy-create the semaphore so it binds to the running event loop."""
    global _PIPELINE_SEMAPHORE
    if _PIPELINE_SEMAPHORE is None:
        _PIPELINE_SEMAPHORE = asyncio.Semaphore(1)
    return _PIPELINE_SEMAPHORE

from app.config import get_settings
from app.database import async_session_factory
from app.models.session import LiveSession
from app.models.script_segment import ScriptSegment
from app.models.danmaku import Danmaku
from app.services.video_processor import process_video
from app.services.script_analyzer import analyze_script_segments
from app.services.danmaku_analyzer import analyze_danmakus
from app.services.correlation import compute_script_danmaku_correlation
from app.services.intent.embedding_intent_analyzer import get_intent_analyzer
from app.services.tactics.embedding_tactic_analyzer import get_tactic_analyzer
from app.services.correlation.semantic_correlation import compute_semantic_correlation
from app.services.live_rhythm_analyzer import analyze_rhythm
from app.services.step_tracker import StepTracker, create_step_trackers
from app.services.pipeline_safety import SafeResult
from typing import Any

settings = get_settings()


# ============================================================
#  Pipeline error hierarchy
# ============================================================

class StepError(Exception):
    """Fatal step error — abort entire pipeline."""
    pass

class StepSkipped(Exception):
    """Non-fatal step error — log and continue to next step."""
    pass


# ============================================================
#  PipelineContext: carries mutable state through steps
# ============================================================

@dataclass
class PipelineContext:
    db: Any
    session: LiveSession
    session_id: str
    steps: dict[str, StepTracker]
    local_video: Path
    output_dir: Path
    minio_bucket: str = ""
    minio_object: str = ""

    # Accumulated state from steps — each field is a typed channel, never mix schemas
    proc: Any = None           # video processing result (T4)
    segments: list = field(default_factory=list)      # raw ASR segments (T5)
    analyzed: list = field(default_factory=list)      # analyzed segments only (T7) — no OCR/other data
    ocr_raw: Any = None        # OCR raw event records (T6) — post 2026-05-28: structured list-line events, not raw OCR text
    viewer_records: list = field(default_factory=list)  # viewer count time series (T6b) — populated by _step_viewer_count
    # 2026-06-03 Day 1: _step_ocr now also produces viewer records (single
    # OCR call shared between two extractors). _step_viewer_count drains
    # this cache instead of re-running OCR. Empty when the combined
    # extractor wasn't used (legacy / fallback paths).
    _viewer_cache: list = field(default_factory=list)

    # 2026-06-03 Day 3: lock serializing db ops across concurrent pipeline
    # branches (ASR branch || OCR branch). Outside the parallel phase the
    # lock is uncontested. All step functions that call ctx.db.execute /
    # ctx.db.add / step_tracker.* honor it.
    _db_lock: Any = None
    danmaku_result: Any = None # danmaku analysis result (T8)
    correlation: Any = None    # semantic correlation (T9)


def _get_celery_app():
    global _celery_app
    if _celery_app is not None:
        return _celery_app
    try:
        from celery import Celery
        from celery.signals import worker_process_init
        app = Celery("livestream", broker=settings.redis_url)
        app.conf.update(
            task_serializer="json", accept_content=["json"],
            result_serializer="json", timezone="UTC", enable_utc=True,
            task_track_started=True, task_acks_late=True, worker_prefetch_multiplier=1,
            # Celery 6.0 deprecates auto-retry on startup; opt back in
            # explicitly so M1 worker doesn't hard-crash if redis is a
            # second slow to accept connections on boot.
            broker_connection_retry_on_startup=True,
        )

        @worker_process_init.connect
        def _init_db_for_worker(**_kwargs):
            """Rebuild SQLAlchemy engine in each forked worker.

            Without this, the asyncpg pool initialized in MainProcess
            (before fork) is inherited as dupe socket FDs that crash
            with "got Future attached to a different loop" on first
            task. See app/database.py:reset_engine_for_fork docstring.
            """
            from app.database import reset_engine_for_fork
            reset_engine_for_fork()

        _celery_app = app
    except Exception:
        _celery_app = False
    return _celery_app if _celery_app else None


def run_analysis_sync(session_id: str) -> dict:
    import asyncio
    import logging as _log
    _log.basicConfig(level=_log.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_async_pipeline(None, session_id))
    finally:
        loop.close()


async def run_analysis_async(session_id: str) -> dict:
    import logging as _log
    from datetime import datetime, timezone
    _log.basicConfig(level=_log.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    sem = _get_pipeline_semaphore()
    # Log queueing so users see "your analysis is waiting in line" in dev logs
    # rather than silent stalls when 2+ uploads land at once.
    if sem.locked():
        _logger.info("Pipeline busy — session %s queued", session_id)

    async with sem:
        try:
            # Mark analysis start time (but NOT status — _async_pipeline handles status)
            async with async_session_factory() as db:
                session = await db.get(LiveSession, session_id)
                if session:
                    session.analysis_started_at = datetime.now(timezone.utc)
                    await db.commit()

            return await _async_pipeline(None, session_id)

        except Exception as e:
            _logging.exception("Analysis failed for %s: %s", session_id, e)
            # Write error state back to DB so UI can show failure reason
            try:
                async with async_session_factory() as db:
                    session = await db.get(LiveSession, session_id)
                    if session:
                        session.status = "error"
                        session.error_message = str(e)[:500]
                        session.analysis_finished_at = datetime.now(timezone.utc)
                        await db.commit()
            except Exception as inner_e:
                _logging.error("Failed to write error state for %s: %s", session_id, inner_e)
            return {"code": 1, "message": str(e)}


def _get_celery_task():
    celery_app = _get_celery_app()
    if celery_app is None:
        return None

    # Retry policy (A6 Day 3 — 2026-06-03):
    #   1st failure → wait 30s  → retry
    #   2nd failure → wait 5min → retry
    #   3rd failure → give up; _async_pipeline writes session.status='error'
    #                 + error_message to DB, UI shows failure.
    #
    # Why retry transient errors (aliyun OCR 5xx, asyncpg disconnect):
    #   * OCR/ASR APIs return 5xx ~1% of the time during peak hours
    #   * asyncpg connection drops happen on DB restarts
    #   * Without retry, user sees "analysis failed" + has to re-trigger
    #     by hand. With retry, the system self-heals.
    #
    # Why ≤3 attempts: anything > 3 means a real bug (model loaded wrong,
    # bad video file, downstream API outage) — burning more retries just
    # hides the real signal and chews worker capacity.
    @celery_app.task(
        name="app.tasks.pipeline.run_analysis_pipeline",
        bind=True,
        max_retries=2,           # 2 retries = 3 total attempts
        autoretry_for=(Exception,),
        retry_backoff=30,        # 1st retry after 30s
        retry_backoff_max=300,   # cap backoff at 5min
        retry_jitter=True,       # randomize ± to avoid thundering herd
    )
    def run_analysis_pipeline(self, session_id: str):
        """Reuse a single per-process event loop across tasks.

        Earlier version created a fresh loop per task and called
        loop.close() at the end. That broke asyncpg's connection pool —
        the pool was bound to the first loop and subsequent tasks raised
        "got Future attached to a different loop".

        Trade-off: never closing the loop means a tiny FD leak on
        worker shutdown. Acceptable: workers are long-lived, OS
        reclaims on process exit. The alternative (close + dispose pool
        each task) loses the entire benefit of connection pooling.
        """
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_closed():
                raise RuntimeError("loop closed")
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        return loop.run_until_complete(_async_pipeline(self, session_id))

    return run_analysis_pipeline


run_analysis_pipeline = _get_celery_task()


# ============================================================
#  Orchestrator — iterates step functions, handles error propagation
# ============================================================


async def _async_pipeline(task, session_id: str):
    async with async_session_factory() as db:
        result = await db.get(LiveSession, session_id)
        if result is None:
            _logger.error("Session %s not found", session_id)
            return {"code": 1, "message": "session not found"}

        session = result
        if session.status == "processing":
            _logger.warning("Session %s already processing", session_id)
            return {"code": 0, "message": "already processing"}
        session.status = "processing"
        await db.commit()

        # Day 3 (2026-06-03): lock serializing db ops between concurrent
        # pipeline branches. Created here so it's tied to this session run.
        db_lock = asyncio.Lock()
        steps = await create_step_trackers(db, session_id, db_lock=db_lock)

        # Analysis ALWAYS reads the master (uploaded original) — never the
        # playback rendition. ctx only ever carries the master location, so
        # downstream steps physically cannot read the degraded playback copy.
        minio_path = session.master_video_path
        if not minio_path:
            session.status = "error"
            await db.commit()
            return {"code": 1, "message": "no video path"}

        bucket, object_name = minio_path.split("/", 1)
        local_video = Path(tempfile.gettempdir()) / object_name
        output_dir = Path(tempfile.mkdtemp(prefix="livestream_"))

        ctx = PipelineContext(
            db=db, session=session, session_id=session_id,
            steps=steps, local_video=local_video, output_dir=output_dir,
            minio_bucket=bucket, minio_object=object_name,
            _db_lock=db_lock,
        )

        try:
            # ---- Download from MinIO ----
            _download_video(bucket, object_name, local_video)

            # ---- Day 3 (2026-06-03): phase orchestration ----
            #
            # Phase 1 (sequential): video processing — produces frames + segments
            #                       basis for both ASR and OCR branches.
            # Phase 2 (PARALLEL):   ASR branch  || OCR branch.
            #                       Compute-heavy work in each step's to_thread
            #                       call overlaps; db ops serialize via ctx._db_lock.
            # Phase 3 (sequential): correlation + scoring + playback —
            #                       need both branches' outputs (analyzed + danmaku).
            #
            # Dependency analysis:
            #   ASR branch:  asr → script → early_analysis → tactics
            #   OCR branch:  ocr → viewer_count → danmaku
            #     - viewer_count consumes ctx._viewer_cache from ocr
            #     - danmaku consumes ctx.ocr_raw from ocr
            #   Convergence: correlation needs ctx.analyzed + ctx.danmaku_result.
            #
            # Wall-clock gain (16min video, observed): ASR ~46s, OCR ~31s.
            # Parallel max ≈ 46s vs serial 77s — saves ~31s per session.
            async def _run_step(step_fn):
                try:
                    return await step_fn(ctx)
                except StepSkipped as e:
                    _logger.info("Step %s skipped: %s", step_fn.__name__, e)
                    return ctx

            async def _run_branch(branch_steps, branch_name):
                _logger.info("[pipeline] branch %s starting (%d steps)",
                             branch_name, len(branch_steps))
                for step_fn in branch_steps:
                    await _run_step(step_fn)
                _logger.info("[pipeline] branch %s done", branch_name)

            # Phase 1
            await _run_step(_step_video)

            # Phase 2 — parallel branches
            asr_branch = [_step_asr, _step_script, _step_early_analysis, _step_tactics]
            ocr_branch = [_step_ocr, _step_viewer_count, _step_danmaku]
            await asyncio.gather(
                _run_branch(asr_branch, "ASR"),
                _run_branch(ocr_branch, "OCR"),
            )

            # Phase 3 — sequential convergence
            for step_fn in [_step_correlation, _step_scoring, _step_playback_encode]:
                await _run_step(step_fn)

            # ---- Pipeline complete ----
            _logger.info("Session %s analyzed: score=%s, segments=%d",
                         session_id, ctx.session.composite_score, len(ctx.analyzed))

            # Fire-and-forget feishu notification — but only once per session.
            # Re-running analysis on an already-notified session shouldn't
            # spam the channel.
            if settings.feishu_webhook_url and ctx.session.feishu_notified_at is None:
                ctx.session.feishu_notified_at = datetime.now(timezone.utc)
                asyncio.create_task(_send_feishu_notification_async(
                    ctx.session, ctx.analyzed, ctx.danmaku_result, ctx.correlation,
                ))

        except StepError as e:
            _logger.exception("Pipeline fatal error for %s: %s", session_id, e)
            session.status = "error"
            session.error_message = str(e)[:500]
            session.analysis_finished_at = datetime.now(timezone.utc)
            if task is not None:
                try:
                    raise task.retry(exc=e, countdown=60)
                except Exception as retry_err:
                    _logger.warning("Celery retry failed for %s: %s", session_id, retry_err)
        except Exception as e:
            _logger.exception("Pipeline unexpected error for %s: %s", session_id, e)
            session.status = "error"
            session.error_message = str(e)[:500]
            session.analysis_finished_at = datetime.now(timezone.utc)
        finally:
            await db.commit()
            # Post-commit truth probe: re-read the row to confirm DB matches
            # what _step_scoring claimed to write. Catches JSONB mutation
            # pitfalls, stale ORM state, or "merged from elsewhere" overwrites.
            try:
                from sqlalchemy import select as _select
                row = (await db.execute(
                    _select(LiveSession).where(LiveSession.id == session_id)
                )).scalar_one_or_none()
                if row is not None:
                    _sum = row.analysis_summary or {}
                    _logger.info(
                        "[commit][%s] post-commit DB truth: composite_score=%s "
                        "summary.composite_score=%s summary.scores.composite=%s "
                        "summary.scores keys=%s status=%s",
                        session_id[:8], row.composite_score,
                        _sum.get("composite_score") if isinstance(_sum, dict) else "<not-dict>",
                        (_sum.get("scores") or {}).get("composite") if isinstance(_sum, dict) else "<not-dict>",
                        list((_sum.get("scores") or {}).keys()) if isinstance(_sum, dict) else "<not-dict>",
                        row.status,
                    )
            except Exception as _probe_err:
                _logger.warning("[commit][%s] post-commit probe failed: %s",
                                session_id[:8], _probe_err)
            # Temp cleanup (audit 2026-06-03 P0-1). Previously only the
            # downloaded source video was unlinked, leaking:
            #   - output_dir: all extracted frames (~50-200MB for a 1h video)
            #   - .playback.mp4: the H.264 re-encode (~139MB), already
            #     uploaded to MinIO so the local copy is dead weight
            # On a 40GB ECS disk a dozen long-video analyses would fill /tmp
            # and hang the box. rmtree/unlink are best-effort — a cleanup
            # failure must never mask the analysis result.
            import shutil
            try:
                if local_video.exists():
                    local_video.unlink(missing_ok=True)
                playback = local_video.with_suffix(".playback.mp4")
                if playback.exists():
                    playback.unlink(missing_ok=True)
                if output_dir.exists():
                    shutil.rmtree(output_dir, ignore_errors=True)
            except Exception as _cleanup_err:
                _logger.warning("[cleanup][%s] temp cleanup failed (non-fatal): %s",
                                session_id[:8], _cleanup_err)

    return {"code": 0, "message": "ok", "data": {"session_id": session_id}}


# ============================================================
#  Step functions — each transforms PipelineContext
# ============================================================

async def _step_video(ctx: PipelineContext) -> PipelineContext:
    await ctx.steps["video_processing"].start({"video": str(ctx.local_video)})
    try:
        _logger.info("T4: Processing video %s", ctx.minio_object)
        # ffmpeg / ffprobe via subprocess.run — synchronous and can take
        # minutes on a multi-GB recording. Without to_thread it blocks
        # the event loop, so every other API request (sessions list,
        # health, even /_debug/runtime) stalls until video_processing
        # finishes. Discovered 2026-05-27 testing a 48-min upload.
        ctx.proc = await asyncio.to_thread(
            process_video,
            str(ctx.local_video), output_dir=ctx.output_dir,
            danmaku_region=settings.danmaku_region,
        )
        ctx.session.duration_sec = int(ctx.proc.duration_sec)
        # Pin source resolution to the session row so future re-crops or
        # debug reruns use the same relative→pixel conversion. Without
        # this, a backfill against a different default would silently
        # shift ROI rects by 1-2% and break OCR. See roi_config.py.
        ctx.session.source_resolution = ctx.proc.resolution

        # ---- Invariants — fail loudly rather than letting bogus inputs
        # cascade silently through ASR / OCR / scoring. The 2026-05-27 test
        # showed how a 81-second file masqueraded as a successful analysis;
        # if it had actually been a corrupt upload, the user would have
        # gotten a session full of plausible-looking but meaningless numbers.
        # ---------------------------------------------------------------
        _sid = str(ctx.session.id)[:8]
        _problems: list[str] = []
        d = ctx.proc.duration_sec
        if d is None or d <= 0:
            _problems.append(f"duration_sec={d} (must be > 0)")
        elif d > 24 * 3600:
            # 24h ceiling — a single broadcaster session longer than this
            # is almost certainly a metadata bug, not a real recording.
            _problems.append(f"duration_sec={d} exceeds 24h ceiling (likely corrupt metadata)")
        # Audio output must exist and have non-trivial content — silent
        # ASR failures often produce 0-byte audio files.
        try:
            audio_size = Path(ctx.proc.audio_path).stat().st_size
        except (OSError, AttributeError) as e:
            _problems.append(f"audio_path stat failed: {e}")
            audio_size = -1
        if 0 <= audio_size < 1024:
            _problems.append(f"audio_path size={audio_size} bytes (likely silent ffmpeg failure)")
        # Frame count sanity — frames are captured at fixed interval, so
        # count should track duration. Off-by-one tolerated. Zero frames
        # on a multi-minute video means the OCR feed is broken.
        # NOTE: ocr_frame_interval defaults to 30s; keep this in sync if it changes.
        frame_count = len(ctx.proc.frame_paths) if ctx.proc.frame_paths is not None else 0
        if d and d > 60 and frame_count == 0:
            _problems.append(
                f"frame_count=0 on a {d:.0f}s video — frame capture failed silently"
            )
        if _problems:
            _msg = "video_processing invariant violation: " + "; ".join(_problems)
            _logger.error("[video][%s] %s", _sid, _msg)
            raise StepError(_msg)

        _logger.info(
            "[video][%s] OK duration=%.1fs audio=%.1fMB frames=%d",
            _sid, d, audio_size / 1024 / 1024 if audio_size > 0 else 0, frame_count
        )

        await ctx.steps["video_processing"].done({
            "duration_sec": ctx.proc.duration_sec,
            "audio_path": str(ctx.proc.audio_path),
            "audio_size_mb": round(audio_size / 1024 / 1024, 1) if audio_size > 0 else 0,
            "frame_count": frame_count,
        })
    except StepError:
        # Already logged + step.fail not yet called — record and re-raise.
        await ctx.steps["video_processing"].fail("invariant violation (see log)")
        raise
    except Exception as e:
        await ctx.steps["video_processing"].fail(str(e))
        raise StepError(str(e)) from e
    return ctx


async def _step_asr(ctx: PipelineContext) -> PipelineContext:
    # Check if segments already exist in DB (e.g., from a previous partial run)
    from sqlalchemy import select, func
    from contextlib import nullcontext
    from app.models.script_segment import ScriptSegment
    _lock = ctx._db_lock or nullcontext()
    async with _lock:
        existing_count = (await ctx.db.execute(
            select(func.count()).where(ScriptSegment.session_id == ctx.session_id)
        )).scalar()
    if existing_count > 0:
        _logger.info("T5: ASR transcription SKIPPED — %d segments already in DB", existing_count)
        # Hydrate segments from DB rather than leaving a placeholder. Any
        # downstream step that touches ctx.segments must see real data —
        # previous placeholder ("loaded_from_db") risked silent corruption.
        async with _lock:
            rows = (await ctx.db.execute(
                select(ScriptSegment)
                .where(ScriptSegment.session_id == ctx.session_id)
                .order_by(ScriptSegment.start_sec)
            )).scalars().all()
        ctx.segments = [
            {"start_sec": r.start_sec, "end_sec": r.end_sec, "text": r.text}
            for r in rows
        ]
        await ctx.steps["asr"].skip(f"{existing_count} segments already exist")
        return ctx

    await ctx.steps["asr"].start({"audio_path": str(ctx.proc.audio_path)})
    try:
        _logger.info("T5: ASR transcription")
        if settings.use_local_asr:
            from app.services.local_asr_service import transcribe_local
            ctx.segments = await asyncio.to_thread(transcribe_local, str(ctx.proc.audio_path))
        else:
            from app.services.asr_service import transcribe
            ctx.segments = await transcribe(str(ctx.proc.audio_path))
        await ctx.steps["asr"].done({
            "segment_count": len(ctx.segments),
            "total_chars": sum(len(s["text"]) for s in ctx.segments),
        })
    except Exception as e:
        await ctx.steps["asr"].fail(str(e))
        raise StepError(str(e)) from e
    return ctx


async def _step_script(ctx: PipelineContext) -> PipelineContext:
    # Check if segments are already analyzed in DB (e.g., from a previous partial run)
    from sqlalchemy import select
    from contextlib import nullcontext
    _lock = ctx._db_lock or nullcontext()
    async with _lock:
        existing = (await ctx.db.execute(
            select(ScriptSegment).where(
                ScriptSegment.session_id == ctx.session_id,
                ScriptSegment.script_type.isnot(None),
            ).order_by(ScriptSegment.start_sec)
        )).scalars().all()
    if existing:
        _logger.info("T7: Script analysis SKIPPED — %d analyzed segments in DB", len(existing))
        ctx.analyzed = [
            {
                "id": s.id,  # DB primary key — required for step-level persistence
                "start_sec": s.start_sec, "end_sec": s.end_sec,
                "text": s.text, "script_type": s.script_type or "chitchat",
                "speech_rate": s.speech_rate or 3.0, "keywords": s.keywords or [],
                "product_mentions": s.product_mentions or [], "score": s.score or 0,
                "analysis_tags": s.analysis_tags or {},
            }
            for s in existing
        ]
        await ctx.steps["script_analysis"].skip(f"loaded {len(existing)} analyzed segments from DB")
        return ctx

    await ctx.steps["script_analysis"].start({"segment_count": len(ctx.segments)})
    try:
        _logger.info("T7: Script analysis")
        # jieba segmentation + per-segment scoring across hundreds of
        # segments. CPU-bound, multi-second on long videos. to_thread
        # keeps the event loop responsive so /sessions polling stays
        # fresh during analysis.
        ctx.analyzed = await asyncio.to_thread(analyze_script_segments, ctx.segments)
        async with _lock:
            for seg in ctx.analyzed:
                db_seg = ScriptSegment(
                    session_id=ctx.session_id,
                    start_sec=seg["start_sec"], end_sec=seg["end_sec"],
                    text=seg["text"], script_type=seg["script_type"],
                    speech_rate=seg["speech_rate"], keywords=seg["keywords"],
                    product_mentions=seg["product_mentions"], score=seg["score"],
                    analysis_tags=seg.get("analysis_tags"),
                )
                ctx.db.add(db_seg)
                await ctx.db.flush()  # generate DB id
                seg["id"] = db_seg.id  # store back to ctx for downstream persistence
        await ctx.steps["script_analysis"].done({
            "segment_count": len(ctx.analyzed),
            "types": dict(Counter(s["script_type"] for s in ctx.analyzed)),
            "avg_score": round(sum(float(s["score"]) for s in ctx.analyzed if s.get("score")) / max(len(ctx.analyzed), 1), 1),
        })
    except Exception as e:
        await ctx.steps["script_analysis"].fail(str(e))
        raise StepError(str(e)) from e
    return ctx


async def _step_early_analysis(ctx: PipelineContext) -> PipelineContext:
    """B5 真分层 (2026-05-30): script-only early insights.

    Runs right after _step_script — before tactics/OCR/danmaku/correlation/
    scoring. Goal: deliver Top 3 actionable feedback in ~5 minutes (after
    ASR + script_analysis) instead of forcing user to wait for the full
    20-minute pipeline.

    Strict contract (see docs/intent-classification-roadmap.md AI 守则 #3):
      - Output dimensions are 7-set {开场/教学/产品讲解/痛点覆盖/促销频率/
        话题稳定度/节奏}, NEVER include conversion/engagement/rhythm —
        those belong to full insights and can only be computed with
        danmaku data.
      - early_* fields are PRESERVED across _step_scoring's rebuild of
        analysis_summary (see scoring step).

    Non-fatal: if extractor or generator crashes, log + skip. Pipeline
    continues to full analysis without preview.
    """
    from app.services.insight_engine.early_signals_extractor import extract_early_signals
    from app.services.insight_engine.early_insight_generator import generate_early_insights

    if not ctx.analyzed:
        await ctx.steps["early_analysis"].skip("no analyzed segments — script step produced nothing")
        return ctx

    await ctx.steps["early_analysis"].start({"segment_count": len(ctx.analyzed)})
    try:
        _logger.info("T7.5: Early insight generation (script-only, %d segments)", len(ctx.analyzed))
        signals = await asyncio.to_thread(
            extract_early_signals,
            ctx.analyzed,
            ctx.session.duration_sec,
        )
        result = await asyncio.to_thread(generate_early_insights, signals, "general_v1")

        # Merge into existing analysis_summary (preserves anything that
        # might have been written earlier; rebuilds aren't expected here
        # but defensive). Reassign whole dict — SQLAlchemy detects.
        existing = dict(ctx.session.analysis_summary or {})
        existing["early_signals"] = signals
        existing["early_insights"] = result["early_insights"]
        existing["early_summary"] = result["early_summary"]
        existing["early_threshold_set"] = result["threshold_set"]
        existing["early_weights_version"] = result["weights_version"]
        existing["early_ready"] = True
        ctx.session.analysis_summary = existing

        await ctx.steps["early_analysis"].done({
            "insight_count": len(result["early_insights"]),
            "threshold_set": result["threshold_set"],
            "signals": signals,
        })
        _logger.info("T7.5: Early insights ready — %d items, summary=%r",
                     len(result["early_insights"]), result["early_summary"][:60])
    except Exception as e:
        _logger.warning("Early insight step failed (non-fatal): %s", e)
        await ctx.steps["early_analysis"].skip(str(e))
    return ctx


async def _step_tactics(ctx: PipelineContext) -> PipelineContext:
    """Semantic tactic analysis — non-fatal."""
    try:
        tactic_analyzer = get_tactic_analyzer()
        # Embedding-based tactic classification across all segments —
        # sentence-transformers encode + similarity, CPU-bound and the
        # single slowest step after ASR. Wrap to keep event loop free.
        tactic_results = await asyncio.to_thread(
            tactic_analyzer.analyze_batch,
            [s["text"] for s in ctx.analyzed],
        )
        for seg, tact in zip(ctx.analyzed, tactic_results):
            if not isinstance(seg.get("analysis_tags"), dict):
                seg["analysis_tags"] = {}
            if tact["tactics"]:
                seg["analysis_tags"]["embedding_tactics"] = tact["tactics"]
            seg["analysis_tags"]["tactic_score_distribution"] = tact.get("score_distribution", {})
        _logger.info("Semantic tactic analysis: %d segments processed", len(ctx.analyzed))

        # Persist embedding_tactics to DB — ctx is NOT the authority, DB is
        from sqlalchemy.orm.attributes import flag_modified
        persisted = 0
        for seg in ctx.analyzed:
            seg_id = seg.get("id")
            tags = seg.get("analysis_tags") or {}
            if not seg_id or "embedding_tactics" not in tags:
                continue
            db_seg = await ctx.db.get(ScriptSegment, seg_id)
            if db_seg is None:
                continue
            merged = dict(db_seg.analysis_tags or {})
            merged["embedding_tactics"] = tags["embedding_tactics"]
            db_seg.analysis_tags = merged
            flag_modified(db_seg, "analysis_tags")  # JSONB deep mutation not auto-detected
            persisted += 1
        await ctx.db.flush()
        _logger.info("Persisted embedding_tactics to %d/%d segments", persisted, len(ctx.analyzed))
    except Exception as e:
        # exception() not warning() — QA 2026-05-29 P0-4: silent
        # "name 'nn' is not defined" hid the real cause (embedding model
        # never loaded due to dead Windows cache_folder path) for weeks.
        # Full traceback now lands in pipeline.log every time.
        _logger.exception("Semantic tactic analysis failed (non-fatal): %s", e)
    return ctx


async def _step_ocr(ctx: PipelineContext) -> PipelineContext:
    """OCR interaction-list extraction — non-fatal.

    Step name stays "ocr" for FSM continuity (pipeline_steps table and
    frontend progress index would otherwise need migration). The semantics
    changed on 2026-05-28: instead of cropping the floating-danmaku layer
    over the video (which mixed gift icons + system messages + chat into
    one untyped stream), we now crop the right-side interaction list and
    parse it as a structured event protocol — username : content lines
    with reliable gift/system prefixes.

    Output schema on ctx.ocr_raw (records ready for _step_danmaku):
      [
        {timestamp_sec, username, content, source_type, raw_ocr_text},
        ...
      ]

    On 2026-05-28 we moved from a cropped interaction-region ROI to
    full-frame OCR. Reason: ROI-based extraction had hard dependencies
    on UI tab state (Douyin's "全部" vs "贡献者") and recording window
    geometry, which broke real-world recordings. The source_classifier
    regex requires username:content structure which only appears in
    the interaction list area — floating-danmaku text in the video
    body is captured but lacks structure and ends up classified as
    UNKNOWN (then dropped at persistence time, see _step_danmaku).
    """
    frames = ctx.proc.full_frame_paths
    await ctx.steps["ocr"].start({"frame_count": len(frames)})
    _logger.info("T6: OCR combined extraction (frames=%d, single-call merged 2026-06-03)", len(frames))
    # 2026-06-03 Day 1 merge: one OCR call per frame extracts both the
    # interaction lines AND the viewer/like signal. The viewer chunk is
    # cached on ctx for _step_viewer_count to consume — saves ~10 min on
    # a 1h video by halving OCR API calls.
    from app.services.safe_ocr_wrapper import safe_extract_all
    ocr_result: SafeResult = await safe_extract_all(frames, interval_sec=30)
    if ocr_result.ok:
        bundle = ocr_result.data or {}
        interactions = bundle.get("interactions", [])
        viewer_records = bundle.get("viewer", [])
        from collections import Counter
        st_dist = dict(Counter(r["source_type"] for r in interactions))
        _logger.info(
            "[ocr][%s] interactions=%d source_type_dist=%s viewer_cached=%d",
            str(ctx.session.id)[:8], len(interactions), st_dist, len(viewer_records),
        )
        await ctx.steps["ocr"].done({
            "interaction_count": len(interactions),
            "source_type_dist": st_dist,
            "viewer_cached": len(viewer_records),
        })
        ctx.ocr_raw = interactions          # for _step_danmaku
        ctx._viewer_cache = viewer_records  # for _step_viewer_count (no extra OCR)
    else:
        await ctx.steps["ocr"].skip(f"OCR failed (non-fatal): {ocr_result.error}")
    return ctx


async def _step_viewer_count(ctx: PipelineContext) -> PipelineContext:
    """Extract per-frame viewer counts from the top-right badge — non-fatal.

    Reads the "在线观众·N" overlay on each frame and persists the time
    series to viewer_snapshots. Non-fatal because the absence of this
    series only degrades normalization metrics to their absolute-value
    fallbacks — it does NOT corrupt anything that's already computed.

    Schema in ctx.viewer_records (for downstream metric computation):
      [{timestamp_sec, viewer_count}, ...]
    Gaps are explicit absences (frames where the regex didn't match are
    skipped rather than zero-filled).

    Uses FULL FRAME (not viewer ROI) as of 2026-05-28. The viewer ROI
    crop turned out to be just as fragile as the interaction ROI —
    pixel coordinates depend on the recording window geometry, which
    we can't control. The _VIEWER_RE regex is tight enough to pick
    the viewer-count badge text out of full-frame OCR output, so the
    spatial localization isn't paying for itself.
    """
    frames = ctx.proc.full_frame_paths
    await ctx.steps["viewer_count"].start({"frame_count": len(frames)})

    # 2026-06-03 Day 1 merge: prefer the cache produced by _step_ocr
    # (single OCR call shared with the interactions extractor). Only fall
    # back to a fresh OCR pass if the cache is empty AND _step_ocr didn't
    # explicitly run — i.e. legacy rerun paths or partial pipeline.
    if ctx._viewer_cache:
        records = ctx._viewer_cache
        _logger.info(
            "T6b: viewer-count consumed from _step_ocr cache (records=%d, no OCR call)",
            len(records),
        )
        # Adapt to the SafeResult shape the rest of this step expects
        res = SafeResult.success(records)
    else:
        _logger.info("T6b: viewer cache empty — fallback to direct OCR (frames=%d)", len(frames))
        from app.services.safe_ocr_wrapper import safe_extract_viewer_count
        res = await safe_extract_viewer_count(frames, interval_sec=30)

    if not res.ok or not res.data:
        await ctx.steps["viewer_count"].skip(
            f"No viewer counts extracted ({res.error if not res.ok else 'empty'})"
        )
        ctx.viewer_records = []
        return ctx

    # Persist to viewer_snapshots — row-based table chosen specifically
    # for downstream SQL queries (rolling avg, spike detection, growth
    # rate, cross-session JOIN). See viewer_snapshot.py for rationale.
    # like_count may be None on any individual frame; that's fine — the
    # MAX() aggregate in trend API still surfaces the session total.
    from app.models import ViewerSnapshot
    from sqlalchemy import delete as _sa_delete
    from contextlib import nullcontext
    # Idempotent re-run: clear any prior snapshots for this session before
    # inserting, otherwise re-analysis accumulates duplicate rows (bug
    # observed 2026-06-03: a re-analyzed session had 2× the frame rows).
    # Day 3: db_lock so parallel branches don't race on the shared session.
    _lock = ctx._db_lock or nullcontext()
    async with _lock:
        await ctx.db.execute(
            _sa_delete(ViewerSnapshot).where(ViewerSnapshot.session_id == ctx.session_id)
        )
        for r in res.data:
            ctx.db.add(ViewerSnapshot(
                session_id=ctx.session_id,
                timestamp_sec=r["timestamp_sec"],
                viewer_count=r["viewer_count"],
                like_count=r.get("like_count"),
            ))

    # Filter None before aggregating: since the like-count OCR work
    # (2026-06-03), a frame may carry like_count but no viewer_count (the
    # 在线观众 badge wasn't in the cropped region / unreadable). Those
    # rows have viewer_count=None; sum()/max() over them would TypeError
    # and leave viewer_peak/avg unset → frontend shows "无在线人数数据".
    counts = [r["viewer_count"] for r in res.data if r.get("viewer_count") is not None]
    avg = sum(counts) / len(counts) if counts else 0
    peak = max(counts) if counts else 0
    # Also stash on the session row — used by anyone reading session
    # totals without a JOIN to viewer_snapshots.
    ctx.session.viewer_peak = peak
    ctx.session.viewer_avg = int(avg)
    ctx.viewer_records = res.data

    # Like-count summary for the step log — useful for spotting OCR
    # regression at a glance (e.g. "snapshots=24 likes=0 likes_max=0"
    # = the like regex didn't match anything, investigate).
    like_values = [r["like_count"] for r in res.data if r.get("like_count") is not None]
    likes_max = max(like_values) if like_values else 0
    likes_hits = len(like_values)

    _logger.info(
        "[viewer_count][%s] snapshots=%d avg=%d peak=%d likes_hits=%d likes_max=%d",
        str(ctx.session.id)[:8], len(res.data), int(avg), peak,
        likes_hits, likes_max,
    )
    await ctx.steps["viewer_count"].done({
        "snapshot_count": len(res.data),
        "avg": int(avg),
        "peak": peak,
        "likes_hits": likes_hits,
        "likes_max": likes_max,
    })
    return ctx


async def _step_danmaku(ctx: PipelineContext) -> PipelineContext:
    """Live-event analysis — sentiment, intent, and DB persistence.

    Input shape (post 2026-05-28 rebuild) — ctx.ocr_raw is now:
        [{timestamp_sec, username, content, source_type, raw_ocr_text}, ...]
    each row already structurally classified by source_classifier. The
    legacy contract was just {timestamp_sec, content}; the extra fields
    are passed through to the Danmaku rows so downstream Q1 invariants
    can filter on source_type and the audit layer can read raw_ocr_text.

    Step name retained as `danmaku_analysis` for FSM continuity. The
    rows now span all SourceType values, not just speech — see the
    Danmaku model docstring's "soft rename" note.

    Non-fatal failure: if the analyzer crashes, we still attempt to
    persist the raw classified rows so the Q1 invariant at least has
    something to operate on; only the sentiment/intent layers are lost.
    """
    raw_events = ctx.ocr_raw  # typed channel, never mixed with ctx.analyzed
    ctx.ocr_raw = None         # clear after consumption

    if not raw_events:
        await ctx.steps["danmaku_analysis"].skip("No OCR data")
        return ctx

    try:
        await ctx.steps["danmaku_analysis"].start({"raw_count": len(raw_events)})
        _logger.info("T8: Live-event analysis (events=%d)", len(raw_events))

        # analyze_danmakus operates on raw {timestamp_sec, content} pairs.
        # We feed only the SPEECH-typed events through it: sentiment +
        # intent classification are language-content analyses, undefined
        # for gift/system events whose body is platform-fixed text.
        speech_events = [r for r in raw_events if r.get("source_type") == "speech"]
        non_speech = [r for r in raw_events if r.get("source_type") != "speech"]
        _logger.info(
            "[danmaku_analysis][%s] speech=%d non_speech=%d",
            str(ctx.session.id)[:8], len(speech_events), len(non_speech),
        )

        # QA 2026-05-29 P0-6 diagnostic trace — the 1h15min QA run logged
        # `speech=581 non_speech=601` here, then nothing until scoring
        # reported `danmaku_total=0`. Adding sub-phase logs so the next
        # failure points to the exact dead line instead of a 100-line
        # silent region.
        _sid_short = str(ctx.session.id)[:8]
        _logger.info("[danmaku.dbg][%s] phase=before-analyze speech_for_analyzer=%d",
                     _sid_short, len(speech_for_analyzer := [
            {"timestamp_sec": r["timestamp_sec"], "content": r["content"]}
            for r in speech_events
        ]))
        analyzed_speech = await asyncio.to_thread(analyze_danmakus, speech_for_analyzer)
        _logger.info("[danmaku.dbg][%s] phase=after-analyze records=%d",
                     _sid_short, len((analyzed_speech or {}).get("records") or []))

        # Merge sentiment back into the structured speech_events list,
        # keyed by (timestamp_sec, content) — the same pair the analyzer
        # used internally. Non-speech events get sentiment=None.
        sentiment_by_key: dict[tuple, str | None] = {
            (r["timestamp_sec"], r["content"]): r.get("sentiment")
            for r in (analyzed_speech.get("records") or [])
        }
        for ev in speech_events:
            ev["sentiment"] = sentiment_by_key.get(
                (ev["timestamp_sec"], ev["content"])
            )
        for ev in non_speech:
            ev["sentiment"] = None

        # Semantic intent analysis: same as sentiment, only meaningful
        # for speech. Failures stay sub-step-local — DB persistence below
        # still runs even if embedding intent crashes.
        try:
            intent_analyzer = get_intent_analyzer()
            dm_texts = [r["content"] for r in speech_events]
            if dm_texts:
                intent_results = await asyncio.to_thread(
                    intent_analyzer.analyze_batch, dm_texts,
                )
                for r, ir in zip(speech_events, intent_results):
                    r["intent_type"] = ir["intent_type"]
                    r["intent_confidence"] = ir["confidence"]
                    r["intent_method"] = ir["method"]
                    r["intent_score_distribution"] = ir.get("score_distribution", {})
        except Exception as e:
            # P0-6: exception() not warning() — full traceback to pin down
            # the next time intent analysis crashes (was suspected of B-8).
            _logger.exception("Semantic intent analysis failed (non-fatal): %s", e)

        _logger.info("[danmaku.dbg][%s] phase=after-intent", _sid_short)

        # Build the final list. Drop UNKNOWN-typed events: they're
        # OCR noise (floating-danmaku text without username:content
        # structure, button labels, banner text). Persisting them
        # would corrupt total_danmaku counts and pollute cross-session
        # baselines. The classifier still logged each unknown line in
        # source_classifier for audit; that's the diagnostic surface.
        all_events = speech_events + non_speech
        events_to_persist = [
            r for r in all_events if r.get("source_type") != "unknown"
        ]
        dropped_unknown = len(all_events) - len(events_to_persist)
        if dropped_unknown > 0:
            _logger.info(
                "[danmaku_analysis][%s] dropped %d unknown events (kept %d)",
                str(ctx.session.id)[:8], dropped_unknown, len(events_to_persist),
            )

        # Username normalization — strip OCR-induced UI pollution so
        # COUNT(DISTINCT normalized_username) gives stable cardinality
        # for Engaged Audience Rate. NULL means "do not count" (invalid,
        # anonymous, or fully stripped). The monitoring log below is
        # the primary signal that Douyin UI changed and patterns need
        # extending — watch the valid/total ratio across sessions.
        from app.services.live_event.username_normalizer import (
            normalize_username, classify_outcome,
        )
        norm_outcomes = {"valid": 0, "invalid": 0, "anonymous": 0}
        invalid_samples: list[str] = []  # first few invalid raws for log
        for r in events_to_persist:
            raw_user = r.get("username")
            normalized = normalize_username(raw_user)
            r["normalized_username"] = normalized
            outcome = classify_outcome(raw_user, normalized)
            norm_outcomes[outcome] += 1
            # Capture a few non-valid samples so the operator can audit
            # rule gaps without hitting the DB. Cap at 5 to keep the
            # log line bounded.
            if outcome != "valid" and raw_user and len(invalid_samples) < 5:
                invalid_samples.append(raw_user)

        total_for_norm = sum(norm_outcomes.values())
        _logger.info(
            "[normalize][%s] raw=%d valid=%d invalid=%d anonymous=%d "
            "(valid_pct=%.0f%%) samples=%s",
            str(ctx.session.id)[:8],
            total_for_norm,
            norm_outcomes["valid"],
            norm_outcomes["invalid"],
            norm_outcomes["anonymous"],
            (norm_outcomes["valid"] / total_for_norm * 100) if total_for_norm else 0,
            invalid_samples,
        )

        _logger.info("[danmaku.dbg][%s] phase=before-persist count=%d",
                     _sid_short, len(events_to_persist))
        # Day 3 (2026-06-03): batch add under db_lock so parallel ASR branch
        # doesn't trample on session state mid-loop.
        from contextlib import nullcontext as _nullctx_dnm
        async with (ctx._db_lock or _nullctx_dnm()):
            for r in events_to_persist:
                ctx.db.add(Danmaku(
                    session_id=ctx.session_id,
                    username=r.get("username"),
                    normalized_username=r.get("normalized_username"),
                    content=r["content"],
                    timestamp_sec=r["timestamp_sec"],
                    source_type=r["source_type"],
                    raw_ocr_text=r.get("raw_ocr_text"),
                    sentiment=r.get("sentiment"),
                    intent=r.get("intent_type") or r.get("intent"),
                ))
            ctx.session.total_danmaku = len(events_to_persist)
        _logger.info("[danmaku.dbg][%s] phase=after-persist queued=%d total_danmaku=%d",
                     _sid_short, len(events_to_persist), ctx.session.total_danmaku)

        # Expose to downstream steps in the legacy danmaku_result shape
        # so correlation / scoring don't need to be retrofit. They still
        # consume `records` and `total`; the new source_type field is
        # carried through but harmless to consumers that ignore it.
        # Note: downstream sees only events_to_persist (unknowns excluded)
        # to keep totals consistent between DB and in-memory ctx.
        ctx.danmaku_result = {
            "total": len(events_to_persist),
            "records": events_to_persist,
        }

        await ctx.steps["danmaku_analysis"].done({
            "total_persisted": len(events_to_persist),
            "ocr_raw_count": len(all_events),
            "dropped_unknown": dropped_unknown,
            "speech_count": len(speech_events),
            "non_speech_count": len(non_speech),
            "username_valid": norm_outcomes["valid"],
            "username_invalid": norm_outcomes["invalid"],
            "username_anonymous": norm_outcomes["anonymous"],
            "sentiments": dict(Counter(
                r.get("sentiment") for r in speech_events
            )),
        })
    except Exception as e:
        # P0-6: silent skip was hiding catastrophic data loss (581 speech
        # events vanished in QA 2026-05-29 with no error log). exception()
        # forces full traceback. Mark step as failed (not skipped) so
        # the invariant harness and UI can distinguish "we deliberately
        # had no data" from "we crashed silently".
        _logger.exception("[danmaku_analysis][%s] FATAL: %s",
                          str(ctx.session.id)[:8], e)
        await ctx.steps["danmaku_analysis"].fail(str(e))
        ctx.danmaku_result = None
    return ctx


async def _step_correlation(ctx: PipelineContext) -> PipelineContext:
    """Script-danmaku correlation — non-fatal."""
    if not (ctx.danmaku_result and ctx.danmaku_result.get("records") and ctx.analyzed):
        await ctx.steps["correlation"].skip("Missing danmaku or script data")
        return ctx

    try:
        await ctx.steps["correlation"].start({})
        _logger.info("T9: Semantic correlation analysis")
        # Window-based correlation over segments × danmakus —
        # O(segments × danmakus_in_window) pure Python.
        ctx.correlation = await asyncio.to_thread(
            compute_script_danmaku_correlation,
            ctx.analyzed, ctx.danmaku_result["records"],
        )
        try:
            # Embedding similarity per window — CPU-bound, same as tactic.
            semantic_corr = await asyncio.to_thread(
                compute_semantic_correlation,
                ctx.analyzed, ctx.danmaku_result["records"],
            )
            ctx.correlation["semantic"] = semantic_corr
        except Exception as e:
            _logger.warning("Semantic correlation failed (non-fatal): %s", e)
        await ctx.steps["correlation"].done({"has_correlation": True})
    except Exception as e:
        await ctx.steps["correlation"].skip(str(e))
    return ctx


async def _step_scoring(ctx: PipelineContext) -> PipelineContext:
    """Scoring + summary + insight engine — fatal."""
    await ctx.steps["scoring"].start({})
    _sid = str(ctx.session.id)[:8]
    _logger.info("[scoring][%s] step_start analyzed=%d danmaku_total=%d duration_sec=%s",
                 _sid, len(ctx.analyzed), (ctx.danmaku_result or {}).get("total", 0), ctx.session.duration_sec)
    try:
        # Legacy: per-segment averaged content score (still used as content
        # sub-score fallback when rhythm.type_distribution is unavailable).
        speech_scores = [float(s["score"]) for s in ctx.analyzed if isinstance(s, dict) and s.get("score")]
        script_score = round(sum(speech_scores) / len(speech_scores), 1) if speech_scores else 50.0
        _logger.info("[scoring][%s] script_score=%s (avg of %d segment scores)",
                     _sid, script_score, len(speech_scores))

        # _build_semantic_summary / _build_analysis_summary are pure-Python
        # aggregations over hundreds of segments + thousands of danmakus.
        # Fast on small data, but still O(n) work and on 3h sessions adds
        # up. Keep event loop free via to_thread.
        semantic_summary = await asyncio.to_thread(
            _build_semantic_summary, ctx.danmaku_result, ctx.analyzed,
        )
        analysis_summary = await asyncio.to_thread(
            _build_analysis_summary, ctx.analyzed, ctx.session.duration_sec or 0,
        )
        analysis_summary["semantic"] = semantic_summary

        # ---- Sub-scores (each 0-100, calibrated to industry benchmarks) ----
        # Benchmarks below come from head-of-vertical beauty MCN data plus
        # informal field observation. They give a 0-100 distribution where:
        #   * 50 ≈ industry median for a mid-tier broadcaster
        #   * 80+ ≈ top-decile execution
        #   * <30 ≈ needs improvement
        # Constants are documented inline so we can adjust them via A/B.
        duration_min = max((ctx.session.duration_sec or 0) / 60.0, 1.0)

        # ---- v5 normalized metrics (must compute BEFORE engagement_score) ----
        # Day 1 (be8e6b4): stored alongside scores but composite unchanged.
        # Day 2 (this commit): engagement_score now consumes
        # engaged_audience_rate when it's available. Computing here (before
        # engagement_score) instead of after the scores block lets the
        # engagement formula use the rate without restructuring the whole
        # function. Failure is non-fatal — engagement falls back to pure
        # absolute (v4 path) when normalized_metrics aren't available.
        try:
            normalized_metrics = await _compute_normalized_metrics(
                ctx.db, ctx.session_id,
            )
            _logger.info(
                "[scoring][%s] normalized_metrics: unique_speakers=%s avg_viewer=%s "
                "engaged_rate=%s purchase_per_viewer=%s strong_purchase=%s",
                _sid, normalized_metrics["unique_speakers"],
                normalized_metrics["avg_viewer_count"],
                normalized_metrics["engaged_audience_rate"],
                normalized_metrics["purchase_intent_per_viewer"],
                normalized_metrics["strong_purchase_count"],
            )
        except Exception as e:
            _logger.warning("[scoring][%s] normalized_metrics failed (non-fatal): %s",
                            _sid, e)
            normalized_metrics = None
        analysis_summary["normalized_metrics"] = normalized_metrics

        # engagement (v5): blend of normalized engaged_audience_rate and
        # legacy prompts_per_minute.
        #
        # Why blend instead of pure normalized:
        #   engaged_audience_rate = unique_speakers / avg_viewer. On a
        #   small-viewer session (5 viewers, 4 of whom say something
        #   once) the rate hits 0.8 trivially — meaningless 80-point
        #   engagement score. The prompts-per-minute anchor stays
        #   honest about whether the anchor actually drove interaction
        #   regardless of crowd size.
        #
        # Why blend instead of pure absolute (v4):
        #   Two anchors emitting the same prompts/min but with very
        #   different audience response should score differently. The
        #   normalized term rewards anchors whose prompts actually land.
        #
        # Blend ratio 0.6 normalized + 0.4 absolute picked empirically
        # from the 2 valid sessions in the pool at the time of v5
        # rollout (29cf7773 = 0.19 rate, 6a1860b3 = 0.93 rate). Will
        # adjust once more sessions accumulate (see project-next-steps
        # memory, "scoring v5 Day 3" entry).
        #
        # Benchmarks for each term:
        #   absolute: 0.5/min → 25, 1.0/min → 50, 2.0/min → 100 (cap)
        #   normalized: rate 0.5 → 50, rate 1.0+ → 100 (cap)
        engagement_pm = analysis_summary.get("engagement", {}).get("prompts_per_minute", 0) or 0
        absolute_engagement = min(engagement_pm / 2.0 * 100, 100)

        engaged_rate = (
            normalized_metrics.get("engaged_audience_rate")
            if normalized_metrics else None
        )
        if engaged_rate is not None:
            # Normalized path (v5 main): 1.0 rate = 100 points, capped.
            normalized_engagement = min(engaged_rate / 1.0 * 100, 100)
            engagement_score = round(
                normalized_engagement * 0.6 + absolute_engagement * 0.4,
                1,
            )
        else:
            # Fallback (v4 behaviour): legacy sessions without viewer_snapshots
            # or sessions where the rate query failed. Pure absolute.
            engagement_score = round(absolute_engagement, 1)

        # conversion: closing tactics density.
        # Benchmark: 1 closing per 2 minutes = 30/hour is excellent.
        #   6/hour → 20, 18/hour → 60, 30/hour → 100 (cap)
        closing_total = analysis_summary.get("closing", {}).get("total_tactics", 0) or 0
        closing_per_hour = closing_total / duration_min * 60
        conversion_score = round(min(closing_per_hour / 30 * 100, 100), 1)

        # content: time spent on value-delivering script types.
        # Reads from rhythm.type_distribution; falls back to averaging
        # segment_scorer outputs if rhythm data is missing.
        # Weights reflect business value:
        #   teaching, product_intro = full value
        #   promotion              = 0.7 (transactional but informative)
        #   interaction            = 0 (counted under engagement)
        #   chitchat               = -0.2  (soft penalty for dead air)
        #
        # QA 2026-05-30 M0 / 🅴-22: original chitchat coefficient was -0.5
        # which produced content_score=0 on a perfectly normal small-streamer
        # session (the 97c5f11d 美妆边化妆边聊天 case: chitchat 68%
        # / teaching 29% → -4 → max 0). Soft penalty -0.2 lets a 70%-chitchat
        # session still score ~15 (teaching 30 - chitchat 14 = 16), which
        # better reflects that 闲聊 is a valid 陪伴 mode for small streamers.
        # Pure-teaching sessions still score higher (incentive preserved).
        rhythm = analysis_summary.get("rhythm", {})
        type_dist = rhythm.get("type_distribution", {}) if rhythm else {}
        if type_dist:
            teaching_pct = type_dist.get("teaching", {}).get("pct", 0) or 0
            product_pct = type_dist.get("product_intro", {}).get("pct", 0) or 0
            promotion_pct = type_dist.get("promotion", {}).get("pct", 0) or 0
            chitchat_pct = type_dist.get("chitchat", {}).get("pct", 0) or 0
            content_score = round(max(0, min(
                teaching_pct + product_pct + promotion_pct * 0.7 - chitchat_pct * 0.2,
                100,
            )), 1)
        else:
            # Legacy fallback: segment_scorer average (10-65 typical range)
            content_score = round(script_score, 1)

        # composite = weighted average of three calibrated sub-scores.
        # All three are script-side (independent of OCR/danmaku availability),
        # so composite no longer silently equals "话术分均值" when弹幕 is missing.
        # Weights:
        #   content   0.4  — value-delivering content is the foundation
        #   engagement 0.3 — interaction tactics amplify reach
        #   conversion 0.3 — closing tactics convert engagement to revenue
        composite_score = round(
            content_score * 0.4 + engagement_score * 0.3 + conversion_score * 0.3,
            1,
        )
        _logger.info("[scoring][%s] sub_scores content=%s engagement=%s conversion=%s -> composite=%s",
                     _sid, content_score, engagement_score, conversion_score, composite_score)
        ctx.session.composite_score = Decimal(str(composite_score))
        _logger.info("[scoring][%s] wrote session.composite_score=%s (pre-commit)",
                     _sid, ctx.session.composite_score)

        analysis_summary["scores"] = {
            "engagement": engagement_score,
            "conversion": conversion_score,
            "content": content_score,
            "composite": composite_score,
        }
        # Also expose top-level for any legacy reader (keep until frontend
        # migration is locked in — currently SessionList already falls back).
        analysis_summary["engagement_score"] = engagement_score
        analysis_summary["conversion_score"] = conversion_score
        analysis_summary["content_score"] = content_score
        analysis_summary["composite_score"] = composite_score
        _logger.info("[scoring][%s] analysis_summary scores written: %s | top-level composite_score=%s",
                     _sid, analysis_summary["scores"], analysis_summary.get("composite_score"))

        # data_completeness — explicitly flag which input layers were available.
        # The frontend uses this to show "本场无弹幕数据，互动/转化指标仅供参考"
        # warnings instead of letting users mistake gaps for genuine silence.
        # Status values:
        #   "ok"      — data layer ran successfully
        #   "missing" — layer skipped (e.g. OCR skipped, no danmakus)
        #   "partial" — produced some data but coverage incomplete
        danmaku_count = ctx.danmaku_result.get("total", 0) if ctx.danmaku_result else 0
        embedding_method_dist = semantic_summary.get("intent_method_distribution", {})
        embedding_used = embedding_method_dist.get("embedding", 0) > 0

        # Step status map for confidence calc
        step_results = {name: t.status for name, t in ctx.steps.items()}

        confidence_dict = _compute_analysis_confidence(
            analyzed=ctx.analyzed,
            danmaku_count=danmaku_count,
            embedding_used=embedding_used,
            duration_sec=ctx.session.duration_sec or 0,
            step_results=step_results,
        )

        valid_for_baseline = (
            len(ctx.analyzed) >= 30
            and (ctx.session.duration_sec or 0) >= 600  # 10 minutes
        )

        # Version-stamp the analysis so future formula/policy changes don't
        # silently corrupt historical comparisons.
        from app.services.versions import get_versions
        analysis_summary["versions"] = get_versions()

        analysis_summary["data_completeness"] = {
            "asr": "ok" if ctx.analyzed else "missing",
            "danmaku": "ok" if danmaku_count > 0 else "missing",
            "intent_embedding": "ok" if embedding_used else ("missing" if danmaku_count > 0 else "n/a"),
            "danmaku_count": danmaku_count,
            "segment_count": len(ctx.analyzed),
            # Single-number confidence + per-axis breakdown so the UI can
            # surface WHY a session is low-confidence ("OCR 未识别到弹幕"
            # vs "ASR 仅覆盖 70%").
            **confidence_dict,
            # valid_for_baseline gates whether this session participates in
            # cross-session comparison baselines. analysis_confidence is a
            # *separate* gate applied at query time.
            "valid_for_baseline": valid_for_baseline,
            # Plain-language summary the UI can show as a banner.
            "notes": _build_completeness_notes(ctx.analyzed, danmaku_count, embedding_used),
        }

        # Insight Engine
        await ctx.steps["insight_engine"].start({})
        _logger.info("T10: Insight Engine")
        from app.services.insight_engine.engine import run_insight_engine
        dm_records = ctx.danmaku_result.get("records", []) if ctx.danmaku_result else []
        # pattern extraction + ranking + narrative generation. Pure
        # Python but iterates over all segments + danmakus and can
        # take a few seconds on long sessions.
        insight_report = await asyncio.to_thread(
            run_insight_engine,
            ctx.analyzed, dm_records, analysis_summary,
            ctx.session.duration_sec or 0,
        )
        insight_report["generated_at"] = str(ctx.session.start_time) if ctx.session.start_time else ""
        analysis_summary["insight_report"] = insight_report
        await ctx.steps["insight_engine"].done({
            "insights_count": len(insight_report.get("insights", [])),
            "recommendations_count": len(insight_report.get("recommendations", [])),
        })

        # B5 2026-05-30: preserve early_* fields written by _step_early_analysis.
        # Scoring rebuilds analysis_summary from scratch, which would
        # otherwise clobber the script-only early Top 3. The early data
        # is still valid (and the front-end may still render it for
        # comparison/audit), so we copy it forward.
        existing = ctx.session.analysis_summary or {}
        for key in ("early_signals", "early_insights", "early_summary",
                    "early_threshold_set", "early_weights_version", "early_ready"):
            if key in existing:
                analysis_summary[key] = existing[key]

        ctx.session.analysis_summary = analysis_summary
        ctx.session.status = "analyzed"
        # Final state-coherence probe before commit. If composite_score on
        # session vs analysis_summary diverge here, the bug is upstream of
        # the JSONB persistence layer. If they agree here but diverge after
        # db.commit() in the outer finally, JSONB / ORM is to blame.
        _logger.info(
            "[scoring][%s] pre-commit state session.composite_score=%s "
            "summary.composite_score=%s summary.scores.composite=%s summary.scores keys=%s",
            _sid, ctx.session.composite_score,
            analysis_summary.get("composite_score"),
            analysis_summary.get("scores", {}).get("composite"),
            list(analysis_summary.get("scores", {}).keys()),
        )

        # ---- Invariants — turn silent corruption into loud failure ----
        # Background: on 2026-05-27 a stale uvicorn worker (running the
        # F-2-pre-redesign pipeline.py) successfully wrote composite_score
        # = script_score and a `scores` dict missing the `composite` key.
        # The session landed as status=analyzed with bogus numbers. No
        # exception, no log, just wrong data. These asserts make that class
        # of "looks fine, isn't fine" failure mode unsurvivable: if any
        # invariant fails we raise StepError, which flips status -> error
        # and surfaces the problem instead of burying it.
        _scores = analysis_summary.get("scores") or {}
        _problems = []
        if "composite" not in _scores:
            _problems.append(f"scores missing 'composite' key (keys={list(_scores.keys())})")
        if analysis_summary.get("composite_score") is None:
            _problems.append("analysis_summary.composite_score not written")
        if not (0 <= composite_score <= 100):
            _problems.append(f"composite_score {composite_score} out of [0,100]")
        try:
            _db_val = float(ctx.session.composite_score) if ctx.session.composite_score is not None else None
        except Exception:
            _db_val = None
        if _db_val is None or abs(_db_val - composite_score) > 0.05:
            _problems.append(f"session.composite_score ({_db_val}) diverges from computed ({composite_score})")
        if _problems:
            _msg = "scoring invariant violation: " + "; ".join(_problems)
            _logger.error("[scoring][%s] %s", _sid, _msg)
            raise StepError(_msg)

        await ctx.steps["scoring"].done({
            "composite_score": composite_score,
            "engagement_score": engagement_score,
            "conversion_score": conversion_score,
            "content_score": content_score,
        })
    except Exception as e:
        await ctx.steps["scoring"].fail(str(e))
        raise StepError(str(e)) from e
    return ctx


async def _step_playback_encode(ctx: PipelineContext) -> PipelineContext:
    """Produce a browser-friendly playback rendition WITHOUT touching master.

    Asset split (2026-06-03, migration 0011): the old version re-encoded to
    720p and uploaded BACK to the master key, destroying the original and
    breaking re-analysis. Now:

      1. Probe the master. If it's already browser-friendly (H.264 / AAC /
         mp4) — most desktop OBS recordings — point playback_video_path
         straight at master, set was_transcoded=false, do NO transcode.
         Saves the encode time AND a second storage copy.
      2. Otherwise (phone HEVC/120fps etc.) transcode to a SEPARATE MinIO
         key (master + ".playback.mp4"), set playback_video_path to it,
         was_transcoded=true. Master is left untouched for re-analysis.

    Non-fatal: on any failure, playback_video_path stays NULL → frontend
    falls back to the master for playback (may be slow, but analysis intact).
    """
    from app.services.video_processor import (
        transcode_for_playback, remux_with_faststart, is_browser_friendly,
    )

    sid = str(ctx.session.id)[:8]
    await ctx.steps["playback_encode"].start({"source_path": str(ctx.local_video)})
    try:
        playback_local = ctx.local_video.with_suffix(".playback.mp4")
        dur = ctx.session.duration_sec or 0

        if await asyncio.to_thread(is_browser_friendly, str(ctx.local_video)):
            # H.264/AAC/mp4 — correct codec, but OBS writes moov at the END of
            # the file (duration unknown during recording). Without faststart the
            # browser fetches the file tail on every seek → multi-second stall per
            # frame. Remux with -c copy -movflags +faststart: no re-encode, no
            # quality loss, ~30s for a 1h video.
            remux_timeout = max(300.0, dur * 0.3)
            _logger.info("[playback_encode][%s] remuxing for faststart (timeout %.0fs)",
                         sid, remux_timeout)
            await asyncio.to_thread(
                remux_with_faststart,
                str(ctx.local_video), str(playback_local), remux_timeout,
            )
            strategy = "remux_faststart"
            codec_tag = "h264_copy_faststart"
        else:
            # Phone HEVC / high-fps / non-mp4 → full re-encode to H.264 720p.
            encode_timeout = max(900.0, dur * 1.5)
            _logger.info("[playback_encode][%s] transcoding (timeout %.0fs)", sid, encode_timeout)
            await asyncio.to_thread(
                transcode_for_playback,
                str(ctx.local_video), str(playback_local), encode_timeout,
            )
            strategy = "transcoded"
            codec_tag = "h264_main_720p_30fps_faststart"

        size_mb = playback_local.stat().st_size / 1024 / 1024

        # Upload to a SEPARATE key — never overwrite the master object.
        playback_object = ctx.minio_object + ".playback.mp4"
        from minio import Minio
        client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )
        await asyncio.to_thread(
            client.fput_object,
            ctx.minio_bucket, playback_object, str(playback_local), "video/mp4",
        )
        ctx.session.playback_video_path = f"{ctx.minio_bucket}/{playback_object}"
        ctx.session.was_transcoded = (strategy == "transcoded")
        await ctx.steps["playback_encode"].done({
            "strategy": strategy,
            "playback_size_mb": round(size_mb, 1),
            "codec": codec_tag,
        })
        _logger.info("[playback_encode][%s] %s → %s (%.0f MB), master kept",
                     sid, strategy, playback_object, size_mb)
        # Clean the local playback file (master + frames cleaned by orchestrator)
        if playback_local.exists():
            playback_local.unlink(missing_ok=True)
    except Exception as e:
        _logger.exception("[playback_encode][%s] non-fatal: %s", sid, e)
        await ctx.steps["playback_encode"].skip(str(e))
    return ctx


# Step order defines the FSM sequence (non-fatal steps raise StepSkipped).
# _step_viewer_count sits right after _step_ocr because both read the same
# pre-cropped frames from video_processing — just from different regions.
# Keeping them adjacent in the FSM also keeps the OCR-related logs
# clustered in pipeline.log for easier debugging.
PIPELINE_STEPS = [
    _step_video,
    _step_asr,
    _step_script,
    _step_early_analysis,  # B5 2026-05-30: script-only Top 3 (~5 min mark)
    _step_tactics,       # non-fatal
    _step_ocr,           # non-fatal — interaction-list OCR
    _step_viewer_count,  # non-fatal — viewer-count OCR (2026-05-28 addition)
    _step_danmaku,       # non-fatal — sentiment/intent + DB persistence
    _step_correlation,   # non-fatal
    _step_scoring,
    _step_playback_encode,  # non-fatal — H.264 720p re-encode (2026-05-30)
]


# ============================================================
#  Helpers
# ============================================================

def _download_video(bucket: str, object_name: str, local_path: Path) -> None:
    from minio import Minio
    client = Minio(
        settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
    )
    client.fget_object(bucket, object_name, str(local_path))


def _compute_analysis_confidence(
    analyzed: list,
    danmaku_count: int,
    embedding_used: bool,
    duration_sec: int,
    step_results: dict,
) -> dict:
    """Compute per-session analysis confidence (0-1) plus breakdown.

    Weights chosen to reflect this system's signal hierarchy:
      * ASR is the primary input — most downstream analysis depends on
        having reliable transcript coverage.
      * OCR is secondary — useful when present, but absence ≠ broken
        analysis (script-side metrics still work without danmakus).
      * Embedding quality matters but keyword fallback still produces
        usable (lower-precision) intent labels.
      * Pipeline step success is a fail-safe — catches edge cases where
        e.g. the scoring step crashed but earlier steps succeeded.

    Tier interpretation (see UI):
      * >= 0.8   高可信
      * 0.6-0.8  可参考
      * < 0.6    低可信 — UI shows warning, baseline pool excludes
    """
    # asr_completion: fraction of video duration covered by ASR segments.
    if analyzed and duration_sec > 0:
        last_end = max((s.get("end_sec", 0) or 0) for s in analyzed)
        asr_completion = min(last_end / duration_sec, 1.0)
    else:
        asr_completion = 0.0

    # ocr_coverage: 1.0 when any danmaku was captured. We can't distinguish
    # "audience was silent" from "OCR failed" perfectly, so this is binary
    # — captured danmakus = 1.0, none = 0.0.
    ocr_coverage = 1.0 if danmaku_count > 0 else 0.0

    # embedding_quality: 1.0 if semantic intent ran, 0.3 if fell back to
    # keyword. (0 would punish keyword-only sessions too harshly — the
    # fallback IS still producing labels, just less precise.)
    embedding_quality = 1.0 if embedding_used else 0.3

    # step_success_ratio: done+skipped count over total. Skipped is OK
    # (intentional, non-fatal), failed is not.
    if step_results:
        ok = sum(1 for s in step_results.values() if s in ("done", "skipped"))
        step_success_ratio = ok / len(step_results)
    else:
        step_success_ratio = 0.0

    confidence = round(
        0.5 * asr_completion
        + 0.2 * ocr_coverage
        + 0.2 * embedding_quality
        + 0.1 * step_success_ratio,
        3,
    )

    return {
        "analysis_confidence": confidence,
        "confidence_breakdown": {
            "asr_completion": round(asr_completion, 3),
            "ocr_coverage": round(ocr_coverage, 3),
            "embedding_quality": round(embedding_quality, 3),
            "step_success_ratio": round(step_success_ratio, 3),
        },
    }


def _build_completeness_notes(analyzed: list, danmaku_count: int, embedding_used: bool) -> list[str]:
    """Produce human-readable warnings about missing/unreliable data layers.

    Frontend should surface these prominently — without them, users could
    confuse missing data with genuine analytical findings (e.g. "0 互动"
    means "no danmakus captured", not "audience was silent").
    """
    notes: list[str] = []
    if not analyzed:
        notes.append("⚠ ASR 未产出任何话术段，整场分析不可信")
    if danmaku_count == 0:
        notes.append("⚠ 本场未采集到弹幕数据，所有「观众反应/互动/转化」相关指标均不可信")
    elif danmaku_count < 30:
        notes.append(f"⚠ 弹幕样本极少（{danmaku_count} 条），互动/转化指标仅供参考")
    if danmaku_count > 0 and not embedding_used:
        notes.append("⚠ 语义意图模型未启用，弹幕意图识别已退化为关键词匹配，精度有限")
    return notes


def _build_semantic_summary(danmaku_result: dict, analyzed: list[dict]) -> dict:
    """Build semantic intent summary from embedding-enhanced danmaku and tactics."""
    from collections import Counter
    from app.services.intent.intent_prototypes import INTENT_FUNNEL

    records = danmaku_result.get("records", []) if danmaku_result else []
    intent_dist = Counter()
    method_dist = Counter()
    for r in records:
        it = r.get("intent_type", "interaction") or "interaction"
        intent_dist[it] += 1
        method_dist[r.get("intent_method", "unknown")] += 1

    total = len(records) or 1
    funnel = {t: {"count": intent_dist.get(t, 0), "pct": round(intent_dist.get(t, 0) / total * 100, 1)} for t in INTENT_FUNNEL}

    # Tactic stats
    tactic_dist = Counter()
    for seg in analyzed:
        tags = seg.get("analysis_tags", {})
        for t in tags.get("embedding_tactics", []):
            tactic_dist[t["type"] if isinstance(t, dict) else t] += 1

    return {
        "intent_funnel": funnel,
        "intent_method_distribution": dict(method_dist),
        "tactic_distribution": dict(tactic_dist),
        "embedding_available": method_dist.get("embedding", 0) > 0,
    }


def _build_analysis_summary(analyzed: list[dict], total_duration: int) -> dict:
    """Build session-level analysis summary from analyzed segments."""
    summary = {}

    # Opening
    opening_segs = [s for s in analyzed if s.get("analysis_tags", {}).get("is_opening")]
    summary["opening"] = {
        "detected": len(opening_segs) > 0,
        "segments_count": len(opening_segs),
        "duration_sec": int(opening_segs[-1]["end_sec"] - opening_segs[0]["start_sec"]) if opening_segs else 0,
    }

    # Engagement
    eng_segs = [s for s in analyzed if s.get("analysis_tags", {}).get("has_engagement_prompt")]
    eng_counts = Counter()
    for s in eng_segs:
        for pt in s.get("analysis_tags", {}).get("engagement_prompt_types", []):
            eng_counts[pt] += 1
    summary["engagement"] = {
        "total_prompts": sum(eng_counts.values()),
        "prompts_per_minute": round(sum(eng_counts.values()) / max(total_duration, 1) * 60, 2),
        "by_type": dict(eng_counts),
        "segments_with_prompt": len(eng_segs),
    }

    # Closing
    close_segs = [s for s in analyzed if s.get("analysis_tags", {}).get("has_closing_tactic")]
    close_counts = Counter()
    for s in close_segs:
        for t in s.get("analysis_tags", {}).get("closing_tactic_types", []):
            close_counts[t] += 1
    summary["closing"] = {
        "detected": len(close_segs) > 0,
        "total_tactics": sum(close_counts.values()),
        "by_type": dict(close_counts),
        "segments_with_tactic": len(close_segs),
    }

    # Pain points
    pp_counter = Counter()
    pp_cats = defaultdict(set)
    for s in analyzed:
        tags = s.get("analysis_tags", {})
        for pp in tags.get("pain_points", []):
            pp_counter[pp] += 1
        for cat in tags.get("pain_point_categories", []):
            pp_cats[cat].update(tags.get("pain_points", []))
    summary["pain_points"] = {
        "top_pain_points": [{"keyword": kw, "count": cnt} for kw, cnt in pp_counter.most_common(10)],
        "total_mentions": sum(pp_counter.values()),
        "categories": {
            cat: {"count": sum(pp_counter[kw] for kw in kws), "items": list(kws)}
            for cat, kws in pp_cats.items()
        },
    }

    # Rhythm
    summary["rhythm"] = analyze_rhythm(analyzed, total_duration)

    return summary


async def _compute_normalized_metrics(db, session_id: str) -> dict:
    """Compute v5 normalized engagement / conversion metrics from DB state.

    Single SQL with three independent subqueries — one round-trip, no
    cross-table join (subqueries are correlated only by session_id, which
    is indexed on both tables).

    Schema contract for downstream readers (committed in v5):
      unique_speakers              int     COUNT(DISTINCT normalized_username)
                                           WHERE source_type='speech'
                                           AND normalized_username IS NOT NULL
      avg_viewer_count             float   AVG(viewer_snapshots.viewer_count); 0 if no snapshots
      engaged_audience_rate        float|None  unique_speakers / avg_viewer; None if avg=0
      purchase_intent_per_viewer   float|None  strong_purchase / avg_viewer; None if avg=0
      strong_purchase_count        int     COUNT speech rows with intent='strong_purchase'

    `None` values are honest absences (no viewer data → no rate). Callers
    must handle them, never coerce to 0 (which would falsely place the
    session at "perfectly disengaged").
    """
    from sqlalchemy import text

    row = (await db.execute(text("""
        SELECT
            (SELECT COUNT(DISTINCT normalized_username)
                FROM danmakus
                WHERE session_id = :sid
                  AND source_type = 'speech'
                  AND normalized_username IS NOT NULL) AS unique_speakers,
            (SELECT AVG(viewer_count)
                FROM viewer_snapshots
                WHERE session_id = :sid) AS avg_viewer,
            (SELECT COUNT(*)
                FROM danmakus
                WHERE session_id = :sid
                  AND source_type = 'speech'
                  AND intent = 'strong_purchase') AS strong_purchase_count
    """), {"sid": session_id})).first()

    unique = int(row.unique_speakers or 0)
    purch = int(row.strong_purchase_count or 0)
    # row.avg_viewer comes back as Decimal; None when viewer_snapshots is empty.
    avg = float(row.avg_viewer) if row.avg_viewer is not None else 0.0

    engaged_rate = round(unique / avg, 4) if avg > 0 else None
    purchase_rate = round(purch / avg, 4) if avg > 0 else None

    return {
        "unique_speakers": unique,
        "avg_viewer_count": round(avg, 2),
        "engaged_audience_rate": engaged_rate,
        "purchase_intent_per_viewer": purchase_rate,
        "strong_purchase_count": purch,
    }


async def _send_feishu_notification_async(session, analyzed, danmaku_result, correlation) -> None:
    if not settings.feishu_webhook_url:
        return
    import httpx

    speech_score = round(
        sum(float(s.get("score", 0)) for s in analyzed) / max(len(analyzed), 1), 1
    ) if analyzed else 0.0

    content = {
        "msg_type": "interactive",
        "card": {
            "header": {"title": {"tag": "plain_text", "content": "直播分析完成"}, "template": "purple"},
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": f"**{session.title}**"}},
                {"tag": "hr"},
                {"tag": "div", "text": {"tag": "lark_md", "content": f"- 时长：{session.duration_sec // 60} 分钟"}},
                {"tag": "div", "text": {"tag": "lark_md", "content": f"- 综合评分：**{session.composite_score} 分**"}},
                {"tag": "div", "text": {"tag": "lark_md", "content": f"- 话术得分：{speech_score} 分"}},
                {"tag": "div", "text": {"tag": "lark_md", "content": f"- 弹幕总量：{danmaku_result.get('total', 0) if danmaku_result else 0} 条"}},
            ],
        },
    }

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10)) as client:
            resp = await client.post(settings.feishu_webhook_url, json=content)
            resp.raise_for_status()
    except Exception:
        _logger.warning("Feishu notification failed")
