"""Regression test: runs full pipeline on 60s video and validates all steps.

Usage:
    cd backend && ../.venv/Scripts/python.exe tests/regression.py

Exit code 0 = PASS, 1 = FAIL.
Must be run when Aliyun API is unfrozen (config.aliyun_frozen=False).

Checks:
    - All 8 steps complete (no failed)
    - Script segments > 0
    - Insight report generated
    - Composite score present
    - Danmaku present (OCR working)
"""
import sys
import os
import time
import asyncio
import logging
import subprocess
import json

logging.basicConfig(level=logging.WARNING, format="%(message)s")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.database import async_session_factory
MINIO_EP = "localhost:9002"
TEST_VIDEO_SRC = "3efc3689b78c407799ef82499ec6a8e4.mp4"

CHECKS_PASSED = 0
CHECKS_TOTAL = 0


def check(name: str, condition: bool, detail: str = ""):
    global CHECKS_PASSED, CHECKS_TOTAL
    CHECKS_TOTAL += 1
    if condition:
        CHECKS_PASSED += 1
        print(f"  ✅ {name}")
    else:
        print(f"  ❌ {name}: {detail}")


async def main():
    global CHECKS_PASSED, CHECKS_TOTAL
    print("=" * 55)
    print("REGRESSION TEST — Live Commerce Intelligence System")
    print("=" * 55)

    # ---- Step 0: Setup ----
    print("\n[Setup] Preparing 60s test video...")
    from minio import Minio
    from app.config import get_settings
    s = get_settings()

    # Ensure unfrozen
    if s.aliyun_frozen:
        print("  ❌ Aliyun API is frozen. Unfreeze before running regression test.")
        print("     Edit config.py: aliyun_frozen = False")
        sys.exit(1)

    c = Minio(MINIO_EP, access_key="minioadmin", secret_key="minioadmin123", secure=False)
    c.fget_object("livestream-videos", TEST_VIDEO_SRC, "/tmp/regr_full.mp4")
    subprocess.run([s.ffmpeg_path, "-y", "-ss", "60", "-t", "60", "-i", "/tmp/regr_full.mp4",
                    "-c", "copy", "/tmp/regr_test.mp4"], check=True, capture_output=True)
    c.fput_object("livestream-videos", "regr_test.mp4", "/tmp/regr_test.mp4")

    # ---- Step 1: Create test session ----
    print("\n[Step 1] Creating test session...")
    from app.models import Anchor, LiveSession, ScriptSegment, Danmaku
    from datetime import datetime, timezone
    import uuid

    async with async_session_factory() as db:
        anchor = await db.get(Anchor, "anchor_test")
        if not anchor:
            anchor = Anchor(id="anchor_test", name="regr_anchor", platform="douyin", followers=0)
            db.add(anchor); await db.flush()
        sid = uuid.uuid4().hex
        ses = LiveSession(id=sid, anchor_id="anchor_test", platform="douyin",
                         title="regression-test", master_video_path="livestream-videos/regr_test.mp4",
                         start_time=datetime.now(timezone.utc), total_danmaku=0, status="uploaded")
        db.add(ses); await db.commit()

    # ---- Step 2: Run pipeline ----
    print("\n[Step 2] Running pipeline...")
    from app.tasks.pipeline import run_analysis_async
    t0 = time.time()
    result = await run_analysis_async(sid)
    elapsed = time.time() - t0
    print(f"  Pipeline: {elapsed:.0f}s → code={result.get('code')}")

    # ---- Step 3: Validate ----
    print("\n[Step 3] Validating results...\n")
    from app.models.pipeline_step import PipelineStep
    from sqlalchemy import func, select as sa_select

    async with async_session_factory() as db:
        # 3.1 Session status
        session = await db.get(LiveSession, sid)
        check("Session analyzed", session.status == "analyzed", session.status)
        check("Composite score present", session.composite_score is not None)
        check("Composite score > 0", float(session.composite_score) > 0 if session.composite_score else False)

        # 3.2 Pipeline steps
        steps_result = await db.execute(
            sa_select(PipelineStep)
            .where(PipelineStep.session_id == sid)
            .order_by(PipelineStep.step_order)
        )
        steps = steps_result.scalars().all()
        step_map = {st.step_name: st.status for st in steps}
        expected_steps = ["video_processing", "asr", "script_analysis", "ocr",
                          "danmaku_analysis", "correlation", "scoring", "insight_engine"]
        for name in expected_steps:
            status = step_map.get(name, "missing")
            check(f"Step {name}", status in ("done", "skipped"), f"status={status}")

        # 3.3 Script segments
        seg_count_result = await db.execute(
            sa_select(func.count()).select_from(ScriptSegment).where(ScriptSegment.session_id == sid)
        )
        seg_count = seg_count_result.scalar()
        check("Script segments > 0", seg_count > 0, f"got {seg_count}")

        # 3.4 Danmaku
        dm_count_result = await db.execute(
            sa_select(func.count()).select_from(Danmaku).where(Danmaku.session_id == sid)
        )
        dm_count = dm_count_result.scalar()
        check("Danmaku generated", dm_count > 0, f"got {dm_count}")

        # 3.5 Insight report
        raw = session.analysis_summary
        asum = json.loads(raw) if isinstance(raw, str) else (raw or {})
        ir = asum.get("insight_report", {})
        check("Insight report present", bool(ir), "missing insight_report")
        check("Insights non-empty", len(ir.get("insights", [])) > 0)
        check("Recommendations present", len(ir.get("recommendations", [])) > 0)
        check("Timeline highlights present", len(ir.get("timeline_highlights", [])) > 0)

        # 3.6 Insight quality
        insights = ir.get("insights", [])
        if insights:
            first = insights[0]
            check("Insight has score", "score" in first)
            check("Insight has evidence", "evidence" in first)
            check("Insight has time_range", "time_range" in first)
            check("Insight has impact", "impact" in first)

        # 3.7 Profile snapshot
        ps = ir.get("profile_snapshot")
        check("Profile snapshot present", bool(ps))

    # ---- Result ----
    print(f"\n{'=' * 55}")
    print(f"RESULT: {CHECKS_PASSED}/{CHECKS_TOTAL} checks passed")
    # Cleanup
    if os.path.exists("/tmp/regr_full.mp4"):
        os.unlink("/tmp/regr_full.mp4")
    if os.path.exists("/tmp/regr_test.mp4"):
        os.unlink("/tmp/regr_test.mp4")

    if CHECKS_PASSED < CHECKS_TOTAL:
        print("REGRESSION FAILED — do not merge")
        sys.exit(1)
    else:
        print("REGRESSION PASSED — safe to merge")
        sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
