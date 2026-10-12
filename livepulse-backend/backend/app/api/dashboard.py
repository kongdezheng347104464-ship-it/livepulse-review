"""Dashboard 聚合接口（首页"复盘中控台"喂数接口）。

一次请求返回首页所需的全部聚合，服务端算好，避免客户端 N+1（尤其"可
导出高光总数"——客户端要对每个场次单独拉 /highlights，服务端一条 JSONB
聚合就出）。全部 owner 隔离（admin 看全部），read-only，不动数据模型。

GET /api/v1/dashboard
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.anchor import Anchor
from app.models.pipeline_step import PipelineStep
from app.models.session import LiveSession
from app.services.auth.current_user import UserContext, get_admin_or_user
from app.services.ownership import apply_owner_scope

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

# 分析中场次不计入这些"终态"
_TERMINAL_STATUS = ("analyzed", "error", "failed", "done")

# pipeline_step.step_name → 用户能看懂的阶段文案（呼应"系统可以慢但必须活着"）
_STAGE_LABELS = {
    "video_processing": "视频预处理",
    "asr": "语音转文字",
    "ocr": "画面识别",
    "danmaku_analysis": "弹幕识别",
    "script_analysis": "话术分析",
    "correlation": "关联分析",
    "scoring": "评分",
    "insight_engine": "生成洞察",
    "early_analysis": "早期分析",
    "viewer_count": "在线人数",
    "playback_encode": "转码",
}

# 高光"可分享"阈值，与 workspace.py 高光接口一致（ADR §3）
_SHAREABLE_SCORE = 60


@router.get("")
async def get_dashboard(
    db: AsyncSession = Depends(get_db),
    ctx: UserContext = Depends(get_admin_or_user),
):
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    in_progress = await _in_progress(db, ctx)
    this_month = await _this_month(db, ctx, month_start)
    exportable = await _exportable_highlights(db, ctx)
    recent = await _recent_reports(db, ctx)
    anchors = await _frequent_anchors(db, ctx)

    return {
        "code": 0,
        "data": {
            "in_progress": in_progress,
            "this_month": this_month,
            "exportable_highlights": exportable,
            "recent_reports": recent,
            "frequent_anchors": anchors,
        },
        "message": "ok",
    }


# ---------------------------------------------------------------------------


async def _in_progress(db: AsyncSession, ctx: UserContext) -> list[dict]:
    """非终态场次 + 当前阶段文案。空 → []（首页显示"暂无分析中"）。"""
    stmt = (
        select(
            LiveSession.id, LiveSession.title, LiveSession.status,
            Anchor.name.label("anchor_name"),
        )
        .join(Anchor, Anchor.id == LiveSession.anchor_id, isouter=True)
        .where(LiveSession.status.notin_(_TERMINAL_STATUS))
        .order_by(LiveSession.created_at.desc())
        .limit(10)
    )
    rows = (await db.execute(apply_owner_scope(stmt, LiveSession, ctx))).all()
    if not rows:
        return []

    # 当前阶段 = 正在 running 的 step（一次查完所有相关 step，避免 N 次）
    ids = [r.id for r in rows]
    running = dict(
        (await db.execute(
            select(PipelineStep.session_id, PipelineStep.step_name)
            .where(
                PipelineStep.session_id.in_(ids),
                PipelineStep.status == "running",
            )
        )).all()
    )

    return [
        {
            "session_id": r.id,
            "title": r.title,
            "anchor_name": r.anchor_name,
            "status": r.status,
            "stage": _STAGE_LABELS.get(running.get(r.id), "分析中"),
        }
        for r in rows
    ]


async def _this_month(db: AsyncSession, ctx: UserContext, month_start: datetime) -> dict:
    """本月已分析场次数 + 本月均分（本月无 → avg_score=None → 前端显示"—"）。"""
    stmt = (
        select(
            func.count(LiveSession.id).filter(LiveSession.created_at >= month_start),
            func.avg(LiveSession.composite_score).filter(LiveSession.created_at >= month_start),
        )
        .where(LiveSession.status == "analyzed")
    )
    row = (await db.execute(apply_owner_scope(stmt, LiveSession, ctx))).one()
    return {
        "review_count": row[0] or 0,
        "avg_score": round(float(row[1]), 1) if row[1] is not None else None,
    }


async def _exportable_highlights(db: AsyncSession, ctx: UserContext) -> int:
    """可导出高光总数（score>=60）。JSONB 聚合，owner 内联过滤（text 用不了
    apply_owner_scope，故用绑定参数复刻其语义：admin 看全部）。"""
    sql = text("""
        SELECT count(*)
        FROM live_sessions s,
             jsonb_array_elements(
                 s.analysis_summary->'insight_report'->'timeline_highlights'
             ) h
        WHERE (h->>'score')::numeric >= :min_score
          AND (:is_admin OR s.owner_user_id = :uid)
    """)
    res = await db.execute(sql, {
        "min_score": _SHAREABLE_SCORE,
        "is_admin": ctx.role == "admin",
        "uid": ctx.id,
    })
    return res.scalar() or 0


async def _recent_reports(db: AsyncSession, ctx: UserContext) -> list[dict]:
    """最近 5 场已分析。"""
    stmt = (
        select(
            LiveSession.id, LiveSession.title, LiveSession.composite_score,
            LiveSession.start_time, LiveSession.created_at,
            LiveSession.analysis_finished_at,
            Anchor.name.label("anchor_name"),
        )
        .join(Anchor, Anchor.id == LiveSession.anchor_id, isouter=True)
        .where(LiveSession.status == "analyzed")
        .order_by(
            func.coalesce(
                LiveSession.analysis_finished_at, LiveSession.created_at
            ).desc()
        )
        .limit(5)
    )
    rows = (await db.execute(apply_owner_scope(stmt, LiveSession, ctx))).all()
    return [
        {
            "session_id": r.id,
            "title": r.title,
            "anchor_name": r.anchor_name,
            "date": (r.start_time or r.created_at).isoformat() if (r.start_time or r.created_at) else None,
            "composite_score": float(r.composite_score) if r.composite_score is not None else None,
        }
        for r in rows
    ]


async def _frequent_anchors(db: AsyncSession, ctx: UserContext) -> list[dict]:
    """按场次数倒序 top 5 主播。"""
    stmt = (
        select(
            Anchor.id, Anchor.name, Anchor.avatar_url,
            func.count(LiveSession.id).label("session_count"),
        )
        .outerjoin(LiveSession, LiveSession.anchor_id == Anchor.id)
        .group_by(Anchor.id, Anchor.name, Anchor.avatar_url)
        .order_by(func.count(LiveSession.id).desc(), Anchor.name)
        .limit(5)
    )
    rows = (await db.execute(apply_owner_scope(stmt, Anchor, ctx))).all()
    return [
        {
            "id": r.id,
            "name": r.name,
            "avatar_url": r.avatar_url,
            "session_count": r.session_count,
        }
        for r in rows
    ]
