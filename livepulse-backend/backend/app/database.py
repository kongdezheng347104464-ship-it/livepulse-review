from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from app.config import get_settings

settings = get_settings()


def _build_engine():
    return create_async_engine(
        settings.database_url,
        echo=settings.debug,
        # Sized for a single-tenant analyzer: API handles handful of QPS, the
        # heavy work is the ASR/OCR pipeline which holds at most one DB session
        # at a time. 10 idle connections were mostly wasted.
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
        pool_recycle=3600,   # PostgreSQL idle timeout: recycle connections before 1h
    )


engine = _build_engine()

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


def reset_engine_for_fork() -> None:
    """Rebuild the engine + session factory after fork().

    Required for Celery prefork worker processes — when MainProcess
    initializes the asyncpg pool and then forks, the child inherits dupe
    socket FDs that crash with "got Future attached to a different loop"
    on first use. Disposing the inherited engine (without closing the
    parent's still-valid sockets) and rebuilding gives the child a clean
    pool bound to its own event loop.

    Called from `worker_process_init` in app/tasks/pipeline.py.
    """
    global engine, async_session_factory
    # close=False so we don't yank the parent's still-live connections.
    # The forked sockets are just abandoned; OS reclaims on process exit.
    engine.sync_engine.dispose(close=False)
    engine = _build_engine()
    async_session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False,
    )


async def get_db() -> AsyncSession:
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
