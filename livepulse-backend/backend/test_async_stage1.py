"""Stage 1: Async ASR verification test."""
import sys
import os
import time
import asyncio
import logging
import subprocess
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
sys.path.insert(0, os.path.dirname(__file__))

print("=== Stage 1: Async ASR Verification ===\n")

# Step 0: create 60s test video
print("[Step 0] Creating 60s test video from MinIO...")
from minio import Minio
c = Minio("localhost:9002", access_key="minioadmin", secret_key="minioadmin123", secure=False)
c.fget_object("livestream-videos", "3efc3689b78c407799ef82499ec6a8e4.mp4", "/tmp/full_video.mp4")

from app.config import get_settings; s = get_settings()
subprocess.run([s.ffmpeg_path, "-y", "-ss", "30", "-t", "60", "-i", "/tmp/full_video.mp4",
                "-c", "copy", "/tmp/test_short.mp4"], check=True, capture_output=True)
print(f"  Test video: {os.path.getsize('/tmp/test_short.mp4')/1024/1024:.1f}MB (60s)")

# Step 1: upload to MinIO
c.fput_object("livestream-videos", "test_async_verify.mp4", "/tmp/test_short.mp4")
print("[Step 1] MinIO upload OK")

# Step 2: create session via ORM
print("[Step 2] Creating test session via ORM...")
from app.database import async_session_factory
from app.models import Anchor, LiveSession
from datetime import datetime, timezone
import uuid

async def run_test():
    async with async_session_factory() as db:
        # Create anchor
        anchor = await db.get(Anchor, "anchor_test")
        if not anchor:
            anchor = Anchor(id="anchor_test", name="test_anchor", platform="douyin", followers=0)
            db.add(anchor)
            await db.flush()

        # Create session
        sid = uuid.uuid4().hex
        session = LiveSession(
            id=sid, anchor_id="anchor_test", platform="douyin",
            title="async-test", master_video_path="livestream-videos/test_async_verify.mp4",
            start_time=datetime.now(timezone.utc), total_danmaku=0, status="uploaded",
        )
        db.add(session)
        await db.commit()
        print(f"  Session: {sid}\n")

    # Step 3: run pipeline with heartbeat
    print("[Step 3] Running pipeline with heartbeat monitor...\n")

    heartbeat_stop = False
    async def heartbeat():
        while not heartbeat_stop:
            await asyncio.sleep(2)
            print("  [heartbeat] event loop alive")

    from app.tasks.pipeline import run_analysis_async

    hb = asyncio.create_task(heartbeat())
    t0 = time.time()

    try:
        result = await run_analysis_async(sid)
        elapsed = time.time() - t0
        heartbeat_stop = True
        await hb
        print(f"\n  Pipeline completed in {elapsed:.0f}s: {result}")

        # Check steps via ORM
        from app.models.pipeline_step import PipelineStep
        from sqlalchemy import select as sa_select
        async with async_session_factory() as db2:
            steps_result = await db2.execute(
                sa_select(PipelineStep)
                .where(PipelineStep.session_id == sid)
                .order_by(PipelineStep.step_order)
            )
            steps = steps_result.scalars().all()
            print("\n  Steps:")
            passed = 0
            for st in steps:
                dur = f"{(st.duration_ms/1000):.1f}s" if st.duration_ms else "-"
                icon = "✅" if st.status == "done" else "⏭️" if st.status == "skipped" else "❌"
                if st.status == "done": passed += 1
                print(f"    {icon} {st.step_name:20s} | {st.status:8s} | {dur}")

        no_skipped = all(st.status not in ("failed",) for st in steps)
        return no_skipped and passed >= 3  # at least video_processing + asr + script_analysis
    except Exception as e:
        elapsed = time.time() - t0
        heartbeat_stop = True
        await hb
        print(f"\n  FAILED after {elapsed:.0f}s: {e}")
        import traceback; traceback.print_exc()
        return False

ok = asyncio.run(run_test())
print(f"\n{'='*50}")
print(f"Stage 1: {'PASS' if ok else 'FAIL'}")
