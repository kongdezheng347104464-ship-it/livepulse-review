"""A1 auth endpoints (2026-05-31).

POST /auth/register — email + password + invitation_code → new user + JWT
POST /auth/login    — email + password → JWT
GET  /auth/me       — Bearer JWT → curated user profile

Hardening levers DEFERRED to Day 3:
  * slowapi rate limit per IP on register + login
  * X-API-Key admin bypass (separate middleware)

Why not in this file:
  * Rate limit is a cross-cutting concern; mixing it into route handlers
    makes the auth logic harder to unit test.
  * Day 2 = correctness; Day 3 = hardening. Avoid mixing them in one PR.
"""
import hashlib
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func as sa_func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.middleware.rate_limit import limiter
from app.models import Invitation, User
from app.models.schemas.auth import (
    LoginRequest, RegisterRequest, TokenResponse, UserMeResponse,
)
from app.services.auth.current_user import (
    CachedUser, get_current_user_full,
)
from app.services.auth.jwt_service import JWT_TTL_DAYS, issue_token
from app.services.auth.password import (
    PasswordPolicyError, hash_password, validate_password, verify_password,
)
from app.services.user_service import UserService

router = APIRouter(prefix="/auth", tags=["auth"])
_audit_logger = logging.getLogger("app.auth_audit")


def _email_hash(email: str) -> str:
    """First 8 hex chars of SHA-256(email). Lets us group attempts
    by-email in logs without recording the raw PII."""
    return hashlib.sha256((email or "").lower().encode()).hexdigest()[:8]


# -- helpers -----------------------------------------------------------

def _normalize_email(email: str) -> str:
    """Lowercase + strip. Apply at every entry point so UNIQUE LOWER(email)
    index can't be bypassed by case variation."""
    return (email or "").strip().lower()


async def _consume_invitation(
    db: AsyncSession, code: str, new_user_id: str,
    request_ip: str = "?", email_for_audit: str = "?",
) -> None:
    """Validate + mark invitation as used. Raises HTTPException(422) on
    any failure with intentionally generic messages so attackers can't
    distinguish "code doesn't exist" from "code already used".

    Audit log records (IP + email_hash + code_prefix + reason) — never
    raw email or full code, so log files can't be exfiltrated and used
    to enumerate valid codes/emails.
    """
    def _audit(reason: str):
        _audit_logger.warning(
            "[invitation_fail] ip=%s email_hash=%s code_prefix=%s reason=%s",
            request_ip, _email_hash(email_for_audit),
            (code[:4] + "…") if code else "?", reason,
        )

    inv = (await db.execute(
        select(Invitation).where(Invitation.code == code)
    )).scalar_one_or_none()
    if inv is None:
        _audit("not_found")
        raise HTTPException(status_code=422, detail="邀请码无效")
    if inv.used_by_user_id is not None:
        _audit("already_used")
        raise HTTPException(status_code=422, detail="邀请码无效")
    now = datetime.now(timezone.utc)
    if inv.expires_at is not None and inv.expires_at < now:
        _audit("expired")
        raise HTTPException(status_code=422, detail="邀请码无效")
    inv.used_by_user_id = new_user_id
    inv.used_at = now


# -- endpoints ---------------------------------------------------------

@router.post("/register", response_model=TokenResponse)
@limiter.limit(lambda: get_settings().auth_rate_limit)
async def register(
    request: Request,            # required by slowapi key_func
    body: RegisterRequest,
    db: AsyncSession = Depends(get_db),
):
    """Create a new user. role is server-forced to 'user' — any client
    attempt to set 'admin' via body is silently ignored (the schema
    doesn't even accept a role field; this is defense in depth)."""
    email = _normalize_email(body.email)
    ip = request.client.host if request.client else "?"

    # Password policy first — cheap check, fails fast on bad input.
    try:
        validate_password(body.password)
    except PasswordPolicyError as e:
        raise HTTPException(status_code=422, detail=str(e))

    # Check duplicate email (case-insensitive via LOWER(email) match).
    existing = (await db.execute(
        select(User).where(sa_func.lower(User.email) == email)
    )).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail="邮箱已注册")

    # Atomic: create user + consume invitation in the same transaction.
    # If invitation consume fails, the user insert rolls back via the
    # SQLAlchemy session's transaction guard.
    user = User(
        email=email,
        hashed_password=hash_password(body.password),
        role="user",  # FORCED — never trust client
    )
    db.add(user)
    await db.flush()  # generate user.id for invitation FK

    await _consume_invitation(
        db, body.invitation_code, user.id,
        request_ip=ip, email_for_audit=email,
    )

    # Record initial login + commit the whole (user + invitation +
    # last_login) atomically via the same UserService entry point as
    # the login endpoint — symmetry simplifies future cache audits.
    await UserService.record_login(db, user)

    token = issue_token(user.id)
    return TokenResponse(
        access_token=token,
        expires_in=JWT_TTL_DAYS * 86400,
    )


@router.post("/login", response_model=TokenResponse)
@limiter.limit(lambda: get_settings().auth_rate_limit)
async def login(
    request: Request,            # required by slowapi key_func
    body: LoginRequest,
    db: AsyncSession = Depends(get_db),
):
    """Email + password → JWT. Case-insensitive email match.

    Known timing-attack gap (documented in project_a1_auth.md): when the
    email doesn't exist we return 401 immediately, whereas a wrong-
    password attempt takes ~100ms (argon2). Internal-beta scope accepts
    this gap; A3 production hardening must add a dummy-hash compare to
    equalize timing.
    """
    email = _normalize_email(body.email)
    user = (await db.execute(
        select(User).where(sa_func.lower(User.email) == email)
    )).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=401, detail="邮箱或密码错误")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="账号已停用")
    if not verify_password(body.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="邮箱或密码错误")

    await UserService.record_login(db, user)

    token = issue_token(user.id)
    return TokenResponse(
        access_token=token,
        expires_in=JWT_TTL_DAYS * 86400,
    )


@router.get("/me", response_model=UserMeResponse)
async def me(cached: CachedUser = Depends(get_current_user_full)):
    """Echo curated profile. Frontend boot path uses this to verify the
    stored token is still valid (else redirect to /login).

    Uses get_current_user_full (escape hatch) because we genuinely need
    email + is_active here. Every other endpoint must use the narrow
    get_current_user → UserContext."""
    return UserMeResponse(
        id=cached.id,
        email=cached.email,
        role=cached.role,
        is_active=cached.is_active,
    )
