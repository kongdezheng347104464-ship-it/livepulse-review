"""Admin user seed hook (A1 2026-05-31).

Idempotent: runs at backend startup, checks for any role='admin' user,
creates one from env if absent. Skip silently if env vars empty (e.g.,
restoring from snapshot that already has admin).

Why a startup hook (vs alembic seed):
  * Alembic migrations should be pure schema — data seeding from env
    is a runtime concern (env not available in alembic env.py context
    without coupling)
  * Idempotent design means re-runs after secret rotation just hash a
    new password and keep going

Why we read env directly (vs settings):
  * Avoids circular import during boot
  * Admin password change should NOT require backend restart for the
    env var read; the seed only runs once anyway
"""
import logging

from sqlalchemy import select

from app.config import get_settings
from app.database import async_session_factory
from app.models import User
from app.services.auth.password import hash_password

logger = logging.getLogger(__name__)


async def ensure_admin_seeded() -> None:
    """Create admin user from settings if none exists. No-op otherwise."""
    settings = get_settings()
    email = (settings.admin_email or "").strip().lower()
    password = settings.admin_password or ""
    if not email or not password:
        logger.info("[admin-seed] ADMIN_EMAIL or ADMIN_PASSWORD empty — skipped")
        return

    async with async_session_factory() as db:
        existing = (await db.execute(
            select(User).where(User.role == "admin")
        )).scalar_one_or_none()
        if existing:
            logger.info("[admin-seed] admin user already present (%s) — skipped",
                        existing.email)
            return

        # Also check for an existing row at the same email — would collide
        # with our UNIQUE LOWER(email) index. Promote it to admin instead
        # of failing.
        same_email = (await db.execute(
            select(User).where(User.email == email)
        )).scalar_one_or_none()
        if same_email:
            same_email.role = "admin"
            await db.commit()
            logger.info("[admin-seed] promoted existing user %s to admin", email)
            return

        user = User(
            email=email,
            hashed_password=hash_password(password),
            role="admin",
            is_active=True,
        )
        db.add(user)
        await db.commit()
        logger.info("[admin-seed] created admin user %s", email)
