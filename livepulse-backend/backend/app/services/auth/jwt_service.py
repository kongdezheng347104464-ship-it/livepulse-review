"""JWT issuance + verification (A1 2026-05-31).

Token payload is sub-only by design — see auth-design discussion. Putting
role/email in the JWT would create a 7-day "stale claim" window when
user state changes (e.g., admin demoted). Instead, middleware re-fetches
the user from DB (with a 5-second cache).

Claims:
  sub: user id (UUID string)
  iat: issued-at, unix seconds
  exp: expiry, unix seconds (M1: iat + 7 days)
  jti: random UUID per token — unused in M1, enables M2 Redis blacklist
       for precise per-token revocation (vs full-account via
       password_changed_at).

Secret:
  Loaded from settings.jwt_secret (env JWT_SECRET). MUST be at least
  32 bytes of entropy: `openssl rand -hex 32`. Empty/missing secret
  causes module to refuse to encode (no silently-vulnerable default).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import jwt as pyjwt

from app.config import get_settings


JWT_ALG = "HS256"
JWT_TTL_DAYS = 7  # M1 single-token, no refresh; users re-login weekly


class JWTConfigError(RuntimeError):
    """Raised when JWT_SECRET is missing or too short. Refuse to issue
    rather than fall back to a guessable default."""


class JWTInvalidError(ValueError):
    """Raised on signature mismatch, malformed token, expired token, etc.
    Callers should map to 401 — never leak the specific reason to clients
    (a "token expired" message is fine; an "invalid signature" is not)."""


def _get_secret() -> str:
    secret = (get_settings().jwt_secret or "").strip()
    if len(secret) < 32:
        raise JWTConfigError(
            "JWT_SECRET missing or too short (< 32 chars). "
            "Generate with: openssl rand -hex 32"
        )
    return secret


def issue_token(user_id: str) -> str:
    """Returns an encoded JWT for the given user. jti is fresh per call."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=JWT_TTL_DAYS)).timestamp()),
        "jti": str(uuid.uuid4()),
    }
    return pyjwt.encode(payload, _get_secret(), algorithm=JWT_ALG)


def decode_token(token: str) -> dict:
    """Returns the verified payload dict {sub, iat, exp, jti}. Raises
    JWTInvalidError on any failure. PyJWT validates iat/exp/signature
    automatically; we re-raise everything as a single error type so
    callers don't have to import PyJWT exception classes."""
    try:
        return pyjwt.decode(token, _get_secret(), algorithms=[JWT_ALG])
    except pyjwt.PyJWTError as e:
        raise JWTInvalidError(str(e)) from e
