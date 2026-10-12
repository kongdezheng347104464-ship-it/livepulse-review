"""Anchor management API.

GET /anchors/                      — list anchors with session counts (for dropdown)
POST /anchors/                     — explicitly create a new anchor
GET /anchors/{id}                  — single anchor detail
GET /anchors/{id}/trend            — B4 Dashboard: cross-session trend time series
GET /anchors/{id}/top-questions    — B4 Dashboard: top viewer questions ranked by recurrence

Upload-time attach/create is handled in upload.py via separate query params
to prevent silent identity fragmentation (typo → silently forked anchor).
"""
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.auth.current_user import UserContext, get_admin_or_user
from app.models.anchor import Anchor
from app.models.danmaku import Danmaku
from app.models.session import LiveSession
from app.models.source_type import SourceType
from app.models.viewer_snapshot import ViewerSnapshot
from app.services.anchor.canonicalize import canonicalize_anchor_name
from app.services.anchor.question_extractor import extract_top_questions
from app.services.ownership import apply_owner_scope, get_owned_anchor

_BASELINE_CONFIDENCE_THRESHOLD = 0.6

# Minimum session duration (seconds) to participate in the cross-session
# trend chart. Scoring v5 is calibrated for normal-length live sessions
# (30-120 min); rates like "closing per hour" and "prompts per minute" are
# wildly inflated on short demo/test clips (e.g. a 5-min clip with 3
# closings extrapolates to 36/hour, instantly hitting the 100-cap).
#
# Production target: 15 min (one full "上品 → 讲解 → 逼单" cycle).
# Currently 10 min temporarily so the M1 demo dataset (max ~12 min clips)
# can populate the chart. Bump back to 15 once real long-form recordings
# start landing.
_MIN_TREND_DURATION_SEC = 10 * 60

router = APIRouter(prefix="/anchors", tags=["anchors"])


class CreateAnchorBody(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    platform: str = Field(default="douyin", max_length=20)


@router.get("/")
async def list_anchors(
    db: AsyncSession = Depends(get_db),
    ctx: UserContext = Depends(get_admin_or_user),
):
    """List anchors owned by ctx (or all anchors if admin) with session counts.

    Includes anchors with zero sessions so the user can attach to a
    freshly-created anchor before any upload exists.
    """
    base = (
        select(
            Anchor.id, Anchor.name, Anchor.canonical_name, Anchor.platform,
            Anchor.created_at,
            func.count(LiveSession.id).label("session_count"),
            func.max(LiveSession.start_time).label("last_session_at"),
        )
        .outerjoin(LiveSession, LiveSession.anchor_id == Anchor.id)
        .group_by(Anchor.id, Anchor.name, Anchor.canonical_name, Anchor.platform, Anchor.created_at)
        .order_by(func.count(LiveSession.id).desc(), Anchor.name)
    )
    rows = (await db.execute(apply_owner_scope(base, Anchor, ctx))).all()

    items = [
        {
            "id": r.id,
            "name": r.name,
            "canonical_name": r.canonical_name,
            "platform": r.platform,
            "session_count": r.session_count,
            "last_session_at": r.last_session_at.isoformat() if r.last_session_at else None,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]
    return {"code": 0, "data": {"items": items, "total": len(items)}, "message": "ok"}


@router.post("/")
async def create_anchor(
    body: CreateAnchorBody,
    db: AsyncSession = Depends(get_db),
    ctx: UserContext = Depends(get_admin_or_user),
):
    """Create a new anchor scoped to the current user.

    Returns 409 if **this user** already has an anchor with the same
    canonical_name + platform — they probably meant to attach to that one
    instead of creating a duplicate. Other users with the same name are
    independent rows (A2 per-user anchor design).
    """
    canonical = canonicalize_anchor_name(body.name)
    if not canonical:
        raise HTTPException(status_code=400, detail="name canonicalizes to empty string")

    existing = (await db.execute(
        select(Anchor).where(
            Anchor.canonical_name == canonical,
            Anchor.platform == body.platform,
            Anchor.owner_user_id == ctx.id,
        )
    )).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "anchor with same canonical name exists",
                "existing_id": existing.id,
                "existing_name": existing.name,
                "canonical_name": canonical,
                "hint": "use the existing anchor instead of creating a duplicate",
            },
        )

    anchor = Anchor(
        name=body.name.strip(),
        canonical_name=canonical,
        platform=body.platform,
        owner_user_id=ctx.id,
    )
    db.add(anchor)
    await db.commit()
    return {
        "code": 0,
        "data": {
            "id": anchor.id,
            "name": anchor.name,
            "canonical_name": anchor.canonical_name,
            "platform": anchor.platform,
        },
        "message": "created",
    }


