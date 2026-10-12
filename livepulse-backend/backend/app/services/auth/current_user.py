"""FastAPI dependency: resolve current user from `Authorization: Bearer <jwt>`.

Design contract (A1, see project_a1_auth.md):
  * JWT carries sub only — we look up role/is_active from DB on each
    request, NOT from token claims (avoids 7-day stale role window).
  * To keep DB load reasonable on hot endpoints (status polling), we
    cache a small struct per user_id with a short TTL (default 5s).
  * Cache + return types are dataclasses, NOT SQLAlchemy ORM objects —
    detached-object errors are a classic foot-gun.
  * Cache is module-level (per uvicorn worker process). When we scale
    to multi-worker we swap to Redis; M1 single-worker is fine.

Two return types — narrow vs full:
  * UserContext(id, role) is the DEFAULT for every endpoint. The
    deliberate narrowness forces every new route author to consider
    "do I really need email here?" rather than letting `ctx.email`
    leak into unrelated business logic (which would block A2 cleanup).
  * CachedUser(id, email, role, is_active) is reserved for endpoints
    that genuinely need the extra fields (/auth/me being the only one
    in M1).

Per 2026-05-31 owner feedback: enforce this discipline NOW, before
Day 3 attaches Depends(get_current_user) to dozens of endpoints.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from cachetools import TTLCache
from fastapi import Depends, Header, HTTPException, Security, status
from fastapi.security import APIKeyHeader
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.models import User
from app.services.auth.jwt_service import decode_token, JWTInvalidError

_admin_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

# Cached admin user id (seeded once at boot per admin_seed.py). Falls
# back to a fresh DB lookup if cache miss. Single value because M1 has
# exactly one admin row.
_admin_user_id: Optional[str] = None


@dataclass(frozen=True)
class UserContext:
    """Narrow user identity for the typical endpoint. Frozen so a
    handler can't mutate it and accidentally affect cached state."""
    id: str
    role: str


@dataclass(frozen=True)
class CachedUser:
    """Full cached snapshot. Internal to this module + /auth/me only.
    Other routes MUST use UserContext to avoid creeping coupling that
    would block A2 data-isolation refactors."""
    id: str
    email: str
    role: str
    is_active: bool


# Bounded LRU + TTL cache. maxsize prevents unbounded growth at large
# user counts; ttl prevents 7-day stale role drift. ttl=0 effectively
# disables the cache (dev mode); we honor that by short-circuiting in
# the helpers below — cachetools TTLCache itself doesn't accept ttl=0.
_CACHE_MAXSIZE = 1000
_user_cache: TTLCache[str, CachedUser] = TTLCache(
    maxsize=_CACHE_MAXSIZE,
    ttl=max(get_settings().user_cache_ttl_sec, 1),
)


def _cache_get(user_id: str) -> Optional[CachedUser]:
    """Pure read — no DB. Returns None if missing or expired."""
    return _user_cache.get(user_id)


def _cache_set(user_id: str, cached: CachedUser, ttl_sec: int) -> None:
    if ttl_sec <= 0:
        return  # disabled: dev wants every request to hit DB
    _user_cache[user_id] = cached


def invalidate_user_cache(user_id: str) -> None:
    """Drop a user's cache entry. INTERNAL — endpoints should call
    UserService.invalidate_cache instead so the discipline of
    "one place to look when cache misbehaves" holds. See
    app/services/user_service.py."""
    _user_cache.pop(user_id, None)


async def _load_user_from_db(db: AsyncSession, user_id: str) -> Optional[CachedUser]:
    user = (await db.execute(
        select(User).where(User.id == user_id)
    )).scalar_one_or_none()
    if user is None:
        return None
    return CachedUser(
        id=user.id, email=user.email,
        role=user.role, is_active=user.is_active,
    )


def _extract_bearer(authorization: str) -> str:
    """Parse `Authorization: Bearer <token>`. Raises 401 on any failure
    — including missing header, wrong scheme, empty token."""
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Authorization scheme",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = parts[1].strip()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Empty token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token


