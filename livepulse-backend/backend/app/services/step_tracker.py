"""Lightweight pipeline step tracker.

Each step records: name, status, timing, I/O, errors.
Writes directly to DB via the pipeline's async session.
"""
import time
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Step order defines the FSM sequence.
# `viewer_count` was added 2026-05-28 as part of the live-event telemetry
# rebuild — it sits adjacent to `ocr` because both consume the same set of
# cropped frames from video_processing, just from different regions of the
# screen (interaction list vs viewer badge). Both are non-fatal: a missing
# viewer_count series degrades normalization metrics to fallbacks, not a
# pipeline failure.
STEP_ORDER = [
    "video_processing",
    "asr",
    "script_analysis",
    "early_analysis",   # B5 2026-05-30: script-only early insights (5-min Top 3)
    "ocr",
    "viewer_count",
    "danmaku_analysis",
    "correlation",
    "scoring",
    "insight_engine",
    "playback_encode",  # QA 2026-05-30: H.264 720p re-encode for browser playback
]


class StepTracker:
    """Tracks one pipeline step during execution."""

    def __init__(self, db, session_id: str, step_name: str, step_order: int = 0,
                 db_lock=None):
        self.db = db
        self.session_id = session_id
        self.step_name = step_name
        self.step_order = step_order
        self.status = "pending"
        self.start_time: float | None = None
        self.end_time: float | None = None
        self.input_data: dict | None = None
        self.output_data: dict | None = None
        self.error: str | None = None
        self._record_id: str | None = None
        # Day 3 (2026-06-03): optional asyncio.Lock to serialize db ops
        # across concurrent pipeline branches. Outside parallel phase it's
        # uncontested. See _async_pipeline branch orchestration.
        self.db_lock = db_lock

    async def start(self, input_info: dict | None = None):
        """Mark step as running and record start time."""
        self.status = "running"
        self.start_time = time.time()
        self.input_data = input_info or {}
        await self._upsert()

    async def done(self, output_info: dict | None = None):
        """Mark step as done and record output."""
        self.end_time = time.time()
        self.status = "done"
        self.output_data = output_info or {}
        await self._upsert()

    async def fail(self, error: str):
        """Mark step as failed with error message."""
        self.end_time = time.time()
        self.status = "failed"
        self.error = error[:2000]  # truncate
        await self._upsert()
        logger.error("[%s] FAILED: %s", self.step_name, error[:200])

    async def skip(self, reason: str = ""):
        """Mark step as skipped."""
        self.status = "skipped"
        self.error = reason[:2000]
        await self._upsert()

    @property
    def elapsed_ms(self) -> int:
        if self.start_time:
            end = self.end_time or time.time()
            return int((end - self.start_time) * 1000)
        return 0

    async def _upsert(self):
        """Write current state to DB. Lock-protected so concurrent branches
        in _async_pipeline don't race on the shared AsyncSession."""
        from app.models.pipeline_step import PipelineStep
        from contextlib import nullcontext
        import uuid

        now = datetime.now(timezone.utc)

        values = {
            "session_id": self.session_id,
            "step_name": self.step_name,
            "step_order": self.step_order,
            "status": self.status,
            "duration_ms": self.elapsed_ms if self.end_time else None,
            "input_info": self.input_data,
            "output_info": self.output_data,
            "error_message": self.error,
        }

        async with (self.db_lock or nullcontext()):
            if self._record_id:
                # Update existing record
                from sqlalchemy import update
                stmt = (
                    update(PipelineStep)
                    .where(PipelineStep.id == self._record_id)
                    .values(**values, finished_at=now if self.end_time else None)
                )
                await self.db.execute(stmt)
            else:
                # Insert new record
                self._record_id = str(uuid.uuid4())
                step = PipelineStep(
                    id=self._record_id,
                    started_at=now,
                    **values,
                )
                self.db.add(step)

            # CRITICAL: commit, not flush. See P-1 fix 2026-05-29 — the
            # /sessions/{id}/status endpoint queries via a SEPARATE session,
            # so flush() leaves it invisible due to transaction isolation
            # and the user sees "0% 准备中 for 22 minutes". Commit (per-step
            # transaction boundary) is the antidote.
            #
            # Day 3 (2026-06-03): commit is now INSIDE the db_lock so
            # parallel ASR/OCR branches don't trample each other on the
            # shared AsyncSession's connection state.
            await self.db.commit()


async def create_step_trackers(
    db, session_id: str, db_lock=None
) -> dict[str, StepTracker]:
    """Create step trackers for all pipeline steps.

    db_lock (optional asyncio.Lock): passed through to every tracker so
    concurrent pipeline branches serialize their db ops. See _async_pipeline.

    Returns a dict keyed by step_name for easy access.
    """
    trackers = {}
    for order, name in enumerate(STEP_ORDER):
        t = StepTracker(db, session_id, name, order, db_lock=db_lock)
        trackers[name] = t
    return trackers
