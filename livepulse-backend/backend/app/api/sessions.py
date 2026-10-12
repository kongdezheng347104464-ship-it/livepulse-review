from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.session import LiveSession
from app.models.danmaku import Danmaku
from app.models.script_segment import ScriptSegment
from app.models.pipeline_step import PipelineStep
from app.config import get_settings
from app.services.auth.current_user import UserContext, get_admin_or_user
from app.services.ownership import apply_owner_scope, get_owned_session

router = APIRouter(prefix="/sessions", tags=["sessions"])
settings = get_settings()


def _session_to_dict(session: LiveSession, include_summary: bool = True) -> dict:
    """Serialize a LiveSession.

    include_summary=False trims the heavy `analysis_summary` JSONB (used by the
    list endpoint where each row would otherwise drag along several KB of
    insight_report / semantic data).
    """
    base = {
        "id": session.id,
        "anchor_id": session.anchor_id,
        # QA 2026-05-29 L-4: list cards previously showed "测试" * N
        # with no way to tell anchors apart. Surface anchor.name via the
        # already-selectin-loaded relationship — zero extra query.
        "anchor_name": session.anchor.name if session.anchor else None,
        "platform": session.platform,
        "title": session.title,
        "cover_url": session.cover_url,
        "master_video_path": session.master_video_path,
        "playback_video_path": session.playback_video_path,
        "was_transcoded": session.was_transcoded,
        "start_time": session.start_time.isoformat() if session.start_time else None,
        "end_time": session.end_time.isoformat() if session.end_time else None,
        "duration_sec": session.duration_sec,
        "viewer_peak": session.viewer_peak,
        "viewer_avg": session.viewer_avg,
        "total_danmaku": session.total_danmaku,
        "total_gmv": float(session.total_gmv) if session.total_gmv else None,
        "total_orders": session.total_orders,
        "new_followers": session.new_followers,
        "composite_score": float(session.composite_score) if session.composite_score else None,
        "status": session.status,
        "created_at": session.created_at.isoformat() if session.created_at else None,
    }
    if include_summary:
        base["analysis_summary"] = session.analysis_summary
    else:
        # Surface just the three sub-scores the list card actually reads, so
        # the frontend doesn't have to refetch a second endpoint. Prefer the
        # nested `scores` dict (post-audit format), fall back to the legacy
        # flat fields for sessions analyzed before the audit fix.
        summary = session.analysis_summary or {}
        scores = summary.get("scores") or {}
        base["scores"] = {
            "engagement": scores.get("engagement", summary.get("engagement_score", 0)),
            "conversion": scores.get("conversion", summary.get("conversion_score", 0)),
            "content": scores.get("content", summary.get("content_score", 0)),
        }
    return base


