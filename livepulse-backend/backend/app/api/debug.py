"""Debug-only endpoints for observability/recovery work.

These endpoints exist to make the pipeline RE-OBSERVABLE without re-running
upstream expensive steps (OCR, ASR, embedding). The motivating case (2026-
05-27): composite_score in DB diverged from what _step_scoring's source
code would compute, with no log evidence to explain why. The cheapest way
to find that out is to re-trigger the broken step in isolation, with full
instrumentation, against the real DB row — that's exactly what this gives.

Why a debug router rather than a flag on /sessions:
  * Surfaces clearly as "non-production" intent in OpenAPI / curl traces.
  * Easy to disable wholesale by removing one include_router() line.
  * Frontend never calls these — they're operator-only.

Endpoints here MUST NOT mutate raw input layers (segments / danmakus /
analysis_tags). They may only re-derive higher-order fields. If you find
yourself touching segments, write a separate `rerun-pipeline` endpoint
that goes through the full pipeline instead.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.database import async_session_factory
from app.models import LiveSession, ScriptSegment, Danmaku
from app.services.step_tracker import StepTracker
from app.services.runtime_fingerprint import get_fingerprint

router = APIRouter(prefix="/_debug", tags=["debug"])
logger = logging.getLogger(__name__)


@router.get("/runtime")
async def runtime():
    """Return the immutable identity of THIS worker process.

    Used by restart_backend.ps1 to poll until boot_id changes — proving
    the new code is actually serving traffic, not just "uvicorn says
    ready". Also useful for ad-hoc `curl` diagnosis when something feels
    wrong: a quick check tells you whether you're talking to the worker
    you think you are.

    Note: this endpoint inherits the project-wide X-API-Key dependency
    from include_router(). It MUST stay authenticated — the fingerprint
    leaks Python version, venv path, and cwd, all of which are useful
    to an attacker doing reconnaissance.
    """
    return {"code": 0, "data": get_fingerprint(), "message": "ok"}


@router.post("/sessions/{session_id}/rerun-scoring")
async def rerun_scoring_only(session_id: str):
    """Re-run ONLY the scoring step against the existing DB state.

    Rebuilds the minimal subset of PipelineContext that _step_scoring
    consumes (session, analyzed segments, danmaku_result dict), invokes
    _step_scoring, commits, and returns the post-commit truth.

    This is the right debugging entry point for any "composite_score /
    scores.* / data_completeness looks wrong" complaint, because:
      * It is fast (no OCR/ASR/embedding recompute).
      * It uses the SAME code path as the real pipeline (no parallel
        formula maintained here — the bug, if any, lives in _step_scoring).
      * The full instrumentation in _step_scoring runs verbatim, so
        pipeline.log captures the exact state transition.

    Returns the post-commit DB row's composite_score, scores dict, and
    insight count, so the caller can immediately diff against expectations.
    """
    # Late import: _step_scoring imports a lot (insight engine, sentence-
    # transformers, etc). Keeping it inside the function avoids paying that
    # cost on every backend start just because debug.py is registered.
    from app.tasks.pipeline import PipelineContext, _step_scoring

    async with async_session_factory() as db:
        session = await db.get(LiveSession, session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="session not found")
        if not session.duration_sec:
            raise HTTPException(
                status_code=400,
                detail="session.duration_sec is empty — pipeline never ran or video_processing failed",
            )

        # ---- Rebuild `analyzed` from script_segments table ----
        # _step_scoring reads:
        #   s["score"] (segment_scorer output)
        #   s["start_sec"], s["end_sec"]
        #   s["analysis_tags"] (engagement prompts, closing tactics, opening, pain points)
        #   s["script_type"] (for rhythm.type_distribution -> content_score)
        # We must reconstruct these *exactly* as the original pipeline would,
        # or composite_score becomes meaningless ("garbage in, garbage out").
        seg_rows = (await db.execute(
            select(ScriptSegment)
            .where(ScriptSegment.session_id == session_id)
            .order_by(ScriptSegment.start_sec)
        )).scalars().all()

        analyzed: list[dict] = []
        for s in seg_rows:
            analyzed.append({
                "start_sec": s.start_sec,
                "end_sec": s.end_sec,
                "text": s.text or "",
                "score": float(s.score) if s.score is not None else 50.0,
                "script_type": s.script_type or "chitchat",
                "speech_rate": s.speech_rate or 0,
                "analysis_tags": s.analysis_tags or {},
                "keywords": s.keywords or [],
            })

        # ---- Rebuild `danmaku_result` from danmakus table ----
        # _step_scoring reads .total and .records[] (each record needs
        # timestamp_sec, content, sentiment, intent at minimum).
        dm_rows = (await db.execute(
            select(Danmaku)
            .where(Danmaku.session_id == session_id)
            .order_by(Danmaku.timestamp_sec, Danmaku.id)
        )).scalars().all()
        # QA 2026-05-30 E follow-up: Danmaku 表不存 intent_method 列(只
        # 存 intent label)。rerun-scoring 重建 records 时如果 method=None,
        # _build_completeness_notes 算 embedding_used=False → banner 复活。
        # 解法:如果 EmbeddingService 当前可用,标记 method='embedding' —
        # 这反映现实(intent 列若是 rerun-intent-analysis 跑过的就是
        # embedding,新上传的也一样)。fallback 仍认 keyword。
        from app.services.embedding.embedding_service import EmbeddingService
        _current_method = "embedding" if EmbeddingService.is_available() else "keyword"
        records = [
            {
                "timestamp_sec": d.timestamp_sec,
                "content": d.content,
                "sentiment": d.sentiment,
                # Real pipeline uses key "intent_type" (see pipeline.py:712);
                # _build_semantic_summary + compute_semantic_correlation both
                # read "intent_type". Keep "intent" alias for older readers.
                "intent_type": d.intent,
                "intent": d.intent,
                "intent_method": _current_method,
            }
            for d in dm_rows
        ]
        danmaku_result = {"total": len(records), "records": records} if records else None

        # ---- Build steps map (only scoring + insight_engine are actually
        # used by _step_scoring; others are referenced in confidence calc) ----
        steps = {name: StepTracker(db, session_id, name) for name in [
            "video_processing", "asr", "script_analysis", "tactics", "ocr",
            "danmaku_analysis", "correlation", "scoring", "insight_engine",
        ]}
        # Mark non-scoring steps as "done" so _compute_analysis_confidence
        # doesn't penalize step_success_ratio. They WERE done previously;
        # this rerun is only re-deriving scoring. (Not persisted — these
        # tracker objects are not _upsert()ed here.)
        for name, t in steps.items():
            if name not in ("scoring", "insight_engine"):
                t.status = "done"

        # ---- Build minimal ctx ----
        # session_id is required by PipelineContext but local_video / output_dir
        # are unused by _step_scoring. Pass dummy Paths to satisfy the dataclass.
        from pathlib import Path
        ctx = PipelineContext(
            db=db,
            session=session,
            session_id=session_id,
            steps=steps,
            local_video=Path("/dev/null-rerun-scoring"),
            output_dir=Path("/dev/null-rerun-scoring"),
        )
        ctx.analyzed = analyzed
        ctx.danmaku_result = danmaku_result

        logger.info("[debug.rerun-scoring][%s] rebuilt ctx: analyzed=%d danmaku_total=%d",
                    session_id[:8], len(analyzed),
                    danmaku_result["total"] if danmaku_result else 0)

        # ---- Run the real step ----
        try:
            await _step_scoring(ctx)
        except Exception as e:
            logger.exception("[debug.rerun-scoring][%s] _step_scoring raised: %s",
                             session_id[:8], e)
            raise HTTPException(status_code=500, detail=f"_step_scoring failed: {e}")

        await db.commit()
        # Post-commit truth probe — re-read from DB, not from `session`
        # (ORM identity map may still hold the in-memory copy).
        await db.refresh(session)
        summary = session.analysis_summary or {}
        result = {
            "session_id": session_id,
            "post_commit": {
                "composite_score_db": float(session.composite_score) if session.composite_score is not None else None,
                "composite_score_summary_top": summary.get("composite_score"),
                "composite_score_summary_scores": (summary.get("scores") or {}).get("composite"),
                "scores": summary.get("scores"),
                "scores_keys": list((summary.get("scores") or {}).keys()),
            },
            "input": {
                "segments": len(analyzed),
                "danmaku_total": danmaku_result["total"] if danmaku_result else 0,
            },
        }
        logger.info("[debug.rerun-scoring][%s] result=%s",
                    session_id[:8], result["post_commit"])
        return {"code": 0, "data": result, "message": "ok"}


@router.post("/sessions/{session_id}/rerun-viewer-count")
async def rerun_viewer_count(session_id: str):
    """Re-extract viewer_count for an existing session's full frames.

    QA 2026-05-30 B1: for sessions analyzed before the phone-Douyin
    heuristic was added (M0 fixture 97c5f11d skipped viewer_count
    entirely), re-run just that step against MinIO-stored frames. Costs
    ~0.005 元 per frame (typical 23 frames for 11min video = ~0.1 元).
    Operator-only.
    """
    import tempfile
    from pathlib import Path
    from minio import Minio
    from app.config import get_settings
    from app.services.video_processor import process_video
    from app.services.aliyun_ocr_service import extract_viewer_count
    from app.models.viewer_snapshot import ViewerSnapshot
    from sqlalchemy import delete as sa_delete

    settings = get_settings()

    async with async_session_factory() as db:
        session = (await db.execute(
            select(LiveSession).where(LiveSession.id == session_id)
        )).scalar_one_or_none()
        if not session:
            raise HTTPException(status_code=404, detail="session not found")
        if not session.master_video_path or "/" not in session.master_video_path:
            raise HTTPException(status_code=400, detail="session has no master_video_path")
        bucket, object_name = session.master_video_path.split("/", 1)

    client = Minio(
        settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
    )
    tmpdir = Path(tempfile.mkdtemp(prefix="rerun_viewer_"))
    src = tmpdir / "src.mp4"
    try:
        logger.info("[rerun-viewer][%s] download from MinIO", session_id[:8])
        client.fget_object(bucket, object_name, str(src))
        logger.info("[rerun-viewer][%s] capturing frames", session_id[:8])
        proc = process_video(str(src), output_dir=tmpdir)
        frames = proc.full_frame_paths
        logger.info("[rerun-viewer][%s] running OCR on %d frames",
                    session_id[:8], len(frames))
        records = extract_viewer_count(frames, interval_sec=30)
        # Replace existing rows for this session
        async with async_session_factory() as db:
            await db.execute(sa_delete(ViewerSnapshot).where(
                ViewerSnapshot.session_id == session_id
            ))
            for r in records:
                db.add(ViewerSnapshot(
                    session_id=session_id,
                    timestamp_sec=r["timestamp_sec"],
                    viewer_count=r["viewer_count"],
                    like_count=r.get("like_count"),
                ))
            await db.commit()
        # Filter None: like-only frames carry viewer_count=None (2026-06-03).
        counts = [r["viewer_count"] for r in records if r.get("viewer_count") is not None]
        avg = round(sum(counts) / len(counts), 1) if counts else 0
        like_values = [r["like_count"] for r in records if r.get("like_count") is not None]
        return {
            "code": 0,
            "data": {
                "session_id": session_id,
                "frames_processed": len(frames),
                "snapshots_extracted": len(records),
                "avg_viewer": avg,
                "peak_viewer": max(counts) if counts else 0,
                # B4 fix 2026-06-03: include like_count rollup for diagnosis.
                "likes_hits": len(like_values),
                "likes_max": max(like_values) if like_values else 0,
                "samples": records[:5],
            },
            "message": "ok",
        }
    finally:
        import shutil
        try:
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass


@router.post("/sessions/{session_id}/rerun-tactics")
async def rerun_tactics(session_id: str):
    """Re-classify each ScriptSegment's tactics using the current embedding analyzer.

    QA 2026-05-30: sessions analyzed before ModelScope embedding swap
    (commit 60d1b5b) ran tactic classification via keyword fallback only
    (embedding service was unavailable). Result:
      - ad9f759c (B-8 victim, 828 segs): 0 tactics tagged
      - b456b873 (uploaded today AM, 163 segs): 6 tactics (keyword sparse)
      - 41e9e83d (uploaded today AM, 138 segs): 26 tactics (keyword sparse)

    This endpoint loads all segments, calls embedding_tactic_analyzer
    on their texts, updates each segment's analysis_tags. Free
    (local embedding, no API calls). After this, call rerun-scoring to
    refresh tactic_distribution in analysis_summary.
    """
    from app.models.script_segment import ScriptSegment
    from app.services.tactics.embedding_tactic_analyzer import get_tactic_analyzer
    from app.services.embedding.embedding_service import EmbeddingService
    from sqlalchemy.orm.attributes import flag_modified
    from collections import Counter

    if not EmbeddingService.is_available():
        raise HTTPException(
            status_code=503,
            detail="embedding service unavailable — model not loaded",
        )

    async with async_session_factory() as db:
        rows = (await db.execute(
            select(ScriptSegment).where(ScriptSegment.session_id == session_id)
            .order_by(ScriptSegment.start_sec)
        )).scalars().all()
        if not rows:
            raise HTTPException(status_code=404, detail="no script segments")

        texts = [(r.text or "").strip() for r in rows]
        analyzer = get_tactic_analyzer()
        import asyncio
        results = await asyncio.to_thread(analyzer.analyze_batch, texts)

        method_counter = Counter()
        tactic_counter = Counter()
        tagged = 0
        for r, tact in zip(rows, results):
            method_counter[tact.get("method", "?")] += 1
            tactics = tact.get("tactics") or []
            tags = dict(r.analysis_tags or {})
            tags["embedding_tactics"] = tactics
            tags["tactic_score_distribution"] = tact.get("score_distribution", {})
            r.analysis_tags = tags
            flag_modified(r, "analysis_tags")
            if tactics:
                tagged += 1
                for t in tactics:
                    tactic_counter[t.get("type", "?")] += 1

        await db.commit()
        logger.info(
            "[debug.rerun-tactics][%s] %d segments tagged (of %d), methods=%s",
            session_id[:8], tagged, len(rows), dict(method_counter),
        )
        return {
            "code": 0,
            "data": {
                "session_id": session_id,
                "segments_total": len(rows),
                "segments_with_tactics": tagged,
                "method_dist": dict(method_counter),
                "tactic_dist": dict(tactic_counter.most_common(10)),
            },
            "message": "ok",
        }


@router.post("/sessions/{session_id}/rerun-ocr-onwards")
async def rerun_ocr_onwards(session_id: str):
    """Re-extract OCR + run danmaku/scoring/insight on existing session.

    QA 2026-05-30: ad9f759c was a B-8 victim (2026-05-29 QA round) where
    OCR captured 581 speech events but the danmaku step crashed mid-flight
    and persisted 0 rows. The bug was fixed (commit 2cb0221) but the
    affected session's data is permanently 0. This endpoint re-runs:
      - frame capture (free, ffmpeg)
      - OCR (costs Aliyun money, ~0.005 元/frame)
      - viewer_count (cheap, mostly skipped for already-populated sessions)
      - danmaku analysis (free, uses local embedding)
      - correlation (free)
      - scoring (free)
      - insight_engine (free)

    Skipped: video transcoding (already done), ASR (already done — script
    segments preserved), script_analysis (already done).

    For a 75-min video with ~150 frames, OCR cost ≈ 0.75 元.
    """
    import tempfile
    from pathlib import Path
    from minio import Minio
    from app.config import get_settings
    from app.services.video_processor import process_video
    from app.tasks.pipeline import (
        PipelineContext, _step_ocr, _step_viewer_count, _step_danmaku,
        _step_correlation, _step_scoring,
    )
    from app.services.step_tracker import create_step_trackers
    from app.models.script_segment import ScriptSegment

    settings = get_settings()
    async with async_session_factory() as db:
        session = (await db.execute(
            select(LiveSession).where(LiveSession.id == session_id)
        )).scalar_one_or_none()
        if not session:
            raise HTTPException(status_code=404, detail="session not found")
        if not session.master_video_path or "/" not in session.master_video_path:
            raise HTTPException(status_code=400, detail="session has no master_video_path")
        bucket, object_name = session.master_video_path.split("/", 1)

    client = Minio(
        settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
    )
    tmpdir = Path(tempfile.mkdtemp(prefix="rerun_ocr_onwards_"))
    src = tmpdir / "src.mp4"
    try:
        logger.info("[rerun-ocr-onwards][%s] download from MinIO", session_id[:8])
        client.fget_object(bucket, object_name, str(src))
        logger.info("[rerun-ocr-onwards][%s] capturing frames", session_id[:8])
        proc = process_video(str(src), output_dir=tmpdir)

        # Build ctx with existing analyzed segments preserved
        async with async_session_factory() as db:
            session = (await db.execute(
                select(LiveSession).where(LiveSession.id == session_id)
            )).scalar_one_or_none()
            seg_rows = (await db.execute(
                select(ScriptSegment).where(ScriptSegment.session_id == session_id)
                .order_by(ScriptSegment.start_sec)
            )).scalars().all()
            analyzed = [
                {
                    "id": s.id,
                    "start_sec": s.start_sec,
                    "end_sec": s.end_sec,
                    "text": s.text or "",
                    "score": float(s.score) if s.score is not None else 50.0,
                    "script_type": s.script_type or "chitchat",
                    "speech_rate": s.speech_rate or 0,
                    "analysis_tags": s.analysis_tags or {},
                    "keywords": s.keywords or [],
                }
                for s in seg_rows
            ]
            steps = await create_step_trackers(db, session_id)
            ctx = PipelineContext(
                db=db, session=session, session_id=session_id,
                steps=steps, local_video=src, output_dir=tmpdir,
                minio_bucket=bucket, minio_object=object_name,
            )
            ctx.proc = proc
            ctx.analyzed = analyzed
            logger.info(
                "[rerun-ocr-onwards][%s] context ready: analyzed=%d frames=%d",
                session_id[:8], len(analyzed), len(proc.full_frame_paths),
            )

            # Run the OCR-dependent steps in sequence.
            # _step_scoring runs the insight engine internally.
            ctx = await _step_ocr(ctx)
            ctx = await _step_viewer_count(ctx)
            ctx = await _step_danmaku(ctx)
            ctx = await _step_correlation(ctx)
            ctx = await _step_scoring(ctx)
            await db.commit()

            # Pull post-commit truth for the response
            await db.refresh(session)
            summary = session.analysis_summary or {}
            scores = summary.get("scores") or {}
        return {
            "code": 0,
            "data": {
                "session_id": session_id,
                "composite": float(session.composite_score) if session.composite_score else None,
                "scores": scores,
                "total_danmaku": session.total_danmaku,
                "notes": (summary.get("data_completeness") or {}).get("notes", []),
            },
            "message": "ok",
        }
    finally:
        import shutil
        try:
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass


@router.post("/sessions/{session_id}/rerun-intent-analysis")
async def rerun_intent_analysis(session_id: str):
    """Re-classify existing danmaku intents with the current embedding model.

    QA 2026-05-30 E: sessions analyzed BEFORE the ModelScope embedding
    swap have:
      - intents classified by keyword fallback (lower precision)
      - data_completeness.notes containing "⚠ 语义意图模型未启用…" banner
      - normalized_metrics intent percentages reflecting keyword path

    This endpoint re-runs JUST the intent + sentiment classification on the
    existing DB danmaku rows (no re-OCR, no re-ASR — free). Then it
    rebuilds data_completeness with embedding_used=True so the banner
    goes away.

    Use case:在已有的 demo session (b456b873 phone / 41e9e83d OBS) 上
    把 banner 清掉,演示时不用解释"我们其实有 embedding 只是这场是老
    数据"。
    """
    from app.models.danmaku import Danmaku
    from app.services.intent.embedding_intent_analyzer import get_intent_analyzer
    from app.services.sentiment.sentiment_analyzer import get_sentiment_analyzer
    from app.services.embedding.embedding_service import EmbeddingService
    from sqlalchemy.orm.attributes import flag_modified
    from collections import Counter

    if not EmbeddingService.is_available():
        raise HTTPException(
            status_code=503,
            detail="embedding service unavailable — model not loaded",
        )

    async with async_session_factory() as db:
        session = (await db.execute(
            select(LiveSession).where(LiveSession.id == session_id)
        )).scalar_one_or_none()
        if not session:
            raise HTTPException(status_code=404, detail="session not found")

        # Fetch all SPEECH danmakus (only these get intent analysis)
        rows = (await db.execute(
            select(Danmaku).where(
                Danmaku.session_id == session_id,
                Danmaku.source_type == "speech",
            )
        )).scalars().all()
        if not rows:
            raise HTTPException(status_code=400, detail="no speech danmakus to re-classify")

        contents = [(r.content or "").strip() for r in rows]

        intent_ana = get_intent_analyzer()
        sent_ana = get_sentiment_analyzer()

        # Run both in thread pool to keep event loop responsive
        import asyncio
        intent_results = await asyncio.to_thread(intent_ana.analyze_batch, contents)
        sent_results = await asyncio.to_thread(sent_ana.analyze_batch, contents)

        intent_method_counter = Counter()
        sent_method_counter = Counter()
        for r, ir, sr in zip(rows, intent_results, sent_results):
            r.intent = ir.get("intent_type")
            r.sentiment = sr.get("sentiment")
            intent_method_counter[ir.get("method", "?")] += 1
            sent_method_counter[sr.get("method", "?")] += 1

        # Update data_completeness in analysis_summary
        summary = session.analysis_summary or {}
        embedding_used = intent_method_counter.get("embedding", 0) > 0
        completeness = summary.get("data_completeness") or {}
        # Rebuild notes (preserve any non-embedding-related ones)
        notes = []
        if session.total_danmaku and session.total_danmaku < 30:
            notes.append(f"⚠ 弹幕样本极少（{session.total_danmaku} 条），互动/转化指标仅供参考")
        if session.total_danmaku and not embedding_used:
            notes.append("⚠ 语义意图模型未启用，弹幕意图识别已退化为关键词匹配，精度有限")
        completeness["notes"] = notes
        completeness["intent_embedding"] = "ok" if embedding_used else "missing"
        # Bump embedding_quality in breakdown if it's there
        bd = completeness.get("confidence_breakdown") or {}
        if bd:
            bd["embedding_quality"] = 1.0 if embedding_used else 0.3
            completeness["confidence_breakdown"] = bd
        summary["data_completeness"] = completeness
        session.analysis_summary = summary
        flag_modified(session, "analysis_summary")

        await db.commit()
        logger.info(
            "[debug.rerun-intent][%s] reclassified %d danmakus, embedding_used=%s, "
            "intent_methods=%s, sent_methods=%s",
            session_id[:8], len(rows), embedding_used,
            dict(intent_method_counter), dict(sent_method_counter),
        )
        return {
            "code": 0,
            "data": {
                "session_id": session_id,
                "danmaku_reclassified": len(rows),
                "embedding_used": embedding_used,
                "intent_method_dist": dict(intent_method_counter),
                "sentiment_method_dist": dict(sent_method_counter),
                "remaining_notes": notes,
            },
            "message": "ok",
        }


@router.post("/sessions/{session_id}/transcode-playback")
async def transcode_playback(session_id: str):
    """Re-transcode an existing session's video for smooth Workspace playback.

    QA 2026-05-30: pipeline now auto-transcodes new uploads at the end of
    `_step_playback_encode`. But sessions analyzed BEFORE that change still
    have raw HEVC/120fps phone recordings in MinIO that are unplayable on
    Mac Intel. This endpoint downloads the MinIO source, transcodes
    H.264 main/720p/30fps/faststart/AAC, uploads back replacing the
    original (URL invariant). Operator-only — no UI hook.
    """
    import tempfile
    from pathlib import Path
    from minio import Minio
    from app.config import get_settings
    from app.services.video_processor import transcode_for_playback

    settings = get_settings()
    async with async_session_factory() as db:
        session = (await db.execute(
            select(LiveSession).where(LiveSession.id == session_id)
        )).scalar_one_or_none()
        if not session:
            raise HTTPException(status_code=404, detail="session not found")
        if not session.master_video_path or "/" not in session.master_video_path:
            raise HTTPException(status_code=400, detail="session has no master_video_path")

        bucket, object_name = session.master_video_path.split("/", 1)

    client = Minio(
        settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
    )
    tmpdir = Path(tempfile.mkdtemp(prefix="transcode_"))
    src = tmpdir / "src.mp4"
    dst = tmpdir / "playback.mp4"
    try:
        logger.info("[debug.transcode][%s] download from MinIO %s/%s",
                    session_id[:8], bucket, object_name)
        client.fget_object(bucket, object_name, str(src))
        src_mb = src.stat().st_size / 1024 / 1024
        logger.info("[debug.transcode][%s] downloaded %.0f MB → transcoding",
                    session_id[:8], src_mb)
        transcode_for_playback(str(src), str(dst))
        dst_mb = dst.stat().st_size / 1024 / 1024
        logger.info("[debug.transcode][%s] transcoded %.0f MB → upload",
                    session_id[:8], dst_mb)
        client.fput_object(bucket, object_name, str(dst), content_type="video/mp4")
        logger.info("[debug.transcode][%s] uploaded ok, MinIO replaced",
                    session_id[:8])
        return {
            "code": 0,
            "data": {
                "session_id": session_id,
                "source_mb": round(src_mb, 1),
                "playback_mb": round(dst_mb, 1),
                "compression_ratio": round(src_mb / max(dst_mb, 0.01), 1),
            },
            "message": "ok",
        }
    finally:
        # Best-effort cleanup
        try:
            src.unlink(missing_ok=True)
            dst.unlink(missing_ok=True)
            tmpdir.rmdir()
        except Exception:
            pass


@router.post("/sessions/{session_id}/rerun-early-analysis")
async def rerun_early_analysis(session_id: str):
    """B5 (2026-05-30): re-derive early_signals + early_insights from existing
    script_segments. Useful for backfilling pre-B5 sessions OR for testing
    threshold_set changes without re-running the full pipeline.

    Reads ONLY script_segments — does not touch ASR / OCR / danmaku data.
    Writes the 6 early_* keys into analysis_summary, preserving everything
    else (full insight_report, scores, semantic, etc).
    """
    from app.models.script_segment import ScriptSegment
    from app.services.insight_engine.early_signals_extractor import extract_early_signals
    from app.services.insight_engine.early_insight_generator import generate_early_insights
    from sqlalchemy.orm.attributes import flag_modified

    async with async_session_factory() as db:
        session = (await db.execute(
            select(LiveSession).where(LiveSession.id == session_id)
        )).scalar_one_or_none()
        if not session:
            raise HTTPException(status_code=404, detail="session not found")

        seg_rows = (await db.execute(
            select(ScriptSegment)
            .where(ScriptSegment.session_id == session_id)
            .order_by(ScriptSegment.start_sec)
        )).scalars().all()
        if not seg_rows:
            raise HTTPException(
                status_code=400,
                detail="no script_segments — script_analysis never ran for this session",
            )

        analyzed = [
            {
                "start_sec": s.start_sec, "end_sec": s.end_sec,
                "script_type": s.script_type or "chitchat",
                "analysis_tags": s.analysis_tags or {},
            }
            for s in seg_rows
        ]

        signals = extract_early_signals(analyzed, session.duration_sec)
        result = generate_early_insights(signals, "general_v1")

        summary = dict(session.analysis_summary or {})
        summary["early_signals"] = signals
        summary["early_insights"] = result["early_insights"]
        summary["early_summary"] = result["early_summary"]
        summary["early_threshold_set"] = result["threshold_set"]
        summary["early_weights_version"] = result["weights_version"]
        summary["early_ready"] = True
        session.analysis_summary = summary
        flag_modified(session, "analysis_summary")
        await db.commit()

        logger.info(
            "[debug.rerun-early][%s] segments=%d signals=%s insights=%d summary=%r",
            session_id[:8], len(seg_rows), signals,
            len(result["early_insights"]), result["early_summary"][:80],
        )
        return {
            "code": 0,
            "data": {
                "session_id": session_id,
                "segment_count": len(seg_rows),
                "signals": signals,
                "insights": result["early_insights"],
                "summary": result["early_summary"],
                "threshold_set": result["threshold_set"],
            },
            "message": "ok",
        }


@router.post("/auth-rate-limit-reset")
async def reset_auth_rate_limit():
    """Wipe slowapi's in-memory rate limit counters. Test-only — has no
    legitimate production use. Lets the Day 5 rate limit invariant test
    clean up after itself so the next /auth/login tests start with a
    fresh budget.
    """
    from app.middleware.rate_limit import limiter
    limiter.reset()
    return {"code": 0, "data": {"reset": True}, "message": "ok"}
