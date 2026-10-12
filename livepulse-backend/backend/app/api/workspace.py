"""Replay Timeline Workspace API.

A separate router from sessions.py because the Workspace's data shape
is fundamentally different: it's a *single* per-session bundle optimised
for a long-lived single-page workspace, not the granular per-resource
endpoints sessions.py serves. Keeping it in its own module also makes
the v1.5 split cleaner (notes editing, timeline zoom data, etc. all
land here without crowding the older sessions API surface).

Endpoints:
  GET  /sessions/{id}/timeline             — one-shot bundle for Workspace open
  GET  /sessions/{id}/transcript           — windowed ASR segments (lazy load)
  POST /sessions/{id}/notes                — create a pinned note
  GET  /sessions/{id}/notes                — list session notes (ordered)
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.services.auth.current_user import UserContext, get_admin_or_user
from app.services.ownership import apply_owner_scope, get_owned_session
from app.services.video_processor import FFMPEG, VideoProcessError, _run_ffmpeg
from app.models.anchor import Anchor
from app.models.script_segment import ScriptSegment
from app.models.session import LiveSession
from app.models.session_analysis import SessionAnalysis
from app.models.session_note import SessionNote
from app.models.viewer_snapshot import ViewerSnapshot
from app.services.llm.range_analysis import (
    AnalysisContext,
    AnalysisSegment,
    LLMCallFailed,
    LLMNotConfigured,
    analyze_range,
)

router = APIRouter(prefix="/sessions", tags=["workspace"])
settings = get_settings()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Presigned URL TTL. 4 hours covers the worst realistic "review session left
# open" case without giving an effectively-permanent leak surface. If the
# user really does leave a tab open longer, a page refresh re-signs.
_PRESIGN_TTL = timedelta(hours=4)


def _presigned_video_url(video_path: str | None, public: bool = True) -> str | None:
    """Sign a Range-capable URL for the session's video.

    video_path on the session row is stored as "bucket/object" — split it
    and hand both halves to minio.presigned_get_object. Returns None if
    the session has no video (legacy state or pre-pipeline rows).

    public=True → URL host = minio_public_endpoint(给客户端/手机,外部可达);
    public=False → host = 内部 minio_endpoint(服务端自己用,如 ffmpeg 取源)。
    """
    if not video_path or "/" not in video_path:
        return None
    bucket, object_name = video_path.split("/", 1)
    return _minio_client(public=public).presigned_get_object(
        bucket, object_name, expires=_PRESIGN_TTL
    )


def _build_insight_markers(segments: list[ScriptSegment]) -> list[dict]:
    """Synthesize timeline markers from ScriptSegment scores.

    The MVP doesn't have per-timestamp insight rows yet — insight_report
    only carries session-level narratives. To populate the timeline's
    ▲ markers without inventing new data, we take the top 10% of
    segments by score (min 3, max 20) and surface them as "high-value
    moments". The frontend can then render them with hover tooltips
    showing the segment text + script_type + score.

    The cap (max 20) keeps the timeline visually navigable; a 3h session
    with 3500 segments would otherwise produce 350 markers — visual
    noise, not signal.

    When the upcoming v1.5 work introduces real per-timestamp insights
    (viewer spike, engagement burst), this helper is replaced — the
    frontend contract for the marker dict stays stable.
    """
    if not segments:
        return []

    scored = [s for s in segments if s.score is not None]
    if not scored:
        return []

    scored.sort(key=lambda s: float(s.score or 0), reverse=True)
    take = max(3, min(20, len(scored) // 10))
    top = scored[:take]
    # Re-sort by time so the timeline can lay them out left-to-right
    # without any post-processing on the client.
    top.sort(key=lambda s: s.start_sec)

    return [
        {
            "start_sec": s.start_sec,
            "end_sec": s.end_sec,
            "script_type": s.script_type or "unknown",
            "score": float(s.score) if s.score is not None else None,
            # Truncate to a tooltip-sized preview — full text is in the
            # transcript window the marker click already navigates to.
            "preview": (s.text or "")[:80],
        }
        for s in top
    ]


# ---------------------------------------------------------------------------
# GET /sessions/{id}/timeline — one-shot Workspace bundle
# ---------------------------------------------------------------------------


@router.get("/{session_id}/timeline")
async def get_workspace_timeline(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    ctx: UserContext = Depends(get_admin_or_user),
):
    """Return the bundle needed to bootstrap the Replay Workspace.

    Deliberately does NOT include the full transcript: a 3h session has
    ~3500 segments and only a ±15s window is ever visible. Workspace
    fetches windowed transcript lazily via GET /transcript as the user
    scrubs. This endpoint stays under 100 KB for typical sessions.
    """
    # Ownership gate
    await get_owned_session(session_id, ctx, db)
    session = (await db.execute(
        select(LiveSession).where(LiveSession.id == session_id)
    )).scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")

    # Viewer snapshots: ~360 rows on a 3h session at 30s cadence. Cheap.
    viewer_rows = (await db.execute(
        select(ViewerSnapshot)
        .where(ViewerSnapshot.session_id == session_id)
        .order_by(ViewerSnapshot.timestamp_sec)
    )).scalars().all()

    # Pull just enough segment data to compute the ▲ markers. We don't
    # return the full segment list here — that's what /transcript is for.
    segments = (await db.execute(
        select(ScriptSegment)
        .where(ScriptSegment.session_id == session_id)
        .order_by(ScriptSegment.start_sec)
    )).scalars().all()
    insights = _build_insight_markers(segments)

    notes = (await db.execute(
        select(SessionNote)
        .where(SessionNote.session_id == session_id)
        .order_by(SessionNote.timestamp_sec)
    )).scalars().all()

    return {
        "code": 0,
        "data": {
            "session": {
                "id": session.id,
                "title": session.title,
                "duration_sec": session.duration_sec or 0,
                # Surface the headline analytics number in the workspace
                # header without forcing a second round-trip.
                "engaged_audience_rate": (
                    (session.analysis_summary or {})
                    .get("normalized_metrics", {})
                    .get("engaged_audience_rate")
                ) if session.analysis_summary else None,
                "composite_score": (
                    float(session.composite_score)
                    if session.composite_score is not None else None
                ),
            },
            # Frontend plays the playback rendition; fall back to master when
            # playback hasn't been produced (legacy rows / encode skipped).
            "video_url": _presigned_video_url(
                session.playback_video_path or session.master_video_path
            ),
            "viewer_snapshots": [
                {"timestamp_sec": v.timestamp_sec, "viewer_count": v.viewer_count}
                for v in viewer_rows
            ],
            "insights": insights,
            "notes": [
                {
                    "id": n.id,
                    "timestamp_sec": n.timestamp_sec,
                    "note_text": n.note_text,
                    "created_at": n.created_at.isoformat(),
                }
                for n in notes
            ],
            "segment_count": len(segments),  # for transcript lazy-load sizing
        },
        "message": "ok",
    }


# ---------------------------------------------------------------------------
# GET /sessions/{id}/transcript — windowed lazy load
# ---------------------------------------------------------------------------


@router.get("/{session_id}/transcript")
async def get_transcript_window(
    session_id: str,
    start_sec: int = Query(default=0, ge=0),
    end_sec: int = Query(default=999999, ge=1),
    db: AsyncSession = Depends(get_db),
    ctx: UserContext = Depends(get_admin_or_user),
):
    """Return ScriptSegments whose [start_sec, end_sec) overlaps the
    requested window.

    Why window-based instead of pagination: the workspace's transcript
    panel is anchored to currentTime, not page number. The natural
    request shape is "give me segments visible around 12:30" not
    "give me page 7". Overlap (not strict containment) keeps a segment
    that straddles the window boundary in view.
    """
    # Session ownership gate — returning [] for a non-existent session
    # would silently mask 404s in client code; gate also enforces A2
    # cross-tenant isolation.
    await get_owned_session(session_id, ctx, db)

    if start_sec >= end_sec:
        raise HTTPException(
            status_code=400,
            detail=f"start_sec ({start_sec}) must be < end_sec ({end_sec})",
        )

    segments = (await db.execute(
        select(ScriptSegment)
        .where(
            ScriptSegment.session_id == session_id,
            # Overlap: segment.start < window.end AND segment.end > window.start
            ScriptSegment.start_sec < end_sec,
            ScriptSegment.end_sec > start_sec,
        )
        .order_by(ScriptSegment.start_sec)
    )).scalars().all()

    return {
        "code": 0,
        "data": {
            "session_id": session_id,
            "window": {"start_sec": start_sec, "end_sec": end_sec},
            "segments": [
                {
                    "id": s.id,
                    "start_sec": s.start_sec,
                    "end_sec": s.end_sec,
                    "text": s.text or "",
                    "script_type": s.script_type,
                }
                for s in segments
            ],
        },
        "message": "ok",
    }


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------


class CreateNoteRequest(BaseModel):
    timestamp_sec: int = Field(..., ge=0, description="Pinned timeline position in seconds")
    note_text: str = Field(..., min_length=1, max_length=10_000, description="The note body")


@router.post("/{session_id}/notes")
async def create_note(
    session_id: Annotated[str, Path()],
    payload: Annotated[CreateNoteRequest, Body()],
    db: AsyncSession = Depends(get_db),
    ctx: UserContext = Depends(get_admin_or_user),
):
    """Create a SessionNote pinned at the given timestamp.

    The note body is trimmed of leading/trailing whitespace BEFORE the
    min_length check is meaningful — Pydantic checks the raw input, so we
    re-validate after strip() to reject "   \\n  " as effectively empty.
    """
    # Ownership gate (also enforces 404 for non-existent / cross-tenant).
    session = await get_owned_session(session_id, ctx, db)

    trimmed = payload.note_text.strip()
    if not trimmed:
        raise HTTPException(status_code=422, detail="note_text cannot be empty after trim")

    # Sanity: timestamp must fall within the recording. Off-by-end-of-video
    # notes are nonsensical and almost certainly a frontend bug.
    if session.duration_sec and payload.timestamp_sec > session.duration_sec:
        raise HTTPException(
            status_code=422,
            detail=(
                f"timestamp_sec ({payload.timestamp_sec}) exceeds session "
                f"duration ({session.duration_sec})"
            ),
        )

    note = SessionNote(
        session_id=session_id,
        timestamp_sec=payload.timestamp_sec,
        note_text=trimmed,
        owner_user_id=ctx.id,
    )
    db.add(note)
    await db.commit()
    await db.refresh(note)

    return {
        "code": 0,
        "data": {
            "id": note.id,
            "session_id": note.session_id,
            "timestamp_sec": note.timestamp_sec,
            "note_text": note.note_text,
            "created_at": note.created_at.isoformat(),
        },
        "message": "ok",
    }


class UpdateNoteRequest(BaseModel):
    """v1.5 (2026-05-29) — note_text only. We deliberately don't allow
    re-anchoring timestamp_sec via PATCH: editing the text is the common
    case (fix typo, add detail), re-anchoring is rare and ambiguous (does
    the new timestamp mean "the same insight applies elsewhere too" or
    "the original anchor was wrong"?). Skipping it sidesteps that UX
    question."""
    note_text: str = Field(..., min_length=1, max_length=10_000)


@router.patch("/{session_id}/notes/{note_id}")
async def update_note(
    session_id: Annotated[str, Path()],
    note_id: Annotated[str, Path()],
    payload: Annotated[UpdateNoteRequest, Body()],
    db: AsyncSession = Depends(get_db),
    ctx: UserContext = Depends(get_admin_or_user),
):
    """Update a note's text. session_id is in the path so the URL is
    consistent with the other workspace endpoints and prevents
    cross-session note manipulation (PATCH /sessions/X/notes/Y where
    note Y belongs to session Z is rejected as 404)."""
    await get_owned_session(session_id, ctx, db)  # 404 if not owned
    note = (await db.execute(
        select(SessionNote)
        .where(SessionNote.id == note_id, SessionNote.session_id == session_id)
    )).scalar_one_or_none()
    if note is None:
        raise HTTPException(status_code=404, detail="note not found in this session")

    trimmed = payload.note_text.strip()
    if not trimmed:
        raise HTTPException(status_code=422, detail="note_text cannot be empty after trim")

    note.note_text = trimmed
    await db.commit()
    await db.refresh(note)

    return {
        "code": 0,
        "data": {
            "id": note.id,
            "session_id": note.session_id,
            "timestamp_sec": note.timestamp_sec,
            "note_text": note.note_text,
            "created_at": note.created_at.isoformat(),
        },
        "message": "ok",
    }


@router.delete("/{session_id}/notes/{note_id}")
async def delete_note(
    session_id: Annotated[str, Path()],
    note_id: Annotated[str, Path()],
    db: AsyncSession = Depends(get_db),
    ctx: UserContext = Depends(get_admin_or_user),
):
    """Delete a note. Same session-id-in-path defense as PATCH above —
    a note from another session can't be deleted via this URL.

    Returns 204 (no body) on success. 404 if the note doesn't exist or
    doesn't belong to this session.
    """
    await get_owned_session(session_id, ctx, db)  # cross-tenant 404 gate
    note = (await db.execute(
        select(SessionNote)
        .where(SessionNote.id == note_id, SessionNote.session_id == session_id)
    )).scalar_one_or_none()
    if note is None:
        raise HTTPException(status_code=404, detail="note not found in this session")

    await db.delete(note)
    await db.commit()
    # FastAPI infers 200 by default; explicit 204 for "no body" semantics.
    from fastapi import Response
    return Response(status_code=204)


@router.get("/{session_id}/notes")
async def list_notes(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    ctx: UserContext = Depends(get_admin_or_user),
):
    """List a session's notes ordered by timestamp (ascending).

    Same ordering as the timeline bundle so a client refresh after a
    save lands in a consistent state without extra sort logic.
    """
    await get_owned_session(session_id, ctx, db)
    exists = (await db.execute(
        select(LiveSession.id).where(LiveSession.id == session_id)
    )).scalar_one_or_none()
    if exists is None:
        raise HTTPException(status_code=404, detail="session not found")

    notes = (await db.execute(
        select(SessionNote)
        .where(SessionNote.session_id == session_id)
        .order_by(SessionNote.timestamp_sec)
    )).scalars().all()

    return {
        "code": 0,
        "data": {
            "session_id": session_id,
            "count": len(notes),
            "items": [
                {
                    "id": n.id,
                    "timestamp_sec": n.timestamp_sec,
                    "note_text": n.note_text,
                    "created_at": n.created_at.isoformat(),
                }
                for n in notes
            ],
        },
        "message": "ok",
    }


# ---------------------------------------------------------------------------
# AI Range Analysis (2026-05-29)
# ---------------------------------------------------------------------------

# Bounds agreed with user 2026-05-29:
#   min 1 segment (analyze a single moment)
#   max 60 segments (~10-20 min content at ASR pacing) — beyond this both
#       prompt cost and Claude attention dilution become problematic.
_MIN_SEGMENTS = 1
_MAX_SEGMENTS = 60

# Context window: ±30s of segments around the selection get rendered into
# the prompt as "context" so Claude understands the surrounding rhythm
# without being asked to evaluate the context itself.
_CONTEXT_HALF_SEC = 30


class RangeAnalysisRequest(BaseModel):
    segment_ids: list[str] = Field(
        ...,
        min_length=_MIN_SEGMENTS,
        max_length=_MAX_SEGMENTS,
        description=f"{_MIN_SEGMENTS}-{_MAX_SEGMENTS} ScriptSegment ids to analyze",
    )


@router.post("/{session_id}/range-analysis")
async def create_range_analysis(
    session_id: Annotated[str, Path()],
    payload: Annotated[RangeAnalysisRequest, Body()],
    db: AsyncSession = Depends(get_db),
    ctx: UserContext = Depends(get_admin_or_user),
):
    """Run Claude over the selected ScriptSegments and persist the result.

    Returns the new SessionAnalysis row. Failure modes:
      - 404 session not found
      - 422 segment_ids violate count bounds (Pydantic) OR contain ids
        that don't belong to this session
      - 503 ANTHROPIC_API_KEY not configured on the backend
      - 502 Claude API call failed (rate limit, network, content filter)
    """
    # ---- Session existence + load context fields ----
    # Ownership gate — 404 if missing or cross-tenant.
    session = await get_owned_session(session_id, ctx, db)

    # ---- Verify all segment_ids belong to this session ----
    # Stale frontend cache or a copy-pasted id from another session would
    # otherwise silently leak segments across sessions in the prompt.
    requested_ids = set(payload.segment_ids)
    found_segments = (await db.execute(
        select(ScriptSegment)
        .where(
            ScriptSegment.session_id == session_id,
            ScriptSegment.id.in_(requested_ids),
        )
        .order_by(ScriptSegment.start_sec)
    )).scalars().all()
    found_ids = {s.id for s in found_segments}
    missing = requested_ids - found_ids
    if missing:
        raise HTTPException(
            status_code=422,
            detail=(
                f"{len(missing)} segment id(s) not found in this session: "
                f"{sorted(missing)[:3]}{'...' if len(missing) > 3 else ''}"
            ),
        )

    # ---- Pull context segments (±30s around the selected range) ----
    min_start = min(s.start_sec for s in found_segments)
    max_end = max(s.end_sec for s in found_segments)
    ctx_lo = max(0, min_start - _CONTEXT_HALF_SEC)
    ctx_hi = max_end + _CONTEXT_HALF_SEC

    selected_id_set = found_ids
    context_segments = (await db.execute(
        select(ScriptSegment)
        .where(
            ScriptSegment.session_id == session_id,
            ScriptSegment.start_sec >= ctx_lo,
            ScriptSegment.end_sec <= ctx_hi,
            ~ScriptSegment.id.in_(selected_id_set),
        )
        .order_by(ScriptSegment.start_sec)
    )).scalars().all()
    context_before = [s for s in context_segments if s.end_sec <= min_start]
    context_after = [s for s in context_segments if s.start_sec >= max_end]

    # ---- Resolve anchor name for the prompt ----
    anchor_name = None
    if session.anchor_id:
        anchor = (await db.execute(
            select(Anchor).where(Anchor.id == session.anchor_id)
        )).scalar_one_or_none()
        if anchor:
            anchor_name = anchor.name

    # ---- Build prompt inputs ----
    def _to_seg(s):
        return AnalysisSegment(
            start_sec=int(s.start_sec or 0),
            end_sec=int(s.end_sec or 0),
            text=s.text or "",
        )

    selected_for_llm = [_to_seg(s) for s in found_segments]
    context = AnalysisContext(
        title=session.title or "",
        duration_min=int((session.duration_sec or 0) // 60),
        composite_score=(
            float(session.composite_score) if session.composite_score is not None else None
        ),
        anchor_name=anchor_name,
    )

    # ---- Call Claude (the actual money-spending part) ----
    try:
        result = await analyze_range(
            selected=selected_for_llm,
            context_before=[_to_seg(s) for s in context_before],
            context_after=[_to_seg(s) for s in context_after],
            session=context,
        )
    except LLMNotConfigured as e:
        raise HTTPException(status_code=503, detail=str(e))
    except LLMCallFailed as e:
        # 502 = bad upstream — distinguishes from our own bugs (500)
        raise HTTPException(status_code=502, detail=str(e))

    # ---- Persist + return ----
    row = SessionAnalysis(
        session_id=session_id,
        owner_user_id=ctx.id,
        # Store the ids in the same order the segments were rendered to
        # the LLM (chronological), not in the order the client sent them.
        # Makes future "rerun this analysis" deterministic.
        segment_ids=[s.id for s in found_segments],
        prompt_version=result.prompt_version,
        result=result.result,
        token_usage=result.token_usage,
        # Denormalised range bounds for the workspace's "jump to range
        # start" affordance — computed from the same chronological-order
        # segments above (min_start / max_end were calculated earlier
        # against the requested set, which is the same set).
        range_start_sec=int(min_start),
        range_end_sec=int(max_end),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)

    return {
        "code": 0,
        "data": _serialize_analysis(row),
        "message": "ok",
    }


@router.get("/{session_id}/range-analyses")
async def list_range_analyses(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    ctx: UserContext = Depends(get_admin_or_user),
):
    """List all AI analyses for a session, newest first.

    Used by the workspace to render the analysis history panel when the
    user reopens a session.
    """
    await get_owned_session(session_id, ctx, db)

    rows = (await db.execute(
        select(SessionAnalysis)
        .where(SessionAnalysis.session_id == session_id)
        .order_by(SessionAnalysis.created_at.desc())
    )).scalars().all()

    return {
        "code": 0,
        "data": {
            "session_id": session_id,
            "count": len(rows),
            "items": [_serialize_analysis(r) for r in rows],
        },
        "message": "ok",
    }


def _serialize_analysis(row: SessionAnalysis) -> dict:
    return {
        "id": row.id,
        "session_id": row.session_id,
        "segment_ids": list(row.segment_ids or []),
        "prompt_version": row.prompt_version,
        "result": row.result,
        "token_usage": row.token_usage,
        "range_start_sec": row.range_start_sec,
        "range_end_sec": row.range_end_sec,
        "created_at": row.created_at.isoformat(),
    }


# ---------------------------------------------------------------------------
# 高光时刻 / Highlights (Sprint 6A — see docs/05-sprint6-highlights.md)
#
# We don't store highlights in their own table: they're a pure projection
# of the immutable insight_report.timeline_highlights the analysis pipeline
# already wrote. GET reads + filters; POST cuts a clip on demand. No
# migration, no job queue, no cache (ADR §1/§5).
# ---------------------------------------------------------------------------

# A highlight is "shareable" iff its significance score clears this bar.
# 60 is the engine's own positive/strong threshold (insight_engine's
# hot_patterns use score>=60; weak_spots use <40). Empirically verified
# 2026-06-10 across 28 real sessions: every score>=60 highlight is a
# positive "购买意向" moment, none are negative "翻车" insights. ADR §3.
_SHAREABLE_SCORE = 60.0

# Clip presigned URL lifetime. iOS downloads the clip immediately after
# export (save-to-Photos / Share Sheet), so 1h is generous; shorter than
# the 4h video TTL because there's no "left the tab open" case here.
_CLIP_TTL = timedelta(hours=1)


def _filtered_highlights(session: LiveSession) -> list[dict]:
    """Shareable highlights for a session, newest-strongest first.

    Projection of insight_report.timeline_highlights with the score>=60
    gate (ADR §3). A highlight's `id` is its start_sec — the natural stable
    key across the GET (list) and POST (export) calls, since the underlying
    timeline_highlights array is regenerated only on re-analysis.

    Returns [] when the session has no qualifying highlights — which is the
    common case (~36% of sessions), NOT an error. The client renders an
    empty state (ADR §3); we never fabricate a highlight.
    """
    summary = session.analysis_summary or {}
    raw = (summary.get("insight_report") or {}).get("timeline_highlights") or []
    items = []
    for h in raw:
        start = h.get("start_sec")
        if start is None:
            continue
        if float(h.get("score") or 0) < _SHAREABLE_SCORE:
            continue
        items.append({
            "id": str(int(start)),
            "start_sec": int(start),
            "end_sec": int(h.get("end_sec") or start),
            "score": float(h.get("score") or 0),
            "type": h.get("type") or "insight",
            "reason": h.get("reason") or "",
        })
    items.sort(key=lambda h: h["score"], reverse=True)
    return items


def _minio_client(public: bool = False):
    """public=True 用对外 endpoint(签发给客户端的 presigned URL);
    否则用内部 endpoint(后端自己读写 / ffmpeg 取源)。"""
    from minio import Minio
    endpoint = (settings.minio_public_endpoint or settings.minio_endpoint) if public \
        else settings.minio_endpoint
    return Minio(
        endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
        # 对外 client 只用来本地签名 presigned URL。显式给 region 可免去签名前
        # 向"对外地址"发起的 bucket-location 网络查询——容器内通常连不上该地址
        # (例如 localhost:9002 指向容器自己)。MinIO 默认 region 即 us-east-1。
        region="us-east-1" if public else None,
    )


def _export_clip(master_path: str, start_sec: int, duration_sec: int, object_name: str) -> tuple[str, int]:
    """Cut [start, start+duration] out of the master and return a presigned
    clip URL. Sync (subprocess + blocking MinIO IO) — call via to_thread.

    `-ss` before `-i` does an HTTP-range seek on the master's presigned URL,
    so ffmpeg pulls only the clip window, not the whole 200MB+ file. `-c copy`
    stream-copies (sub-second, no re-encode, no quality loss, keeps source
    codec). `-t DURATION` not `-to`: with `-ss` before input, `-to` would be
    relative to the seek point. Spike-validated 2026-06-10 (ADR §1).
    """
    bucket = master_path.split("/", 1)[0]
    # ffmpeg 在服务端取源 → 用内部 endpoint(public=False)
    src_url = _presigned_video_url(master_path, public=False)
    if not src_url:
        raise VideoProcessError("无法为 master 视频生成下载链接")

    fd, tmp_path = tempfile.mkstemp(suffix=".mp4", prefix="clip_")
    os.close(fd)
    try:
        cmd = [
            FFMPEG, "-nostdin",
            "-ss", str(start_sec),
            "-i", src_url,
            "-t", str(duration_sec),
            "-c", "copy",
            "-movflags", "+faststart",
            tmp_path, "-y",
        ]
        _run_ffmpeg(cmd, timeout=120.0, desc="高光切片导出")

        # 上传走内部 endpoint;给手机的下载链接用对外 endpoint 签发
        _minio_client(public=False).fput_object(bucket, object_name, tmp_path, content_type="video/mp4")
        url = _minio_client(public=True).presigned_get_object(bucket, object_name, expires=_CLIP_TTL)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
    return url, int(_CLIP_TTL.total_seconds())


@router.get("/{session_id}/highlights")
async def list_highlights(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    ctx: UserContext = Depends(get_admin_or_user),
):
    """List the session's shareable highlights (score>=60), strongest first.

    Empty list is normal (~36% of sessions) — the client shows an empty
    state, not an error.
    """
    session = await get_owned_session(session_id, ctx, db)  # 404 if missing / cross-tenant
    items = _filtered_highlights(session)
    return {
        "code": 0,
        "data": {"session_id": session_id, "count": len(items), "items": items},
        "message": "ok",
    }


@router.post("/{session_id}/highlights/{highlight_id}/export")
async def export_highlight(
    session_id: Annotated[str, Path()],
    highlight_id: Annotated[str, Path()],
    db: AsyncSession = Depends(get_db),
    ctx: UserContext = Depends(get_admin_or_user),
):
    """Cut the highlight out of the master and return a presigned clip URL.

    Only AI-identified highlights are exportable — the client passes a
    highlight_id, never an arbitrary start/end (ADR §4). Synchronous: the
    `-c copy` cut is sub-second (ADR §1), so there's no job/polling.

    Failure modes:
      - 404 session or highlight not found (or cross-tenant)
      - 409 session has no master video to clip
      - 422 highlight has zero duration
      - 502 ffmpeg failed / timed out
    """
    session = await get_owned_session(session_id, ctx, db)

    hl = next((h for h in _filtered_highlights(session) if h["id"] == highlight_id), None)
    if hl is None:
        raise HTTPException(status_code=404, detail="highlight not found in this session")

    master = session.master_video_path
    if not master or "/" not in master:
        raise HTTPException(status_code=409, detail="session has no master video to clip")

    duration = hl["end_sec"] - hl["start_sec"]
    if duration <= 0:
        raise HTTPException(status_code=422, detail="highlight has zero duration, cannot export")

    object_name = f"clips/{session_id}/{highlight_id}.mp4"
    try:
        clip_url, expires_in = await asyncio.to_thread(
            _export_clip, master, hl["start_sec"], duration, object_name
        )
    except VideoProcessError as e:
        raise HTTPException(status_code=502, detail=str(e))

    return {
        "code": 0,
        "data": {"clip_url": clip_url, "expires_in": expires_in},
        "message": "ok",
    }
