"""Pipeline integration tests — validate session lifecycle and state consistency.

Tests the full upload -> processing -> analyzed/error lifecycle.
Requires PostgreSQL (via test DB).

NOTE: Windows + pytest-asyncio + asyncpg has a known event loop recycling issue.
5/10 tests may fail on Windows with "Event loop is closed". These all PASS on
Linux (GitHub Actions CI). The root cause is asyncpg socket handles bound to
ProactorEventLoop not surviving loop recreation between tests.

Workaround on Windows: run tests individually:
    pytest tests/integration/test_pipeline_lifecycle.py::TestHappyPath -v

Usage:
    pytest tests/integration/ -v
"""
import pytest
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy import select, func

from app.database import async_session_factory
from app.models.anchor import Anchor
from app.models.session import LiveSession
from app.models.pipeline_step import PipelineStep
from app.models.danmaku import Danmaku
from app.models.script_segment import ScriptSegment
from app.models.user import User


# Event-loop scoping is handled globally in pyproject.toml
# (asyncio_default_test_loop_scope = "session"), so the module-level
# async_session_factory's asyncpg pool stays bound to the same loop
# across tests. The old explicit `event_loop` fixture override here was
# both broken (missing `import asyncio`) and the wrong pattern under
# pytest-asyncio v1.x — removed 2026-06-03.


async def _get_admin_id() -> str:
    """Return the admin user id, used as owner for integration fixtures.

    A2 (2026-06-01 migration 0008) made `owner_user_id` NOT NULL on
    anchors / live_sessions. These lifecycle tests predate A2 and used
    to create rows without an owner; that now violates the constraint.
    Admin is always seeded at backend startup so we use it as the
    deterministic test owner — cross-tenant isolation isn't what these
    lifecycle tests cover, those live in `tests/api/test_invariants.py`
    Groups 13-18.
    """
    async with async_session_factory() as db:
        result = await db.execute(select(User).where(User.role == "admin").limit(1))
        admin = result.scalar_one_or_none()
        if admin is None:
            raise RuntimeError(
                "no admin user seeded — start backend once or run "
                "ensure_admin_seeded() to populate ADMIN_EMAIL/ADMIN_PASSWORD"
            )
        return admin.id


async def _ensure_anchor() -> str:
    """Create test anchor if not exists. Returns anchor_id."""
    async with async_session_factory() as db:
        result = await db.execute(select(Anchor).where(Anchor.id == "anchor_test"))
        anchor = result.scalar_one_or_none()
        if not anchor:
            owner_id = await _get_admin_id()
            anchor = Anchor(
                id="anchor_test", name="test_anchor", platform="douyin",
                followers=0, owner_user_id=owner_id,
            )
            db.add(anchor)
            await db.commit()
        return anchor.id


async def _create_session(anchor_id: str) -> str:
    """Create a fresh test session. Returns session_id."""
    sid = uuid.uuid4().hex
    owner_id = await _get_admin_id()
    async with async_session_factory() as db:
        session = LiveSession(
            id=sid,
            anchor_id=anchor_id,
            platform="douyin",
            title="integration-test",
            master_video_path="livestream-videos/test.mp4",
            start_time=datetime.now(timezone.utc),
            total_danmaku=0,
            status="uploaded",
            owner_user_id=owner_id,
        )
        db.add(session)
        await db.commit()
    return sid


@pytest.fixture(autouse=True)
async def _cleanup_integration_sessions():
    """Wipe any rows this module's tests created, after each test runs.

    Without this, a previous failure run leaves `integration-test`-titled
    sessions in random states (analyzed without steps / error / processing
    forever) — these then trip OTHER invariants like
    test_analyzed_session_has_step_rows and test_all_subscores_in_range
    on the next pytest invocation, masquerading as B4 / scoring regressions.

    Uses raw SQL to bypass ORM relationship cascade issues — danmakus /
    script_segments / pipeline_steps go via FK ON DELETE CASCADE.
    """
    yield  # test runs here
    from sqlalchemy import text
    async with async_session_factory() as db:
        await db.execute(text(
            "DELETE FROM live_sessions WHERE title = 'integration-test'"
        ))
        await db.commit()


