"""Integration test configuration — Windows + asyncpg event loop management.

The async_session_factory in database.py creates a module-level engine.
On Windows, pytest creates a new event loop per session, but the engine's
connection pool holds references to the import-time loop.

Fix: dispose the engine at session start and let it reconnect on the new loop.
"""
import asyncio
import sys
import pytest
import logging

logger = logging.getLogger(__name__)


@pytest.fixture(scope="session")
def event_loop():
    """Session-scoped event loop — reconnects the engine on the new loop."""
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # Dispose stale engine connections from import-time loop
    from app.database import engine
    loop.run_until_complete(engine.dispose())
    logger.info("Engine disposed, will reconnect on session event loop")

    yield loop
    loop.run_until_complete(engine.dispose())
    loop.close()