@router.get("/{anchor_id}/trend")
async def get_anchor_trend(
    anchor_id: str,
    db: AsyncSession = Depends(get_db),
    ctx: UserContext = Depends(get_admin_or_user),
):
    """B4 Dashboard — cross-session trend time series for one anchor.

    Returns every analyzed session for this anchor in chronological order.
    Each item carries scores, engagement metrics, like activity count, and a
    human-readable label.  The label is generated here so the frontend never
    has to reason about same-day collisions.

    `is_baseline_eligible` mirrors the existing baseline-pool gate used by
    HistoricalComparisonCard, so the frontend can render dotted/grey points
    without re-implementing the eligibility logic.
    """
    anchor = await get_owned_anchor(anchor_id, ctx, db)

    # Trend chart filters:
    #   1. composite_score > 0  — exclude analysis runs that produced no
    #      usable insight (data completeness too low to score). Drawing them
    #      as a dive to 0 creates the false visual of a "crash" on trend.
    #   2. duration_sec >= _MIN_TREND_DURATION_SEC  — exclude short clips
    #      where per-minute / per-hour rates inflate against scoring v5's
    #      normal-length calibration (see constant comment above).
    sessions_rows = (await db.execute(
        select(LiveSession)
        .where(
            LiveSession.anchor_id == anchor_id,
            LiveSession.status == "analyzed",
            LiveSession.composite_score.is_not(None),
            LiveSession.composite_score > 0,
            LiveSession.duration_sec.is_not(None),
            LiveSession.duration_sec >= _MIN_TREND_DURATION_SEC,
        )
        .order_by(LiveSession.start_time.asc())
    )).scalars().all()

    total_sessions = len(sessions_rows)

    if total_sessions == 0:
        return {
            "code": 0,
            "data": {
                "anchor_name": anchor.name,
                "total_sessions": 0,
                "items": [],
                "min_duration_min": _MIN_TREND_DURATION_SEC // 60,
            },
            "message": "ok",
        }

    session_ids = [s.id for s in sessions_rows]

    # B4 fix 2026-06-03: switch like data source.
    #   OLD: COUNT of "X 为主播点赞了" SYSTEM events in danmakus.
    #        These are individual per-user like events captured in the
    #        comment stream. 抖音 frequently throttles / collapses these,
    #        and OBS scrollback misses most. Result was systematic
    #        under-counting (1h28m session with 11.1万 真实点赞 → 0 events).
    #
    #   NEW: MAX(like_count) from viewer_snapshots, which OCRs the
    #        cumulative "本场点赞 N 万" badge displayed on screen — the
    #        platform's own aggregate, ground truth.
    #
    # Legacy events kept as a side channel (`like_event_count`) for
    # diagnostics during the migration window; will be removed in M2 once
    # the new path has ≥10 sessions of stable coverage. Frontend prefers
    # `like_count` and falls back to "—" (NOT to events) when NULL.
    like_event_rows = (await db.execute(
        select(Danmaku.session_id, func.count(Danmaku.id).label("cnt"))
        .where(
            Danmaku.session_id.in_(session_ids),
            Danmaku.source_type == SourceType.SYSTEM,
            Danmaku.content.like("%为主播点赞了%"),
        )
        .group_by(Danmaku.session_id)
    )).all()
    like_event_count_map: dict[str, int] = {r.session_id: r.cnt for r in like_event_rows}

    # New path: OCR'd cumulative 本场点赞 — one MAX per session.
    like_count_rows = (await db.execute(
        select(
            ViewerSnapshot.session_id,
            func.max(ViewerSnapshot.like_count).label("like_max"),
        )
        .where(
            ViewerSnapshot.session_id.in_(session_ids),
            ViewerSnapshot.like_count.is_not(None),
        )
        .group_by(ViewerSnapshot.session_id)
    )).all()
    like_count_map: dict[str, int | None] = {
        r.session_id: int(r.like_max) for r in like_count_rows
    }

    # Group sessions by calendar date to detect same-day collisions for
    # label generation.  Use start_time.date() as the key; sessions without
    # a start_time get a sentinel None bucket.
    day_buckets: dict = defaultdict(list)
    for s in sessions_rows:
        day_key = s.start_time.date() if s.start_time else None
        day_buckets[day_key].append(s.id)

    # Map session_id → ordinal within its day (1-based; only set when
    # the day has more than one session).
    session_day_ordinal: dict[str, int | None] = {}
    for day_key, ids in day_buckets.items():
        if len(ids) > 1:
            for idx, sid in enumerate(ids, start=1):
                session_day_ordinal[sid] = idx
        else:
            session_day_ordinal[ids[0]] = None

    items = []
    for s in sessions_rows:
        day_key = s.start_time.date() if s.start_time else None
        day_str = day_key.strftime("%m-%d") if day_key else "未知"
        ordinal = session_day_ordinal.get(s.id)
        label = f"{day_str} 第{ordinal}场" if ordinal is not None else day_str

        summary = s.analysis_summary or {}
        scores = summary.get("scores") or {}
        nm = summary.get("normalized_metrics") or {}
        dc = summary.get("data_completeness") or {}

        # Reuse the same gate as _compute_historical_comparison so the two
        # views stay consistent without duplicating the rule.
        is_baseline_eligible = bool(
            dc.get("valid_for_baseline")
            and (dc.get("analysis_confidence") or 0) >= _BASELINE_CONFIDENCE_THRESHOLD
        )

        items.append({
            "label": label,
            "timestamp": s.start_time.isoformat() if s.start_time else None,
            "composite": round(float(s.composite_score or 0), 1),
            "engagement": round(float(scores.get("engagement") or 0), 1),
            "conversion": round(float(scores.get("conversion") or 0), 1),
            "content": round(float(scores.get("content") or 0), 1),
            "engaged_rate": nm.get("engaged_audience_rate"),
            "unique_speakers": nm.get("unique_speakers"),
            "avg_viewer": nm.get("avg_viewer_count"),
            # NEW: cumulative 本场点赞 from OCR (None when legacy session
            # has no like_count snapshots — frontend treats as "—" break).
            "like_count": like_count_map.get(s.id),
            # OLD: per-user like events (kept during migration window for
            # diagnostics). M2 will remove once new path proves stable.
            "like_event_count": like_event_count_map.get(s.id, 0),
            "is_baseline_eligible": is_baseline_eligible,
        })

    return {
        "code": 0,
        "data": {
            "anchor_name": anchor.name,
            "total_sessions": total_sessions,
            "items": items,
            "min_duration_min": _MIN_TREND_DURATION_SEC // 60,
        },
        "message": "ok",
    }


