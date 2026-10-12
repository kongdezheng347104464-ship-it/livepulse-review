"""Rate limiter setup (Day 3 P2, 2026-05-31 — Redis swap 2026-06-03 A6).

We use slowapi (the FastAPI-friendly wrapper around limits/limits-storage).

Storage backend selection (A6 Celery rollout):
  * USE_CELERY=true  → Redis storage. Required once multiple uvicorn workers
    exist, because in-memory limiter is per-process and 4 workers × 5/min =
    20/min real limit (= no real limit).
  * USE_CELERY=false → in-memory. Single-process dev / single-worker prod.
    Zero infra requirement.

The flag is reused (vs adding USE_REDIS) because both Celery and the
distributed rate limiter need the same Redis instance — toggling them
together is the right coupling for M1.

Why apply only to /auth/register + /auth/login:
  * These are the brute-force attack surface (email enumeration, password
    spraying, invitation code guessing).
  * Business endpoints (/sessions/*, /upload/*) are user-authenticated;
    abuse there is per-account not per-IP, and rate limit by IP would
    punish legit users sharing NAT.

5/minute per IP is conservative — a real human registering or logging in
hits it < 1/min. Distributed (botnet) attacks bypass per-IP; that's a
WAF / Cloudflare problem, not ours for M1.
"""
import logging

from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import get_settings

_logger = logging.getLogger(__name__)


def _build_limiter() -> Limiter:
    """Construct a Limiter with the right storage backend.

    Redis when USE_CELERY=true (multi-process visibility required).
    Memory otherwise (single-process dev or single-worker prod).
    """
    settings = get_settings()
    if settings.use_celery:
        _logger.info(
            "[rate-limit] using redis storage (%s) — cross-worker enforcement",
            settings.redis_url,
        )
        return Limiter(
            key_func=get_remote_address,
            storage_uri=settings.redis_url,
        )
    _logger.info("[rate-limit] using memory storage — single-process dev / single-worker prod")
    return Limiter(key_func=get_remote_address)


# get_remote_address reads request.client.host. Behind nginx we'll need
# to switch to a function that reads X-Forwarded-For; the swap is a
# one-line change in A3 deployment.
limiter = _build_limiter()
