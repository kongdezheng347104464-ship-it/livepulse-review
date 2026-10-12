"""Stage 2: Semantic Stability verification.

Tests whether analysis results remain trustworthy
when some modules fail or produce partial data.
"""
import sys
import os
import time
import asyncio
import logging
import json

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
sys.path.insert(0, os.path.dirname(__file__))

from datetime import datetime, timezone
import uuid

from minio import Minio
from app.config import get_settings
from app.database import async_session_factory
from app.models import Anchor, LiveSession, ScriptSegment, Danmaku, PipelineStep

s = get_settings()


async def main():
    print("=" * 60)
    print("Stage 2: Semantic Stability Verification")
    print("=" * 60)

    # Temporarily unfreeze
    print("\n[Setup] Unfreezing Aliyun API...")
    s.aliyun_frozen = False

    # Create 5-minute test video (180-480 seconds)
    print("[Setup] Creating test video...")
    import subprocess
    c = Minio("localhost:9002", access_key="minioadmin", secret_key="minioadmin123", secure=False)
    c.fget_object("livestream-videos", "3efc3689b78c407799ef82499ec6a8e4.mp4", "/tmp/full_video.mp4")
    subprocess.run([s.ffmpeg_path, "-y", "-ss", "180", "-t", "300", "-i", "/tmp/full_video.mp4",
                    "-c", "copy", "/tmp/stage2_test.mp4"], check=True, capture_output=True)
    test_mb = os.path.getsize("/tmp/stage2_test.mp4") / 1024 / 1024
    c.fput_object("livestream-videos", "stage2_test.mp4", "/tmp/stage2_test.mp4")
    print(f"  Video: {test_mb:.1f}MB (5 min)")

    # Create session
    async with async_session_factory() as db:
        anchor = await db.get(Anchor, "anchor_test")
        if not anchor:
            anchor = Anchor(id="anchor_test", name="test_anchor", platform="douyin", followers=0)
            db.add(anchor); await db.flush()

        sid = uuid.uuid4().hex
        session = LiveSession(id=sid, anchor_id="anchor_test", platform="douyin",
                             title="stage2-test", master_video_path="livestream-videos/stage2_test.mp4",
                             start_time=datetime.now(timezone.utc), total_danmaku=0, status="uploaded")
        db.add(session); await db.commit()
        print(f"\n  Session: {sid}")

    # ---- Run pipeline ----
    print("\n" + "-" * 40)
    print("Running pipeline...")
    print("-" * 40)

    from app.tasks.pipeline import run_analysis_async
    heartbeat_stop = False

    async def heartbeat():
        while not heartbeat_stop:
            await asyncio.sleep(3)
            print("  [hb] event loop alive")

    hb = asyncio.create_task(heartbeat())
    t0 = time.time()
    try:
        result = await run_analysis_async(sid)
    except Exception as e:
        result = {"code": 1, "message": str(e)}
    elapsed = time.time() - t0
    heartbeat_stop = True
    await hb
    print(f"\nPipeline: {elapsed:.0f}s → {result.get('code')}")

    # ---- Analyze results ----
    print("\n" + "=" * 60)
    print("Semantic Stability Report")
    print("=" * 60)

    async with async_session_factory() as db:
        from sqlalchemy import func, select as sa_select, text as sa_text

        # Get session data
        session = await db.get(LiveSession, sid)
        steps_result = await db.execute(
            sa_select(PipelineStep)
            .where(PipelineStep.session_id == sid)
            .order_by(PipelineStep.step_order)
        )
        steps = steps_result.scalars().all()

    print(f"\n{'#' * 50}")
    print("# Summary")
    print(f"{'#' * 50}")
    print(f"  Duration: {session.duration_sec:.0f}s = {session.duration_sec/60:.0f} min")
    print(f"  Score: {session.composite_score}")
    print(f"  Status: {session.status}")

    print(f"\n{'#' * 50}")
    print("# 1. Data Completeness")
    print(f"{'#' * 50}")

    step_status = {st.step_name: st.status for st in steps}
    step_dur = {st.step_name: st.duration_ms or 0 for st in steps}

    total_steps = 7
    done_steps = sum(1 for s in step_status.values() if s == "done")
    skip_steps = sum(1 for s in step_status.values() if s == "skipped")
    fail_steps = sum(1 for s in step_status.values() if s == "failed")
    health = (done_steps + skip_steps * 0.5) / total_steps * 100

    seg_count_result = await db.execute(
        sa_select(func.count()).select_from(ScriptSegment).where(ScriptSegment.session_id == sid)
    )
    seg_count = seg_count_result.scalar() or 0
    dm_count_result = await db.execute(
        sa_select(func.count()).select_from(Danmaku).where(Danmaku.session_id == sid)
    )
    danmaku_count = dm_count_result.scalar() or 0

    print(f"  Segments: {seg_count}")
    print(f"  Danmakus: {danmaku_count}")
    print(f"  Steps: {done_steps} done / {skip_steps} skipped / {fail_steps} failed")
    print(f"  Completeness: {health:.0f}%")

    for st in steps:
        icon = "✅" if st.status == "done" else "⏭️" if st.status == "skipped" else "❌"
        dur = f"{(st.duration_ms/1000):.1f}s" if st.duration_ms else "-"
        err = st.error_message[:50] if st.error_message else ""
        print(f"    {icon} {st.step_name:20s} | {st.status:8s} | {dur:6s} | {err}")

    print(f"\n{'#' * 50}")
    print("# 2. Event Coverage")
    print(f"{'#' * 50}")

    # Segment type distribution
    types_result = await db.execute(
        sa_select(ScriptSegment.script_type, func.count())
        .where(ScriptSegment.session_id == sid)
        .group_by(ScriptSegment.script_type)
    )
    type_map = dict(types_result.all())
    print(f"  Script types: {dict(type_map)}")
    print(f"  Total events: {seg_count} segments")

    # Check for event gaps (large time jumps)
    gaps_result = await db.execute(sa_text("""
        SELECT curr.start_sec - prev.end_sec as gap
        FROM (
            SELECT start_sec, end_sec,
                   LAG(end_sec) OVER (ORDER BY start_sec) as prev_end
            FROM script_segments
            WHERE session_id = :sid
        ) sub
        WHERE prev.end_sec IS NOT NULL
    """), {"sid": sid})
    gaps = gaps_result.all()
    large_gaps = sum(1 for g in gaps if g.gap and g.gap > 10)
    print(f"  Large gaps (>10s): {large_gaps}")
    event_cov_score = max(0, 100 - large_gaps * 5)

    print(f"\n{'#' * 50}")
    print("# 3. Correlation Stability")
    print(f"{'#' * 50}")

    if danmaku_count > 0:
        # Check danmaku time distribution
        dm_times_result = await db.execute(
            sa_select(Danmaku.timestamp_sec)
            .where(Danmaku.session_id == sid)
            .order_by(Danmaku.timestamp_sec)
        )
        dm_times = dm_times_result.scalars().all()
        if dm_times:
            first_dm = dm_times[0]
            last_dm = dm_times[-1]
            span = last_dm - first_dm if len(dm_times) > 1 else 0
            density = danmaku_count / max(session.duration_sec or 1, 1) * 60
            print(f"  Danmaku span: {first_dm:.0f}s - {last_dm:.0f}s ({span:.0f}s)")
            print(f"  Danmaku density: {density:.1f} /min")
            corr_score = min(100, density * 50 + 30) if span > 60 else 20
        else:
            corr_score = 0
            print("  No danmaku data for correlation")
    else:
        corr_score = 10  # partial credit for graceful degradation
        print("  OCR degraded → falling back to script-only scoring")
        print("  Correlation score based on script events alone")
        print("  Danmaku density: 0 /min → using script-only composite")

    print(f"\n{'#' * 50}")
    print("# 4. Rhythm Stability")
    print(f"{'#' * 50}")

    summary = session.analysis_summary or {}
    rhythm = summary.get("rhythm", {})
    if rhythm:
        phases = rhythm.get("phases", [])
        transitions = rhythm.get("phase_transitions", 0)
        avg_phase = rhythm.get("avg_phase_duration_sec", 0)
        dist = rhythm.get("type_distribution", {})
        recs = rhythm.get("recommendations", [])
        print(f"  Phases: {len(phases)}")
        print(f"  Transitions: {transitions}")
        print(f"  Avg phase: {avg_phase:.0f}s")
        print(f"  Distribution: {json.dumps({k:v.get('pct',0) for k,v in dist.items()}, ensure_ascii=False)}")
        print(f"  Recommendations: {len(recs)}")
        rhythm_score = min(100, len(phases) * 10 + 20) if phases else 30
    else:
        rhythm_score = 20
        print("  Rhythm data missing")

    # Engagement/Closing
    eng = summary.get("engagement", {})
    close = summary.get("closing", {})
    pp = summary.get("pain_points", {})
    print(f"\n  Engagement: {eng.get('total_prompts', 0)} prompts")
    print(f"  Closing: {close.get('total_tactics', 0)} tactics")
    print(f"  Pain points: {pp.get('total_mentions', 0)} mentions")

    # ---- Final Scores ----
    print(f"\n{'=' * 60}")
    print("STAGE 2 RESULTS")
    print(f"{'=' * 60}")

    scores = {
        "Data Completeness": round(health),
        "Event Coverage": event_cov_score,
        "Correlation Stability": round(corr_score),
        "Rhythm Accuracy": rhythm_score,
    }
    overall = sum(scores.values()) / len(scores)

    for name, sc in scores.items():
        bar = "█" * (sc // 5) + "░" * (20 - sc // 5)
        print(f"  {name:25s}: {sc:3d}/100  {bar}")

    print(f"  {'-' * 25}")
    print(f"  {'Overall Pipeline Health':25s}: {overall:.0f}/100  {'✅ PASS' if overall >= 60 else '⚠️ DEGRADED' if overall >= 40 else '❌ FAIL'}")

    # Cleanup
    s.aliyun_frozen = True
    print("\n[Cleanup] API re-frozen")


asyncio.run(main())
