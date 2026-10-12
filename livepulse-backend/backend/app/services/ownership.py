"""Ownership helpers — A2 Day 2 (2026-05-31).

Single source of truth for "does this user own this row" logic. Every
business endpoint that touches an owned model (LiveSession / Anchor /
SessionNote / SessionAnalysis) goes through one of these helpers,
NEVER through `db.get(Model, id)` directly.

Why centralized:
  * Admin bypass policy lives here once. To flip B1 → B2 (admin must
    pass `?as_admin=true` explicitly), edit this file alone.
  * Cross-tenant 404 behavior (vs 403) lives here once. Same.
  * Static audit (`tests/test_no_raw_owned_select`) can prove no
    business endpoint sneaks past — caught at CI, not in prod.

Why three helper shapes (not one):
  * `apply_owner_scope` for LIST / paginated SELECTs that return many rows
  * `get_owned_X` for detail / delete / update endpoints (single row, 404
    on miss-or-not-owned — same status to defeat enumeration)
  * `assert_owned_anchor` for write-path binding (upload + new anchor_id).
    Same implementation as get_owned_anchor, distinct name so reviewers
    spot "this is the gate that defends invariant #12" at a glance.

Helper contract:
  * For admin role: pass through. No filter, no role-check, no raise.
  * For user role: filter / verify against ctx.id.
  * Never returns None — raises HTTPException(404) so callers don't
    forget the if-None check.
"""
from typing import Type, TypeVar, Union

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from app.models.anchor import Anchor
from app.models.session import LiveSession
from app.models.session_analysis import SessionAnalysis
from app.models.session_note import SessionNote
from app.services.auth.current_user import UserContext


# Type union for the 4 owned models. Used as `model` arg to scope helper.
OwnedModel = Union[
    Type[LiveSession],
    Type[Anchor],
    Type[SessionNote],
    Type[SessionAnalysis],
]


def apply_owner_scope(
    stmt: Select, model: OwnedModel, ctx: UserContext,
) -> Select:
    """Return `stmt` with an owner filter appended, unless caller is admin.

    Use for LIST / paginated SELECTs:
        stmt = select(LiveSession).where(LiveSession.status == 'analyzed')
        stmt = apply_owner_scope(stmt, LiveSession, ctx)
        rows = (await db.execute(stmt)).scalars().all()

    Existing WHERE clauses on `stmt` are preserved — we append, never replace.
    """
    if ctx.role == "admin":
        return stmt
    return stmt.where(model.owner_user_id == ctx.id)


async def get_owned_session(
    session_id: str, ctx: UserContext, db: AsyncSession,
) -> LiveSession:
    """Fetch a single session, enforcing ownership.

    Raises HTTPException(404) on:
      * session_id doesn't exist
      * session exists but owned by a different user (and ctx is not admin)

    Both cases use 404 by design — telling the caller "exists but forbidden"
    would let an attacker enumerate valid session_ids. See ADR-0001.
    """
    row = (await db.execute(
        select(LiveSession).where(LiveSession.id == session_id)
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="session not found")
    if ctx.role != "admin" and row.owner_user_id != ctx.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="session not found")
    return row


async def get_owned_anchor(
    anchor_id: str, ctx: UserContext, db: AsyncSession,
) -> Anchor:
    """Fetch a single anchor, enforcing ownership. Same 404 semantics."""
    row = (await db.execute(
        select(Anchor).where(Anchor.id == anchor_id)
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="anchor not found")
    if ctx.role != "admin" and row.owner_user_id != ctx.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="anchor not found")
    return row


async def assert_owned_anchor(
    anchor_id: str, ctx: UserContext, db: AsyncSession,
) -> Anchor:
    """Write-path ownership gate for anchor binding.

    Identical to `get_owned_anchor` — distinct name documents intent.
    Used in upload.py when client passes `anchor_id`, to prevent
    invariant #12 (cross-owner anchor binding pollution). Code review
    sees `assert_owned_anchor` and knows: "this is the defense against
    user B polluting user A's dashboard via a known anchor_id".
    """
    return await get_owned_anchor(anchor_id, ctx, db)