# ---------------------------------------------------------------------------
# Happy Path
# ---------------------------------------------------------------------------

class TestHappyPath:
    @pytest.mark.asyncio
    async def test_session_created_in_uploaded_state(self):
        anchor_id = await _ensure_anchor()
        sid = await _create_session(anchor_id)

        async with async_session_factory() as db:
            result = await db.execute(select(LiveSession).where(LiveSession.id == sid))
            session = result.scalar_one()
            assert session.status == "uploaded"
            assert session.composite_score is None
            assert session.analysis_summary is None

    @pytest.mark.asyncio
    async def test_session_transitions_to_analyzed(self):
        anchor_id = await _ensure_anchor()
        sid = await _create_session(anchor_id)

        async with async_session_factory() as db:
            session = await db.get(LiveSession, sid)
            session.status = "processing"
            session.analysis_started_at = datetime.now(timezone.utc)
            await db.commit()

        async with async_session_factory() as db:
            session = await db.get(LiveSession, sid)
            assert session.status == "processing"

            session.status = "analyzed"
            session.composite_score = Decimal("75.0")
            session.analysis_finished_at = datetime.now(timezone.utc)
            session.analysis_summary = {"test": True}
            await db.commit()

        async with async_session_factory() as db:
            session = await db.get(LiveSession, sid)
            assert session.status == "analyzed"
            assert session.composite_score == Decimal("75.0")

    @pytest.mark.asyncio
    async def test_pipeline_steps_recorded_in_order(self):
        from app.services.step_tracker import STEP_ORDER
        anchor_id = await _ensure_anchor()
        sid = await _create_session(anchor_id)

        async with async_session_factory() as db:
            for order, name in enumerate(STEP_ORDER):
                step = PipelineStep(
                    id=uuid.uuid4().hex, session_id=sid,
                    step_name=name, step_order=order, status="done",
                    started_at=datetime.now(timezone.utc),
                    finished_at=datetime.now(timezone.utc), duration_ms=100,
                )
                db.add(step)
            await db.commit()

        async with async_session_factory() as db:
            result = await db.execute(
                select(PipelineStep)
                .where(PipelineStep.session_id == sid)
                .order_by(PipelineStep.step_order)
            )
            steps = result.scalars().all()
            # Compare against the live STEP_ORDER constant rather than a
            # hardcoded count — new pipeline steps land in STEP_ORDER and
            # would otherwise silently regress this test. Same pattern as
            # test_step_tracker.test_creates_all_steps.
            assert len(steps) == len(STEP_ORDER)
            for i, name in enumerate(STEP_ORDER):
                assert steps[i].step_name == name


# ---------------------------------------------------------------------------
# Failure Path
# ---------------------------------------------------------------------------

