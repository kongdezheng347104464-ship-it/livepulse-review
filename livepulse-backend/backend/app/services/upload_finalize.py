"""Shared finalize step for video uploads.

Both the single-shot upload (`/upload/video`) and the chunked upload
(`/upload/complete/{id}`) end the same way: the video object is already in
the main MinIO bucket, and now we must resolve the anchor (attach existing
vs create new), create the LiveSession row, commit, and trigger analysis.

Extracted so the **A2 ownership invariant #12** (cannot bind a session to
another user's anchor) and the per-owner anchor-collision policy live in
exactly one place — duplicating them across two upload paths is how those
guarantees quietly diverge.
"""
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.anchor import Anchor
from app.models.session import LiveSession
from app.services.anchor.canonicalize import canonicalize_anchor_name
from app.services.auth.current_user import UserContext
from app.services.ownership import assert_owned_anchor
from app.tasks import trigger_analysis

settings = get_settings()


def validate_anchor_binding(anchor_id: str | None, new_anchor_name: str | None) -> None:
    """Exactly one of attach (anchor_id) / create (new_anchor_name) must be
    given. Raises 400 otherwise. Call this early (e.g. at /upload/init) to
    fail fast before bytes are transferred.
    """
    if (anchor_id and new_anchor_name) or (not anchor_id and not new_anchor_name):
        raise HTTPException(
            status_code=400,
            detail="must provide exactly one of `anchor_id` (attach to existing) "
                   "or `new_anchor_name` (create new)",
        )


async def finalize_video_upload(
    *,
    object_name: str,
    anchor_id: str | None,
    new_anchor_name: str | None,
    title: str,
    filename: str | None,
    ctx: UserContext,
    db: AsyncSession,
) -> dict:
    """Resolve anchor → create session → commit → trigger analysis.

    Precondition: `object_name` is already stored in `settings.minio_bucket`.
    Returns the same data dict shape as the single-shot upload endpoint.
    """
    validate_anchor_binding(anchor_id, new_anchor_name)

    # Resolve anchor: explicit attach vs create. Either path produces an
    # anchor row that the session FKs into; neither silently forks identities.
    if anchor_id:
        # A2 invariant #12: cannot bind a session to another user's anchor.
        anchor = await assert_owned_anchor(anchor_id, ctx, db)
    else:
        canonical = canonicalize_anchor_name(new_anchor_name or "")
        if not canonical:
            raise HTTPException(status_code=400, detail="new_anchor_name canonicalizes to empty")
        # Refuse to insert a duplicate within THIS user's anchor space
        # (Anchor UNIQUE is per-owner since A2 migration 0008).
        collision = (await db.execute(
            select(Anchor).where(
                Anchor.canonical_name == canonical,
                Anchor.platform == "douyin",
                Anchor.owner_user_id == ctx.id,
            )
        )).scalar_one_or_none()
        if collision is not None:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "anchor with same canonical name already exists",
                    "existing_id": collision.id,
                    "existing_name": collision.name,
                    "hint": "attach to existing anchor via anchor_id instead",
                },
            )
        anchor = Anchor(
            name=(new_anchor_name or "").strip(),
            canonical_name=canonical,
            platform="douyin",
            owner_user_id=ctx.id,
        )
        db.add(anchor)
        await db.flush()

    # Sanitize the human-visible title — strip path separators, cap at the
    # column's 500-char bound. filename is user-controlled.
    safe_filename = (filename or "Untitled").replace("\\", "_").replace("/", "_")[:500]
    safe_title = (title or safe_filename or "Untitled")[:500]

    session = LiveSession(
        anchor_id=anchor.id,
        title=safe_title,
        master_video_path=f"{settings.minio_bucket}/{object_name}",
        start_time=datetime.now(timezone.utc),
        owner_user_id=ctx.id,
    )
    db.add(session)
    await db.flush()
    session_id = session.id
    status = session.status

    # Commit BEFORE spawning the analysis task: the pipeline opens its own
    # session_factory and may not see this row yet otherwise (phantom
    # "session not found" race).
    await db.commit()

    trigger_analysis(session_id)

    return {
        "session_id": session_id,
        "status": status,
        "anchor_name": anchor.name,
        "title": safe_title,
        "master_video_path": f"{settings.minio_bucket}/{object_name}",
    }