async def _resolve_cached_user(
    authorization: Optional[str], db: AsyncSession,
) -> CachedUser:
    """Shared core: parse Bearer → load (cache or DB) → enforce is_active.
    Caller-facing dependencies wrap this and project to the right shape."""
    token = _extract_bearer(authorization)
    try:
        payload = decode_token(token)
    except JWTInvalidError:
        # Don't leak the specific PyJWT error to client (e.g. signature
        # vs expiry — fingerprinting risk).
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing subject",
        )

    settings = get_settings()
    cached = _cache_get(user_id)
    if cached is None:
        cached = await _load_user_from_db(db, user_id)
        if cached is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found",
            )
        _cache_set(user_id, cached, settings.user_cache_ttl_sec)

    if not cached.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is disabled",
        )
    return cached


async def get_current_user(
    authorization: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db),
) -> UserContext:
    """DEFAULT dep — use for nearly every endpoint:
        ctx: UserContext = Depends(get_current_user)
    Exposes only (id, role) deliberately. If you find yourself needing
    email/is_active, ask whether the endpoint really should — odds are
    it shouldn't."""
    cached = await _resolve_cached_user(authorization, db)
    return UserContext(id=cached.id, role=cached.role)


async def get_current_user_full(
    authorization: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db),
) -> CachedUser:
    """ESCAPE HATCH — full snapshot for /auth/me only. Adding new
    callers to this should require explicit justification (consider
    whether your endpoint is overreaching)."""
    return await _resolve_cached_user(authorization, db)


# --- X-API-Key admin bypass (Day 3 P0-1, 2026-05-31) ----------------
#
# Precedence rule (locked):
#   1. If `X-API-Key` header present AND ALLOW_ADMIN_API_KEY=true AND
#      value matches settings.api_key → return UserContext(role='admin')
#      using the seeded admin's user.id.
#   2. Else fall through to Bearer JWT (standard get_current_user path).
#   3. Else 401.
#
# Why X-API-Key wins when BOTH headers are present: an operator who
# explicitly attaches their X-API-Key is saying "I want admin scope";
# letting JWT take priority would silently downgrade them to user scope,
# which is the wrong default for "emergency rescue" semantics.

async def _resolve_admin_user_id(db: AsyncSession) -> Optional[str]:
    """Find the seeded admin's user.id. Cached after first lookup."""
    global _admin_user_id
    if _admin_user_id:
        return _admin_user_id
    user = (await db.execute(
        select(User).where(User.role == "admin").limit(1)
    )).scalar_one_or_none()
    if user:
        _admin_user_id = user.id
    return _admin_user_id


async def get_admin_or_user(
    authorization: Optional[str] = Header(None),
    api_key: Optional[str] = Security(_admin_api_key_header),
    db: AsyncSession = Depends(get_db),
) -> UserContext:
    """Use this on every business endpoint that needs auth. Accepts
    either:
      * `X-API-Key: <env API_KEY>` → admin scope (when feature-flagged on)
      * `Authorization: Bearer <jwt>` → user scope from token's sub

    Both means X-API-Key wins."""
    settings = get_settings()

    # Path 1: X-API-Key admin bypass
    if settings.allow_admin_api_key and settings.api_key and api_key == settings.api_key:
        admin_id = await _resolve_admin_user_id(db)
        if admin_id is None:
            # Edge case: ALLOW_ADMIN_API_KEY is on but no admin row exists
            # yet (e.g., seed env vars weren't set on first boot). Refuse
            # rather than silently elevating an unseeded request to admin.
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Admin not seeded — set ADMIN_EMAIL/ADMIN_PASSWORD and restart",
            )
        return UserContext(id=admin_id, role="admin")

    # Path 2: Bearer JWT (standard user)
    cached = await _resolve_cached_user(authorization, db)
    return UserContext(id=cached.id, role=cached.role)


async def require_admin(
    ctx: UserContext = Depends(get_admin_or_user),
) -> UserContext:
    """Stricter dep — require role='admin'. Use for admin invitation
    management, cross-tenant operator endpoints, etc.

    Depends on `get_admin_or_user` (not `get_current_user`) so that
    both auth paths reach admin endpoints:
      * X-API-Key + ALLOW_ADMIN_API_KEY=true → admin scope → passes
      * Bearer JWT of an admin user → admin scope → passes
      * Bearer JWT of a normal user → 403 Admin only
      * No credentials → 401 (from get_admin_or_user)
    """
    if ctx.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin only",
        )
    return ctx