@router.get("/{anchor_id}/top-questions")
async def get_anchor_top_questions(
    anchor_id: str,
    limit: int = 10,
    db: AsyncSession = Depends(get_db),
    ctx: UserContext = Depends(get_admin_or_user),
):
    """B4 Dashboard — viewer questions ranked by cross-session recurrence.

    Returns the top recurring questions asked in this anchor's live sessions,
    ranked by how many distinct sessions the question appeared in (session_count),
    then session_ratio, then raw message_count.

    Text normalization strips filler prefixes and punctuation so that
    "请问主播多少钱？" and "多少钱" are counted together.  Substring merging
    further collapses verbose forms into their shorter canonical core.
    """
    anchor = await get_owned_anchor(anchor_id, ctx, db)

    session_ids_rows = (await db.execute(
        select(LiveSession.id)
        .where(
            LiveSession.anchor_id == anchor_id,
            LiveSession.status == "analyzed",
        )
    )).scalars().all()

    total_sessions = len(session_ids_rows)

    if total_sessions == 0:
        return {
            "code": 0,
            "data": {"anchor_name": anchor.name, "total_sessions": 0, "items": []},
            "message": "ok",
        }

    danmaku_rows = (await db.execute(
        select(Danmaku.session_id, Danmaku.content)
        .where(
            Danmaku.session_id.in_(session_ids_rows),
            Danmaku.source_type == SourceType.SPEECH,
            Danmaku.content.isnot(None),
        )
    )).all()

    items = extract_top_questions(
        [(r.session_id, r.content) for r in danmaku_rows],
        total_sessions=total_sessions,
        limit=max(1, min(limit, 20)),
    )

    return {
        "code": 0,
        "data": {
            "anchor_name": anchor.name,
            "total_sessions": total_sessions,
            "items": items,
        },
        "message": "ok",
    }