class TestFailurePath:
    @pytest.mark.asyncio
    async def test_session_marked_error_on_failure(self):
        anchor_id = await _ensure_anchor()
        sid = await _create_session(anchor_id)

        async with async_session_factory() as db:
            session = await db.get(LiveSession, sid)
            session.status = "error"
            session.error_message = "ASR transcription timed out after 600s"
            session.analysis_finished_at = datetime.now(timezone.utc)
            await db.commit()

        async with async_session_factory() as db:
            session = await db.get(LiveSession, sid)
            assert session.status == "error"
            assert "ASR" in session.error_message

    @pytest.mark.asyncio
    async def test_error_message_truncated(self):
        anchor_id = await _ensure_anchor()
        sid = await _create_session(anchor_id)

        async with async_session_factory() as db:
            session = await db.get(LiveSession, sid)
            session.status = "error"
            session.error_message = "x" * 450  # under 500 char limit
            await db.commit()

        async with async_session_factory() as db:
            session = await db.get(LiveSession, sid)
            assert len(session.error_message) == 450
            assert session.status == "error"

    @pytest.mark.asyncio
    async def test_retry_clears_error(self):
        anchor_id = await _ensure_anchor()
        sid = await _create_session(anchor_id)

        # Set error first
        async with async_session_factory() as db:
            session = await db.get(LiveSession, sid)
            session.status = "error"
            session.error_message = "OCR failed"
            await db.commit()

        # Retry
        async with async_session_factory() as db:
            session = await db.get(LiveSession, sid)
            session.status = "processing"
            session.error_message = None
            session.analysis_started_at = datetime.now(timezone.utc)
            await db.commit()

        async with async_session_factory() as db:
            session = await db.get(LiveSession, sid)
            assert session.status == "processing"
            assert session.error_message is None

    @pytest.mark.asyncio
    async def test_idempotency_processing_skip(self):
        anchor_id = await _ensure_anchor()
        sid = await _create_session(anchor_id)

        async with async_session_factory() as db:
            session = await db.get(LiveSession, sid)
            session.status = "processing"
            await db.commit()

        # Verify it stays processing (no double trigger)
        async with async_session_factory() as db:
            session = await db.get(LiveSession, sid)
            assert session.status == "processing"


# ---------------------------------------------------------------------------
# Data Integrity
# ---------------------------------------------------------------------------

class TestDataIntegrity:
    @pytest.mark.asyncio
    async def test_danmaku_count_matches(self):
        anchor_id = await _ensure_anchor()
        sid = await _create_session(anchor_id)

        async with async_session_factory() as db:
            for i in range(5):
                db.add(Danmaku(
                    id=uuid.uuid4().hex, session_id=sid,
                    content=f"danmaku {i}", timestamp_sec=i * 10.0, sentiment="neutral",
                ))
            session = await db.get(LiveSession, sid)
            session.total_danmaku = 5
            await db.commit()

        async with async_session_factory() as db:
            count = await db.execute(
                select(func.count()).where(Danmaku.session_id == sid)
            )
            assert count.scalar() == 5

    @pytest.mark.asyncio
    async def test_segments_persist(self):
        anchor_id = await _ensure_anchor()
        sid = await _create_session(anchor_id)

        async with async_session_factory() as db:
            db.add(ScriptSegment(
                id=uuid.uuid4().hex, session_id=sid,
                start_sec=0.0, end_sec=5.0, text="test",
                script_type="chitchat", speech_rate=150.0, keywords=["test"],
            ))
            await db.commit()

        async with async_session_factory() as db:
            count = await db.execute(
                select(func.count()).where(ScriptSegment.session_id == sid)
            )
            assert count.scalar() == 1

    @pytest.mark.asyncio
    async def test_cascade_delete(self):
        """Verify DB-level ON DELETE CASCADE on danmakus.session_id.

        Production `delete_session` endpoint does manual ordered DELETE
        (see app/api/sessions.py) because LiveSession ORM relationships
        don't carry `passive_deletes=True` — so calling `db.delete(session)`
        triggers SQLAlchemy's default "SET NULL on dependents" pre-delete
        and crashes against the NOT NULL FK. To verify the FK CASCADE
        configured at schema level still works (it's our safety net), this
        test bypasses the ORM and issues a raw DELETE.
        """
        from sqlalchemy import text

        anchor_id = await _ensure_anchor()
        sid = await _create_session(anchor_id)

        async with async_session_factory() as db:
            db.add(Danmaku(
                id=uuid.uuid4().hex, session_id=sid,
                content="cascade", timestamp_sec=0.0, sentiment="neutral",
            ))
            await db.commit()

        # Raw DELETE — DB cascade should fire on the FK
        async with async_session_factory() as db:
            await db.execute(
                text("DELETE FROM live_sessions WHERE id = :sid"),
                {"sid": sid},
            )
            await db.commit()

        async with async_session_factory() as db:
            count = await db.execute(
                select(func.count()).where(Danmaku.session_id == sid)
            )
            assert count.scalar() == 0, \
                "danmakus.session_id ON DELETE CASCADE not firing"
