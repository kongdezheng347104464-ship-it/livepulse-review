"""Session lifecycle recovery — auto-detects and recovers zombie sessions.

A zombie session is one stuck in a locked state (processing/analyzing/running)
with no active pipeline worker. Recovery marks them as 'error' so the UI can
offer retry instead of showing them as forever-processing.

Invoked on application startup via the lifespan handler.
"""

import logging
from datetime import datetime, timezone, timedelta
from sqlalchemy import select
from app.database import async_session_factory
from app.models.session import LiveSession

logger = logging.getLogger(__name__)

LOCKED_STATES = {"processing", "analyzing", "running"}
ZOMBIE_THRESHOLD = timedelta(minutes=30)


async def recover_zombie_sessions() -> int:
    """Find and recover zombie sessions. Returns count of recovered sessions."""
    async with async_session_factory() as db:
        # Scope the query to locked rows only — full-table scans on startup
        # got expensive once we accumulated a few hundred sessions.
        result = await db.execute(
            select(LiveSession).where(LiveSession.status.in_(LOCKED_STATES))
        )
        sessions = result.scalars().all()
        now = datetime.now(timezone.utc)
        recovered = 0

        for s in sessions:
            started = s.analysis_started_at
            is_zombie = False

            if started is None:
                # Never received a heartbeat → permanent zombie
                is_zombie = True
            elif now - started > ZOMBIE_THRESHOLD:
                is_zombie = True

            if is_zombie:
                s.status = "error"
                s.error_message = (
                    f"Zombie session recovered at {now.isoformat()}: "
                    "pipeline process lost or never started"
                )
                s.analysis_finished_at = now
                recovered += 1
                logger.warning(
                    "Recovered zombie session %s (status was %s, title=%s)",
                    s.id, s.status, s.title,
                )

        if recovered:
            await db.commit()
            logger.info("Session recovery: %d zombies recovered", recovered)

        return recovered