@router.get("/")
async def list_sessions(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status: str | None = Query(default=None),
    anchor_id: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    ctx: UserContext = Depends(get_admin_or_user),
):
    query = apply_owner_scope(select(LiveSession), LiveSession, ctx)
    count_query = apply_owner_scope(
        select(func.count(LiveSession.id)), LiveSession, ctx,
    )

    if status:
        query = query.where(LiveSession.status == status)
        count_query = count_query.where(LiveSession.status == status)
    if anchor_id:
        query = query.where(LiveSession.anchor_id == anchor_id)
        count_query = count_query.where(LiveSession.anchor_id == anchor_id)

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    query = query.order_by(LiveSession.created_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    sessions = result.scalars().all()

    return {
        "code": 0,
        "data": {
            "items": [_session_to_dict(s, include_summary=False) for s in sessions],
            "total": total,
            "page": page,
            "page_size": page_size,
        },
        "message": "ok",
    }


# ---------------------------------------------------------------------------
# GET danmaku window — danmaku by time range
# (must be before /{session_id} to avoid route conflict)
# ---------------------------------------------------------------------------

@router.get("/{session_id}/danmaku-window")
async def get_danmaku_window(
    session_id: str,
    start_sec: float = Query(default=0, ge=0),
    end_sec: float = Query(default=99999, ge=0),
    limit: int = Query(default=50, ge=5, le=200),
    source_type: str = Query(
        default="speech",
        description=(
            "Filter by event source. Default 'speech' enforces the Q1 "
            "conversion invariant at the API layer — UI components that "
            "show 'conversion evidence' or 'audience reaction' MUST receive "
            "speech-only data; gift/system events are events of a different "
            "kind and don't belong in a conversation drawer. Pass 'all' "
            "explicitly to opt out (e.g. for a future heat-events panel)."
        ),
    ),
    db: AsyncSession = Depends(get_db),
    ctx: UserContext = Depends(get_admin_or_user),
):
    """Return danmakus within a time range.

    The default `source_type='speech'` filter is THE second line of
    defense for the Q1 invariant. The first line is the pipeline-side
    invariant (insight_engine refuses to emit conversion insights that
    reference non-speech events); this API-level filter makes the
    invariant hold even if a future caller skips that check.
    """
    # Ownership gate: 404 if session missing OR owned by another user.
    await get_owned_session(session_id, ctx, db)

    q = (
        select(Danmaku)
        .where(Danmaku.session_id == session_id,
               Danmaku.timestamp_sec >= start_sec,
               Danmaku.timestamp_sec <= end_sec)
        # timestamp_sec 精度是秒（DB 字段是 Integer），同一秒可能有多条
        # 弹幕。加 id 做二级排序，保证刷新顺序稳定，不依赖物理存储顺序。
        .order_by(Danmaku.timestamp_sec, Danmaku.id)
        .limit(limit)
    )
    if source_type != "all":
        # Pass the string straight through; SQLAlchemy compares against
        # the ENUM column. Invalid values (e.g. 'speeech' typo) return
        # zero rows rather than erroring — that's a UX trade-off worth
        # noting if a future caller debugs an empty drawer.
        q = q.where(Danmaku.source_type == source_type)

    dm_result = await db.execute(q)
    danmakus = dm_result.scalars().all()

    return {
        "code": 0,
        "data": {
            "danmakus": [
                {
                    "id": d.id,
                    "content": d.content,
                    "timestamp_sec": d.timestamp_sec,
                    "sentiment": d.sentiment,
                    "intent": d.intent,
                    # New fields surfaced by the P0-Evt / P1-Norm rebuild.
                    # Front-end can render the username chip on the chat
                    # bubble (P2 polish); right now it's at least there
                    # for ad-hoc debugging via /api response inspection.
                    "username": d.username,
                    "normalized_username": d.normalized_username,
                    "source_type": d.source_type.value if d.source_type else None,
                }
                for d in danmakus
            ],
        },
        "message": "ok",
    }


# ---------------------------------------------------------------------------
# GET intent-details — drill-down: danmaku by intent type
# (must be before /{session_id} to avoid route conflict)
# ---------------------------------------------------------------------------

@router.get("/{session_id}/intent-details")
async def get_intent_details(
    session_id: str,
    intent: str = Query(default="strong_purchase", description="Intent type filter"),
    limit: int = Query(default=20, ge=5, le=100),
    db: AsyncSession = Depends(get_db),
    ctx: UserContext = Depends(get_admin_or_user),
):
    """Return danmakus filtered by intent type with related script context.

    Implicitly speech-only: intent classification (strong_purchase /
    purchase_consideration / etc.) only runs over events with
    source_type='speech' in the pipeline — gift / system events have
    intent=NULL by design. So filtering on `intent` is already an
    indirect filter on source_type. We still add the explicit
    source_type='speech' predicate below as defense-in-depth: it
    makes the SQL plan deterministic and prevents a future intent-
    inference change on non-speech events from leaking into this
    drawer.
    """
    await get_owned_session(session_id, ctx, db)

    dm_query = (
        select(Danmaku)
        .where(Danmaku.session_id == session_id,
               Danmaku.source_type == "speech")  # Q1 invariant defense
        .order_by(Danmaku.timestamp_sec)
        .limit(limit)
    )
    if intent and intent != "all":
        dm_query = dm_query.where(Danmaku.intent == intent)

    dm_result = await db.execute(dm_query)
    danmakus = dm_result.scalars().all()

    # Get total count for this intent (also speech-only for consistency
    # with the filter above — otherwise total != items count looks like
    # a paging bug).
    count_filter = [Danmaku.session_id == session_id,
                    Danmaku.source_type == "speech"]
    if intent and intent != "all":
        count_filter.append(Danmaku.intent == intent)
    count_result = await db.execute(
        select(func.count()).where(*count_filter)
    )
    total = count_result.scalar() or 0

    items = []
    for d in danmakus:
        m, s = divmod(int(d.timestamp_sec or 0), 60)
        items.append({
            "id": d.id,
            "time": f"{m}:{s:02d}",
            "timestamp_sec": d.timestamp_sec,
            "content": d.content,
            "sentiment": d.sentiment,
            "intent": d.intent,
            "intent_type": d.intent,  # deprecated compat
            # Surface new P0-Evt / P1-Norm fields for UI (display the
            # username chip on the row, attribute repeat-asks to the
            # same person at the drawer level).
            "username": d.username,
            "normalized_username": d.normalized_username,
            "source_type": d.source_type.value if d.source_type else None,
        })

    return {
        "code": 0,
        "data": {
            "session_id": session_id,
            "intent": intent,
            "count": total,
            "items": items,
        },
        "message": "ok",
    }


# ---------------------------------------------------------------------------
# GET strategies — drill-down: tactic examples with script text
# (must be before /{session_id} to avoid route conflict)
# ---------------------------------------------------------------------------

@router.get("/{session_id}/strategies")
async def get_strategy_examples(
    session_id: str,
    tactic: str | None = Query(default=None, description="Filter by tactic type"),
    sort_by: str = Query(default="conversion", description="Sort: conversion, time"),
    limit: int = Query(default=10, ge=5, le=50),
    db: AsyncSession = Depends(get_db),
    ctx: UserContext = Depends(get_admin_or_user),
):
    """Return strategy-tagged script segments grouped by tactic type."""
    from collections import defaultdict

    await get_owned_session(session_id, ctx, db)

    seg_result = await db.execute(
        select(ScriptSegment)
        .where(ScriptSegment.session_id == session_id)
        .order_by(ScriptSegment.score.desc())
        .limit(500)
    )
    segments = seg_result.scalars().all()

    groups: dict[str, dict] = defaultdict(lambda: {"type": "", "title": "", "count": 0, "examples": []})
    TACTIC_META = {
        "scarcity": "稀缺策略", "urgency": "紧迫感", "social_proof": "社会证明",
        "authority": "专业权威", "emotional_trigger": "情感触发",
        "interactive_hook": "互动钩子", "price_anchor": "价格锚点",
        "risk_reversal": "风险逆转", "welfare_stimulus": "福利刺激",
        "purchase_guide": "购买引导",
    }

    for seg in segments:
        tags = (seg.analysis_tags or {}).get("embedding_tactics", [])
        if not tags:
            continue
        for t in tags:
            t_type = t.get("type", "") if isinstance(t, dict) else str(t)
            if not t_type:
                continue
            groups[t_type]["type"] = t_type
            groups[t_type]["title"] = TACTIC_META.get(t_type, t_type)
            groups[t_type]["count"] += 1
            groups[t_type]["examples"].append({
                "start_sec": seg.start_sec,
                "end_sec": seg.end_sec,
                "script_text": seg.text,
                "score": float(seg.score) if seg.score else 0,
                "speech_rate": seg.speech_rate,
                "strategy_tags": [x.get("type", str(x)) if isinstance(x, dict) else str(x) for x in tags],
            })

    if tactic and tactic in groups:
        groups = {tactic: groups[tactic]}
    elif tactic:
        groups = {}

    result_list = []
    for t_type, g in sorted(groups.items()):
        examples = g["examples"]
        if sort_by == "conversion":
            examples.sort(key=lambda x: x["score"], reverse=True)
        elif sort_by == "time":
            examples.sort(key=lambda x: x["start_sec"])
        g["examples"] = examples[:limit]
        result_list.append(g)

    return {
        "code": 0,
        "data": {"session_id": session_id, "strategies": result_list},
        "message": "ok",
    }


# ---------------------------------------------------------------------------
# GET status — lightweight polling endpoint
# Returns only status + score + per-step progress (no payload data).
# Frontend polls this every 5s while analyzing; only fetches /{session_id}
# once the status reaches a terminal state.
# (must be before /{session_id} to avoid route conflict)
# ---------------------------------------------------------------------------

@router.get("/{session_id}/status")
async def get_session_status(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    ctx: UserContext = Depends(get_admin_or_user),
):
    # Ownership gate first — 404 if missing OR not owned by ctx user.
    await get_owned_session(session_id, ctx, db)
    result = await db.execute(
        select(
            LiveSession.id, LiveSession.status, LiveSession.composite_score,
            LiveSession.error_message, LiveSession.analysis_started_at,
            LiveSession.analysis_finished_at, LiveSession.duration_sec,
        ).where(LiveSession.id == session_id)
    )
    row = result.first()

    steps_result = await db.execute(
        select(
            PipelineStep.step_name, PipelineStep.step_order, PipelineStep.status,
            PipelineStep.duration_ms, PipelineStep.error_message,
        )
        .where(PipelineStep.session_id == session_id)
        .order_by(PipelineStep.step_order)
    )
    steps = [
        {
            "name": s.step_name, "order": s.step_order, "status": s.status,
            "duration_ms": s.duration_ms, "error": s.error_message,
        }
        for s in steps_result.all()
    ]

    # Real progress derived from step completion ratio. Terminal states report
    # 100; failed runs also report 100 so the UI stops spinning.
    TERMINAL_STATUSES = {"analyzed", "done", "error", "failed"}
    if row.status in TERMINAL_STATUSES:
        progress = 100
    else:
        # 2026-06-03 Fix 1: divide by the TOTAL pipeline step count, not by
        # the number of rows created so far. Steps are lazily inserted (one
        # row appears only when that step starts), so `len(steps)` grows
        # over time — dividing by it produced the "0 → 50% → stuck → 75%"
        # non-linear jumps the user saw (1 running step out of 1 created
        # row = 50%, frozen for the whole 10-min video_processing stage).
        # STEP_ORDER is the fixed denominator (11 steps).
        from app.services.step_tracker import STEP_ORDER
        total = len(STEP_ORDER)
        done = sum(1 for s in steps if s["status"] in ("done", "skipped"))
        running = sum(1 for s in steps if s["status"] == "running")
        # Each done step = 1.0, each running = 0.5 (mid-flight estimate)
        progress = min(int((done + running * 0.5) / total * 100), 99)

    return {
        "code": 0,
        "data": {
            "session_id": row.id,
            "status": row.status,
            "progress": progress,
            "composite_score": float(row.composite_score) if row.composite_score else None,
            "error_message": row.error_message,
            "duration_sec": row.duration_sec,
            "analysis_started_at": row.analysis_started_at.isoformat() if row.analysis_started_at else None,
            "analysis_finished_at": row.analysis_finished_at.isoformat() if row.analysis_finished_at else None,
            "steps": steps,
        },
        "message": "ok",
    }


# Pathological-input guardrails. Beyond these counts, fall back to returning
# only the first N rows + a `truncated=true` flag so the API doesn't OOM the
# server or send a 50MB payload. For typical 2–3h livestreams (≤4000 segments,
# ≤30000 danmakus) the full set is returned in a single response.
_SEGMENT_HARD_CAP = 10000
_DANMAKU_HARD_CAP = 50000


@router.get("/{session_id}")
async def get_session_detail(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    ctx: UserContext = Depends(get_admin_or_user),
):
    """Return the *complete* session for analysis rendering.

    Returns ALL script segments and danmakus in a single response — this
    endpoint is intended for the analysis page which needs to reason over
    the full timeline. Pagination was previously bolted on but silently
    truncated long sessions (only first 100 segments returned by default),
    leaving the back half of the semantic timeline blank.

    Guardrails: if the session has > _SEGMENT_HARD_CAP segments or
    > _DANMAKU_HARD_CAP danmakus, the response includes the first N rows
    plus a `truncated` flag so the caller can detect and fall back to a
    streaming/incremental load path.
    """
    session = await get_owned_session(session_id, ctx, db)

    # Count first so we can detect pathological cases without loading them all.
    seg_total = (await db.execute(
        select(func.count()).where(ScriptSegment.session_id == session_id)
    )).scalar() or 0
    dm_total = (await db.execute(
        select(func.count()).where(Danmaku.session_id == session_id)
    )).scalar() or 0

    seg_truncated = seg_total > _SEGMENT_HARD_CAP
    dm_truncated = dm_total > _DANMAKU_HARD_CAP

    seg_query = (
        select(ScriptSegment)
        .where(ScriptSegment.session_id == session_id)
        .order_by(ScriptSegment.start_sec)
    )
    if seg_truncated:
        seg_query = seg_query.limit(_SEGMENT_HARD_CAP)
    segments = (await db.execute(seg_query)).scalars().all()

    dm_query = (
        select(Danmaku)
        .where(Danmaku.session_id == session_id)
        .order_by(Danmaku.timestamp_sec)
    )
    if dm_truncated:
        dm_query = dm_query.limit(_DANMAKU_HARD_CAP)
    danmakus = (await db.execute(dm_query)).scalars().all()

    # 2026-05-29: viewer_snapshots added to detail response so the
    # Conversion tab can render a viewer-count line chart alongside
    # the funnel. Payload is small (~360 rows max on a 3h session,
    # ~12-byte ints; even bigger sessions stay under 20 KB).
    from app.models.viewer_snapshot import ViewerSnapshot
    viewer_rows = (await db.execute(
        select(ViewerSnapshot)
        .where(ViewerSnapshot.session_id == session_id)
        .order_by(ViewerSnapshot.timestamp_sec)
    )).scalars().all()

    return {
        "code": 0,
        "data": {
            "session": _session_to_dict(session),
            "viewer_snapshots": [
                {"timestamp_sec": v.timestamp_sec, "viewer_count": v.viewer_count}
                for v in viewer_rows
            ],
            "danmakus": [
                {
                    "id": d.id,
                    "user_id": d.user_id,
                    "content": d.content,
                    "timestamp_sec": d.timestamp_sec,
                    "sentiment": d.sentiment,
                    "intent": d.intent,
                    # TODO: remove intent_type after frontend migration to "intent" only
                    "intent_type": d.intent,  # deprecated — use "intent"
                }
                for d in danmakus
            ],
            "script_segments": [
                {
                    "id": s.id,
                    "start_sec": s.start_sec,
                    "end_sec": s.end_sec,
                    "text": s.text,
                    "script_type": s.script_type,
                    "speech_rate": s.speech_rate,
                    "keywords": s.keywords,
                    "product_mentions": s.product_mentions,
                    "score": float(s.score) if s.score else None,
                    "analysis_tags": s.analysis_tags,
                }
                for s in segments
            ],
            "totals": {
                "segments": seg_total,
                "danmakus": dm_total,
                "segments_returned": len(segments),
                "danmakus_returned": len(danmakus),
                "segments_truncated": seg_truncated,
                "danmakus_truncated": dm_truncated,
            },
            "historical_comparison": await _compute_historical_comparison(db, session),
        },
        "message": "ok",
    }


# ---------------------------------------------------------------------------
# Historical comparison logic
# ---------------------------------------------------------------------------

# Baseline window: last 5 valid sessions within 30 days of the current
# session's start_time. Daily broadcasters get ~5 days of context, weekly
# broadcasters get ~5 weeks — neither is overwhelmed by stale data.
_BASELINE_MAX_SAMPLES = 5
_BASELINE_WINDOW_DAYS = 30
_CONFIDENCE_THRESHOLD = 0.6
_MIN_BASELINE_SAMPLES = 3
_LOW_CONFIDENCE_THRESHOLD = 0.6


async def _compute_historical_comparison(db: AsyncSession, session: LiveSession) -> dict:
    """Compute "this session vs your recent baseline" for the comparison card.

    Baseline pool:
      * Same anchor_id
      * valid_for_baseline == True (analyzed, duration >= 10min, segments >= 30)
      * analysis_confidence >= 0.6 (excludes degraded runs)
      * start_time within 30 days BEFORE current session
      * session_id != current
      * Take most recent up to 5

    Returns:
      * `{"insufficient": true, "available_count": N, "required": 3, ...}`
        when too few baseline samples
      * `{"metrics": {...}, "n_baseline": N, ...}` otherwise
    """
    from datetime import timedelta
    from app.models.session import LiveSession as LS

    if not session.start_time:
        return {"insufficient": True, "available_count": 0, "required": _MIN_BASELINE_SAMPLES,
                "reason": "current session has no start_time"}

    window_start = session.start_time - timedelta(days=_BASELINE_WINDOW_DAYS)

    rows = (await db.execute(
        select(LS)
        .where(
            LS.anchor_id == session.anchor_id,
            LS.id != session.id,
            LS.status == "analyzed",
            LS.start_time >= window_start,
            LS.start_time < session.start_time,
        )
        .order_by(LS.start_time.desc())
    )).scalars().all()

    # Apply confidence + valid_for_baseline filters in Python because they
    # live inside JSONB analysis_summary.data_completeness.
    eligible: list[LS] = []
    excluded_low_confidence = 0
    excluded_invalid = 0
    for r in rows:
        dc = (r.analysis_summary or {}).get("data_completeness") or {}
        if not dc.get("valid_for_baseline"):
            excluded_invalid += 1
            continue
        if (dc.get("analysis_confidence") or 0) < _CONFIDENCE_THRESHOLD:
            excluded_low_confidence += 1
            continue
        eligible.append(r)
        if len(eligible) >= _BASELINE_MAX_SAMPLES:
            break

    current_dc = (session.analysis_summary or {}).get("data_completeness") or {}
    current_confidence = current_dc.get("analysis_confidence") or 0
    current_low_confidence = current_confidence < _LOW_CONFIDENCE_THRESHOLD

    n_eligible = len(eligible)
    if n_eligible < _MIN_BASELINE_SAMPLES:
        return {
            "insufficient": True,
            "available_count": n_eligible,
            "required": _MIN_BASELINE_SAMPLES,
            "baseline_window_days": _BASELINE_WINDOW_DAYS,
            "current_confidence": current_confidence,
            "current_low_confidence": current_low_confidence,
        }

    # Aggregate baseline scores
    def _mean(vals: list[float]) -> float:
        return round(sum(vals) / len(vals), 1) if vals else 0.0

    def _metric_block(current_val: float, baseline_vals: list[float], metric_key: str) -> dict:
        baseline = _mean(baseline_vals)
        delta_abs = round(current_val - baseline, 1)
        delta_pct = round((current_val - baseline) / baseline * 100) if baseline > 0 else 0
        if delta_abs >= 1:
            direction = "up"
            label = f"本场比近期高 {abs(delta_pct)}%"
        elif delta_abs <= -1:
            direction = "down"
            label = f"本场比近期低 {abs(delta_pct)}%"
        else:
            direction = "flat"
            label = "本场与近期持平"
        return {
            "current": round(current_val, 1),
            "baseline": baseline,
            "delta_abs": delta_abs,
            "delta_pct": delta_pct,
            "direction": direction,
            "label": label,
        }

    current_scores = (session.analysis_summary or {}).get("scores") or {}

    def _baseline_scores(key: str) -> list[float]:
        vals = []
        for r in eligible:
            s = (r.analysis_summary or {}).get("scores") or {}
            v = s.get(key)
            if v is not None:
                vals.append(float(v))
        return vals

    metrics = {
        "engagement": _metric_block(
            float(current_scores.get("engagement", 0)),
            _baseline_scores("engagement"),
            "engagement",
        ),
        "conversion": _metric_block(
            float(current_scores.get("conversion", 0)),
            _baseline_scores("conversion"),
            "conversion",
        ),
        "content": _metric_block(
            float(current_scores.get("content", 0)),
            _baseline_scores("content"),
            "content",
        ),
        # composite included for completeness only — UI MUST render it
        # de-emphasized; the formula is known to be unstable (see F-2).
        "composite": {
            **_metric_block(
                float(session.composite_score or 0),
                [float(r.composite_score or 0) for r in eligible if r.composite_score is not None],
                "composite",
            ),
            "stability": "low",
        },
    }

    # Version-stamp the comparison so the frontend can warn when the
    # current session and baseline sessions were scored under different
    # rule sets. (Today they're always the same; tomorrow a v3→v4 change
    # could silently invalidate historical comparison if we don't track.)
    from app.services.versions import get_versions
    current_versions = (session.analysis_summary or {}).get("versions") or {}
    baseline_versions: dict[str, set] = {}
    for r in eligible:
        rv = (r.analysis_summary or {}).get("versions") or {}
        for k, v in rv.items():
            baseline_versions.setdefault(k, set()).add(v)
    cross_version = any(
        len(vs - {current_versions.get(k)}) > 0
        for k, vs in baseline_versions.items()
    )

    return {
        "insufficient": False,
        "baseline_label": f"近 {_BASELINE_WINDOW_DAYS} 天内最近 {n_eligible} 场有效数据",
        "n_baseline": n_eligible,
        "n_excluded_low_confidence": excluded_low_confidence,
        "n_excluded_invalid": excluded_invalid,
        "current_confidence": current_confidence,
        "current_low_confidence": current_low_confidence,
        "confidence_threshold": _CONFIDENCE_THRESHOLD,
        "metrics": metrics,
        "versions": {
            "current": current_versions or get_versions(),
            "baseline": {k: sorted(v) for k, v in baseline_versions.items()},
            "cross_version": cross_version,
        },
    }


@router.post("/rerun-insights/{session_id}")
async def rerun_insights(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    ctx: UserContext = Depends(get_admin_or_user),
):
    """Recompute insight report from existing raw data only."""
    await get_owned_session(session_id, ctx, db)  # 404 gate
    try:
        from app.services.insight_engine.runner import run_for_session
        result = await run_for_session(session_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return {"code": 0, "data": result, "message": "ok"}


# ---------------------------------------------------------------------------
# DELETE — cascade delete session + children + MinIO video
# ---------------------------------------------------------------------------

@router.patch("/{session_id}/title")
async def rename_session(
    session_id: str,
    body: dict,
    db: AsyncSession = Depends(get_db),
    ctx: UserContext = Depends(get_admin_or_user),
):
    title = (body.get("title") or "").strip()
    if not title:
        raise HTTPException(status_code=422, detail="title cannot be empty")
    if len(title) > 200:
        raise HTTPException(status_code=422, detail="title too long (max 200)")
    session = await get_owned_session(session_id, ctx, db)
    session.title = title
    await db.commit()
    return {"id": session_id, "title": session.title}


@router.delete("/{session_id}")
async def delete_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    ctx: UserContext = Depends(get_admin_or_user),
):
    """Delete a session and all related data.
    
    Returns 409 Conflict only for locked (actively running) non-zombie sessions.
    Terminal states (done, analyzed, error, failed, analysis_failed, timeout) always allow deletion.
    Zombie sessions (stuck in locked state) are allowed deletion with a warning.
    """
    from datetime import datetime, timezone, timedelta
    from sqlalchemy import delete as sa_delete
    import logging
    logger = logging.getLogger(__name__)

    # ---- State definitions ----
    # Locked: actively running, guarded against deletion
    LOCKED_STATES = {"processing", "analyzing", "running"}
    # Any status NOT in LOCKED_STATES is considered terminal and always deletable.
    # Explicit terminal examples: done, analyzed, error, failed, analysis_failed, timeout, uploaded

    ZOMBIE_TIMEOUT = timedelta(hours=2)  # generous safety margin for stuck tasks

    session = await get_owned_session(session_id, ctx, db)

    if session.status in LOCKED_STATES:
        now = datetime.now(timezone.utc)
        if session.analysis_started_at and (now - session.analysis_started_at) > ZOMBIE_TIMEOUT:
            logger.warning(
                "Deleting zombie session %s (status=%s, stuck since %s, elapsed=%s)",
                session.id, session.status, session.analysis_started_at,
                now - session.analysis_started_at,
            )
        else:
            raise HTTPException(
                status_code=409,
                detail=f"Cannot delete session with status '{session.status}'. The session is currently being analyzed. Wait for completion or cancel first.",
            )

    # Delete MinIO objects if present (non-fatal). Both master and the
    # separate playback rendition (when transcoded) must be removed — but
    # only once if playback == master (passthrough case).
    _video_paths = {session.master_video_path, session.playback_video_path}
    _video_paths.discard(None)
    if _video_paths:
        from minio import Minio
        client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )
        for path in _video_paths:
            if "/" not in path:
                continue
            try:
                bucket, obj = path.split("/", 1)
                client.remove_object(bucket, obj)
                logger.info("Deleted MinIO object: %s/%s", bucket, obj)
            except Exception as e:
                logger.warning("Failed to delete MinIO object %s (non-fatal): %s", path, e)

    # Cascade delete children in order (respect FK constraints)
    await db.execute(sa_delete(Danmaku).where(Danmaku.session_id == session_id))
    await db.execute(sa_delete(ScriptSegment).where(ScriptSegment.session_id == session_id))
    await db.execute(sa_delete(PipelineStep).where(PipelineStep.session_id == session_id))
    await db.delete(session)

    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise HTTPException(status_code=500, detail="Failed to delete session")

    return {"code": 0, "data": {"session_id": session_id}, "message": "deleted"}
