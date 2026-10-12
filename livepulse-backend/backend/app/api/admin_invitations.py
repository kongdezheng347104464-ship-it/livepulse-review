"""A4 admin invitation management (2026-06-01).

Admin generates codes and lists status. All endpoints require role=admin
(enforced by router-level `require_admin` dep). No 'user can list their
own invitations' endpoint — invitations are an admin-only resource in
M1.

Why a separate router (not extending /auth/*):
  * /auth/* is open to anonymous users (register, login). /admin/* is
    by definition admin-scoped. Mixing them invites accidental
    privilege leaks (e.g., forgetting `require_admin` on a new route).
  * Router-level dependency makes the privilege wall structural —
    every endpoint under /admin/* is admin-only by default, exceptions
    must be explicit.
"""
import logging
import secrets
import string
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Invitation, User
from app.models.schemas.invitation import (
    CreateInvitationRequest, InvitationItem, InvitationListResponse,
)
from app.services.auth.current_user import UserContext, require_admin

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin)],  # router-wide admin gate
)
logger = logging.getLogger(__name__)

# 16-char [a-zA-Z0-9] — matches the entropy used during register validation
# (A1 design: 62^16 ≈ 4.7×10^28, brute-force infeasible even with no rate
# limit). Avoids ambiguous chars like 0/O 1/l only when humans must type
# them — current admin-paste flow doesn't need that.
_CODE_ALPHABET = string.ascii_letters + string.digits
_CODE_LEN = 16


def _generate_code() -> str:
    """Cryptographically random 16-char code. secrets.choice is the
    standard CSPRNG-backed picker — never use random.choice for tokens."""
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LEN))


def _compute_status(
    used_by_user_id: str | None,
    expires_at: datetime | None,
    now: datetime,
) -> str:
    if used_by_user_id is not None:
        return "used"
    if expires_at is not None and expires_at < now:
        return "expired"
    return "unused"


@router.post("/invitations", response_model=InvitationItem)
async def create_invitation(
    body: CreateInvitationRequest,
    db: AsyncSession = Depends(get_db),
    ctx: UserContext = Depends(require_admin),
):
    """Generate a new invitation code. Returns the code in the response
    so admin can copy-paste it into a message / email to the invitee.

    Notes:
      * code is the PRIMARY KEY of invitations table; collision
        probability with 16-char random is negligible (we don't retry).
      * expires_at is computed server-side from expires_in_days to
        prevent clock-skew bugs (client setting absolute datetime in
        a different TZ is a recurring source of "code expires too early").
    """
    now = datetime.now(timezone.utc)
    expires_at = None
    if body.expires_in_days is not None:
        expires_at = now + timedelta(days=body.expires_in_days)

    inv = Invitation(
        code=_generate_code(),
        created_by=ctx.id,
        created_at=now,
        expires_at=expires_at,
    )
    db.add(inv)
    await db.commit()

    logger.info(
        "[admin.invitation] created code=%s by admin=%s expires=%s",
        inv.code[:4] + "…", ctx.id[:8], expires_at,
    )

    return InvitationItem(
        code=inv.code,
        created_at=now,
        used_at=None,
        used_by_email=None,
        expires_at=expires_at,
        status="unused",
    )


@router.get("/invitations", response_model=InvitationListResponse)
async def list_invitations(
    status_filter: str = Query(
        default="all",
        alias="status",
        description="Filter by invitation status: all | unused | used | expired",
    ),
    db: AsyncSession = Depends(get_db),
):
    """List all invitations admin can see. Returns used_by_email by
    joining users (so admin sees who redeemed each code, not opaque
    UUIDs)."""
    if status_filter not in ("all", "unused", "used", "expired"):
        raise HTTPException(status_code=422, detail="invalid status filter")

    # Outer join — invitations with NULL used_by_user_id (unused) must
    # still appear in the list. Inner join would silently drop them.
    stmt = (
        select(
            Invitation.code,
            Invitation.created_at,
            Invitation.used_at,
            Invitation.used_by_user_id,
            Invitation.expires_at,
            User.email.label("used_by_email"),
        )
        .outerjoin(User, User.id == Invitation.used_by_user_id)
        .order_by(Invitation.created_at.desc())
    )
    rows = (await db.execute(stmt)).all()

    now = datetime.now(timezone.utc)
    items: list[InvitationItem] = []
    for r in rows:
        status = _compute_status(r.used_by_user_id, r.expires_at, now)
        if status_filter != "all" and status != status_filter:
            continue
        items.append(InvitationItem(
            code=r.code,
            created_at=r.created_at,
            used_at=r.used_at,
            used_by_email=r.used_by_email,
            expires_at=r.expires_at,
            status=status,
        ))

    return InvitationListResponse(items=items, total=len(items))
