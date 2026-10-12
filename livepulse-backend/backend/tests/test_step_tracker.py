"""Tests for StepTracker — pipeline step state machine.

StepTracker is the observability backbone. If it silently fails:
- Frontend progress bar stalls forever
- Pipeline debugging becomes impossible
"""
import pytest
from unittest.mock import AsyncMock, MagicMock


# ---------------------------------------------------------------------------
# Helper: create a mocked StepTracker with a fake db session
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_db():
    """Mock async DB session.

    `commit` is async because StepTracker._upsert() awaits it — the
    QA 2026-05-29 P-1 fix switched flush → commit so the cross-session
    `/sessions/{id}/status` poll can see in-progress steps. Production
    code no longer touches flush, but it stays mocked for any historical
    test that references it (harmless extra attribute).
    """
    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    return db


@pytest.fixture
def tracker(mock_db):
    """Create a StepTracker for testing."""
    from app.services.step_tracker import StepTracker
    return StepTracker(mock_db, session_id="test-sid", step_name="video_processing", step_order=0)


# ---------------------------------------------------------------------------
# State transitions
# ---------------------------------------------------------------------------

class TestStateTransitions:
    @pytest.mark.asyncio
    async def test_initial_state_is_pending(self, tracker):
        assert tracker.status == "pending"
        assert tracker.start_time is None
        assert tracker.end_time is None

    @pytest.mark.asyncio
    async def test_start_transitions_to_running(self, tracker):
        await tracker.start({"video": "test.mp4"})
        assert tracker.status == "running"
        assert tracker.start_time is not None
        assert tracker.input_data == {"video": "test.mp4"}

    @pytest.mark.asyncio
    async def test_done_transitions_from_running(self, tracker):
        await tracker.start()
        await tracker.done({"duration_sec": 60})
        assert tracker.status == "done"
        assert tracker.end_time is not None
        assert tracker.output_data == {"duration_sec": 60}

    @pytest.mark.asyncio
    async def test_fail_transitions_from_running(self, tracker):
        await tracker.start()
        await tracker.fail("ffmpeg not found")
        assert tracker.status == "failed"
        assert tracker.error[:30] == "ffmpeg not found"

    @pytest.mark.asyncio
    async def test_skip_marks_as_skipped_with_reason(self, tracker):
        await tracker.skip("No OCR data available")
        assert tracker.status == "skipped"
        assert "OCR" in tracker.error

    @pytest.mark.asyncio
    async def test_error_truncated_to_2000_chars(self, tracker):
        await tracker.start()
        long_error = "x" * 3000
        await tracker.fail(long_error)
        assert len(tracker.error) == 2000

    @pytest.mark.asyncio
    async def test_elapsed_ms_before_done(self, tracker):
        await tracker.start()
        elapsed = tracker.elapsed_ms
        assert elapsed >= 0
        # After done, elapsed should be stable
        await tracker.done()
        assert tracker.elapsed_ms >= 0


# ---------------------------------------------------------------------------
# DB persistence (mocked)
# ---------------------------------------------------------------------------

class TestDBPersistence:
    @pytest.mark.asyncio
    async def test_start_writes_insert_to_db(self, mock_db):
        t = _make_tracker(mock_db)
        await t.start({"input": "test"})
        mock_db.add.assert_called_once()
        # P-1 fix: persistence is via commit, not flush. The cross-session
        # status poll endpoint depends on commit visibility.
        mock_db.commit.assert_called()

    @pytest.mark.asyncio
    async def test_done_writes_update_to_db(self, mock_db):
        t = _make_tracker(mock_db)
        await t.start()
        await t.done({"result": "ok"})
        # start() → add + commit; done() → execute (update) + commit.
        # So commit must fire at least twice.
        assert mock_db.commit.call_count >= 2

    @pytest.mark.asyncio
    async def test_fail_writes_error_to_db(self, mock_db):
        t = _make_tracker(mock_db)
        await t.start()
        await t.fail("Disk full")
        assert t.status == "failed"


# ---------------------------------------------------------------------------
# create_step_trackers
# ---------------------------------------------------------------------------

class TestCreateStepTrackers:
    @pytest.mark.asyncio
    async def test_creates_all_steps(self, mock_db):
        from app.services.step_tracker import create_step_trackers, STEP_ORDER
        trackers = await create_step_trackers(mock_db, "sid")
        # Assert against the live STEP_ORDER constant rather than a
        # hardcoded count — new steps land here and would otherwise
        # silently regress this test (B5 2026-05-30 added early_analysis,
        # earlier additions: viewer_count + playback_encode).
        assert len(trackers) == len(STEP_ORDER)
        for name in STEP_ORDER:
            assert name in trackers
            assert trackers[name].step_name == name

    @pytest.mark.asyncio
    async def test_step_order_is_sequential(self, mock_db):
        from app.services.step_tracker import create_step_trackers
        trackers = await create_step_trackers(mock_db, "sid")
        for i, (name, t) in enumerate(trackers.items()):
            assert t.step_order == i


def _make_tracker(db):
    from app.services.step_tracker import StepTracker
    return StepTracker(db, session_id="test-sid", step_name="test_step", step_order=0)
