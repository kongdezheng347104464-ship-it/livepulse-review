"""UserService — single entry point for any user-state mutation that
must also propagate to caches / sessions / audit logs.

Design rationale (Day 3 P3, 2026-05-31):
  * Day 2 had the login endpoint call invalidate_user_cache(user.id)
    directly. With one caller that's fine; with M2's password change /
    role update / disable endpoints joining, that pattern spreads to
    8 files. By the time someone forgets, the cache becomes a bug source.
  * UserService centralizes the rule: any mutation goes through here,
    and here is where cache invalidation lives. Endpoints never touch
    the cache helper directly.

M1 has one method (record_login). M2 will add update_role / set_active /
change_password. Every new method MUST end by invalidating the cache.
"""
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.services.auth.current_user import invalidate_user_cache


class UserService:
    """Stateless helpers (could be free functions; class form lets
    callers DI / mock more easily and groups related methods)."""

    @staticmethod
    async def record_login(db: AsyncSession, user: User) -> None:
        """Update last_login_at + invalidate cache (in case role/is_active
        changed between password set and now, post-rotation use)."""
        user.last_login_at = datetime.now(timezone.utc)
        await db.commit()
        invalidate_user_cache(user.id)

    @staticmethod
    def invalidate_cache(user_id: str) -> None:
        """Public entry — endpoints should call this instead of importing
        invalidate_user_cache from current_user. Lets future debugging
        add a "log every cache drop" line in one place."""
        invalidate_user_cache(user_id)