class UpdateAnchorBody(BaseModel):
    name: str = Field(min_length=1, max_length=100)


@router.patch("/{anchor_id}")
async def update_anchor(
    anchor_id: str,
    body: UpdateAnchorBody,
    db: AsyncSession = Depends(get_db),
    ctx: UserContext = Depends(get_admin_or_user),
):
    """Rename an anchor. Re-canonicalizes the name and checks for duplicates
    within this user's anchors (same canonical + platform = 409)."""
    anchor = await get_owned_anchor(anchor_id, ctx, db)
    new_name = body.name.strip()
    canonical = canonicalize_anchor_name(new_name)
    if not canonical:
        raise HTTPException(status_code=400, detail="name canonicalizes to empty string")

    if canonical != anchor.canonical_name:
        conflict = (await db.execute(
            select(Anchor).where(
                Anchor.canonical_name == canonical,
                Anchor.platform == anchor.platform,
                Anchor.owner_user_id == ctx.id,
                Anchor.id != anchor_id,
            )
        )).scalar_one_or_none()
        if conflict:
            raise HTTPException(status_code=409, detail="该主播名与已有主播重名")

    anchor.name = new_name
    anchor.canonical_name = canonical
    await db.commit()
    return {
        "code": 0,
        "data": {"id": anchor.id, "name": anchor.name},
        "message": "ok",
    }


@router.delete("/{anchor_id}")
async def delete_anchor(
    anchor_id: str,
    db: AsyncSession = Depends(get_db),
    ctx: UserContext = Depends(get_admin_or_user),
):
    """Delete an anchor and all associated sessions (CASCADE at DB level).

    Returns the count of deleted sessions so the frontend can show a
    meaningful confirmation result. DB-level CASCADE handles danmakus,
    script_segments, viewer_snapshots, session_notes, session_analyses,
    pipeline_steps — no manual cleanup needed.
    """
    anchor = await get_owned_anchor(anchor_id, ctx, db)
    session_count = (await db.execute(
        select(func.count(LiveSession.id)).where(LiveSession.anchor_id == anchor_id)
    )).scalar() or 0

    await db.delete(anchor)
    await db.commit()
    return {
        "code": 0,
        "data": {"deleted_sessions": session_count},
        "message": "deleted",
    }


@router.get("/{anchor_id}")
async def get_anchor(
    anchor_id: str,
    db: AsyncSession = Depends(get_db),
    ctx: UserContext = Depends(get_admin_or_user),
):
    anchor = await get_owned_anchor(anchor_id, ctx, db)
    session_count = (await db.execute(
        select(func.count(LiveSession.id)).where(LiveSession.anchor_id == anchor_id)
    )).scalar() or 0
    return {
        "code": 0,
        "data": {
            "id": anchor.id,
            "name": anchor.name,
            "canonical_name": anchor.canonical_name,
            "platform": anchor.platform,
            "avatar_url": anchor.avatar_url,
            "followers": anchor.followers,
            "session_count": session_count,
            "created_at": anchor.created_at.isoformat() if anchor.created_at else None,
        },
        "message": "ok",
    }
