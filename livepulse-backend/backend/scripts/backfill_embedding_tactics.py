"""One-off backfill: compute and persist embedding_tactics for session 7981bae7.

The _step_tactics had written embedding_tactics to ctx.analyzed (memory) but never
persisted to DB. This script computes tactics from existing segments and writes back.

Run from backend/ directory:
    python scripts/backfill_embedding_tactics.py
"""
import asyncio
import logging
import sys
sys.path.insert(0, ".")

from app.database import async_session_factory
from app.models.script_segment import ScriptSegment
from sqlalchemy import select

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

SESSION_ID = "7981bae7-ad0e-4847-8a04-33bc76eeca9a"


async def main():
    from app.services.tactics.embedding_tactic_analyzer import get_tactic_analyzer

    async with async_session_factory() as db:
        # Load segments
        result = await db.execute(
            select(ScriptSegment)
            .where(ScriptSegment.session_id == SESSION_ID)
            .order_by(ScriptSegment.start_sec)
        )
        segments = result.scalars().all()
        logger.info("Loaded %d segments", len(segments))

        if not segments:
            logger.error("No segments found for %s", SESSION_ID)
            return

        # Compute tactics
        tactic_analyzer = get_tactic_analyzer()
        texts = [s.text for s in segments]
        tactic_results = tactic_analyzer.analyze_batch(texts)

        # Persist
        updated = 0
        for seg, tact in zip(segments, tactic_results):
            if not tact["tactics"]:
                continue
            tags = dict(seg.analysis_tags or {})
            tags["embedding_tactics"] = tact["tactics"]
            tags["tactic_score_distribution"] = tact.get("score_distribution", {})
            seg.analysis_tags = tags
            updated += 1

        await db.commit()
        logger.info("Persisted embedding_tactics to %d/%d segments", updated, len(segments))

        # Verify
        verify = await db.execute(
            select(ScriptSegment)
            .where(ScriptSegment.session_id == SESSION_ID)
            .limit(1)
        )
        s = verify.scalars().first()
        tags = s.analysis_tags or {}
        has = "embedding_tactics" in tags
        count = len(tags.get("embedding_tactics", []))
        logger.info("Verification: has_embedding_tactics=%s count=%d", has, count)


if __name__ == "__main__":
    asyncio.run(main())
