"""Insight Engine independent runner.

Reads raw data from DB, runs insight engine, writes back derived layer.
Uses app.database.async_session_factory — the single database entry point.
Never touches raw layer (ASR/OCR/segments/danmakus).
"""
import json
import logging
from sqlalchemy import select, text
from app.database import async_session_factory
from app.models.session import LiveSession
from app.models.script_segment import ScriptSegment
from app.models.danmaku import Danmaku
from .engine import run_insight_engine

logger = logging.getLogger(__name__)


async def run_for_session(session_id: str) -> dict:
    """Run insight engine on an already-analyzed session.

    Reads script_segments + danmakus via SQLAlchemy ORM.
    Runs pattern extraction -> ranking -> insight generation.
    Writes insight_report + profile_snapshot back to DB.
    Does NOT touch ASR, OCR, video processing, or raw data tables.

    Returns:
        Dict with {session_id, insights_count, recommendations_count, score}
    """
    async with async_session_factory() as db:
        # ---- Read session ----
        session = await db.get(LiveSession, session_id)
        if not session:
            return {"error": "session not found"}

        duration_sec = float(session.duration_sec or 0)
        if duration_sec <= 0:
            return {"error": "session duration unknown"}

        # Parse existing summary — MUST deep-copy to avoid SQLAlchemy JSONB mutation pitfall
        raw_sum = session.analysis_summary
        if isinstance(raw_sum, str):
            analysis_summary = json.loads(raw_sum)
        elif raw_sum is not None:
            analysis_summary = json.loads(json.dumps(raw_sum))  # round-trip copy
        else:
            analysis_summary = {}

        # ---- Read segments (ORM) ----
        seg_result = await db.execute(
            select(ScriptSegment)
            .where(ScriptSegment.session_id == session_id)
            .order_by(ScriptSegment.start_sec)
        )
        seg_rows = seg_result.scalars().all()

        script_segments = []
        for s in seg_rows:
            d = {
                "start_sec": s.start_sec,
                "end_sec": s.end_sec,
                "text": s.text or "",
                "score": float(s.score) if s.score else 50.0,
                "script_type": s.script_type or "chitchat",
                "speech_rate": s.speech_rate or 0,
            }
            tags = s.analysis_tags
            if isinstance(tags, str):
                tags = json.loads(tags)
            d["analysis_tags"] = tags or {}

            kw = s.keywords
            if isinstance(kw, str):
                kw = json.loads(kw)
            d["keywords"] = kw or []

            script_segments.append(d)

        # ---- Read danmakus (ORM) ----
        dm_result = await db.execute(
            select(Danmaku)
            .where(Danmaku.session_id == session_id)
            .order_by(Danmaku.timestamp_sec)
        )
        dm_rows = dm_result.scalars().all()
        danmakus = [
            {
                "timestamp_sec": d.timestamp_sec,
                "content": d.content,
                "sentiment": d.sentiment,
                "intent": d.intent,
            }
            for d in dm_rows
        ]

        # ---- Run insight engine ----
        insight_report = run_insight_engine(script_segments, danmakus, analysis_summary, duration_sec)

        # ---- Write derived layer ----
        now_result = await db.execute(text("SELECT NOW()"))
        insight_report["generated_at"] = str(now_result.scalar())
        analysis_summary["insight_report"] = insight_report
        analysis_summary["profile_snapshot"] = insight_report.get("profile_snapshot", {})

        session.analysis_summary = analysis_summary
        await db.commit()

        insights_count = len(insight_report.get("insights", []))
        recs_count = len(insight_report.get("recommendations", []))

        logger.info("Insight engine rerun for %s: %d insights, %d recs", session_id[:8], insights_count, recs_count)

        return {
            "session_id": session_id,
            "insights_count": insights_count,
            "recommendations_count": recs_count,
            "score": insight_report.get("summary", {}).get("overall_score"),
        }


async def main():
    """Entry point for: python -m app.services.insight_engine.runner <session_id>"""
    import sys
    if len(sys.argv) < 2:
        print("Usage: python -m app.services.insight_engine.runner <session_id>")
        sys.exit(1)
    result = await run_for_session(sys.argv[1])
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
