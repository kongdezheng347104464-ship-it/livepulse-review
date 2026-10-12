#!/usr/bin/env python3
"""Recompute insight engine for a session from existing raw data only.

Usage:
    cd backend && ../.venv/Scripts/python.exe scripts/rerun_insights.py <session_id>

Never touches ASR, OCR, video processing, or raw data tables.
"""
import sys
import os
import asyncio
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.insight_engine.runner import run_for_session


async def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/rerun_insights.py <session_id>")
        sys.exit(1)

    sid = sys.argv[1]
    print(f"Recomputing insights for {sid[:8]}...")
    result = await run_for_session(sid)

    if "error" in result:
        print(f"Error: {result['error']}")
        sys.exit(1)

    print(f"  Insights: {result['insights_count']}")
    print(f"  Recommendations: {result['recommendations_count']}")
    print(f"  Score: {result.get('score')}")
    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
