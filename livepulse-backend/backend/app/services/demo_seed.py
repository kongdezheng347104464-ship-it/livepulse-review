"""Demo-mode seed (DEMO_MODE=1).

Lets anyone run LivePulse without any cloud API key: at startup it writes a
small set of *fully synthetic*, already-analysed live sessions plus a short
generated sample video, so the whole UI (dashboard, review workspace,
transcript, insights, highlights, trend) can be browsed.

Everything here is invented — the anchors, scripts, audience curves and scores
are not derived from any real streamer or platform data. Nothing in this module
calls OCR / ASR / LLM services.

Layout:
  * build_demo_dataset()  — pure function, no I/O (unit-tested)
  * ensure_demo_seeded()  — idempotent startup hook: video + DB rows
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from decimal import Decimal

logger = logging.getLogger(__name__)

DEMO_PLATFORM = "demo"
_BASE_SEC = 180            # 下方样例数据按 3 分钟时间轴书写
DEMO_DURATION_SEC = 660    # 实际场次 11 分钟:趋势接口只统计 >=10 分钟的场次
_K = DEMO_DURATION_SEC / _BASE_SEC
_SNAPSHOT_STEP_SEC = 15
_VIDEO_OBJECT = "demo/demo-sample.mp4"

# (anchor key, display name, followers)
_ANCHORS = [
    ("xiaoman", "演示主播·小满", 12800),
    ("aye", "演示主播·阿野", 5600),
]

# Each session: 12 script lines spread evenly over the whole timeline.
# Times below are on a 180s base and scaled by _K when the dataset is built.
# line = (script_type, text, score)
_SESSIONS = [
    {
        "anchor": "xiaoman",
        "title": "春季护肤专场（示例）",
        "days_ago": 9,
        "composite": 62.0, "engagement": 58.0, "conversion": 55.0, "content": 73.0,
        "gmv": "3860.00", "orders": 41, "followers_gain": 120, "danmaku": 380,
        "peak_at": 95,
        "lines": [
            ("chitchat", "大家好呀，欢迎来到直播间，先跟我打个招呼吧。", 48),
            ("chitchat", "今天主要聊换季护肤，皮肤爱出油或者泛红的朋友注意听。", 52),
            ("teaching", "第一步是清洁，水温不要太烫，三十度左右最合适。", 66),
            ("teaching", "第二步是补水，爽肤水用手拍开，不要用力搓。", 64),
            ("product_intro", "接下来给大家看一下这款保湿精华，质地比较轻薄。", 70),
            ("product_intro", "主要成分是神经酰胺和透明质酸，适合敏感肌。", 72),
            ("promotion", "今天直播间下单立减二十，前五十名再送小样。", 78),
            ("promotion", "库存只有两百份，卖完就恢复原价，需要的朋友点链接。", 83),
            ("interaction", "有朋友问干皮能不能用，可以的，叠加面霜效果更好。", 61),
            ("interaction", "还有人问孕妇能不能用，这个建议先咨询医生。", 57),
            ("product_intro", "再看一下防晒，这款是物理加化学的复合型。", 63),
            ("chitchat", "好的，今天先到这里，谢谢大家陪我，我们下次见。", 45),
        ],
        "highlights": [
            (90, 120, 82.0, "限时优惠话术出现后在线人数明显拉升，下单意向弹幕集中出现"),
            (60, 75, 64.0, "产品卖点讲解期间互动稳定"),
        ],
        "insights": [
            ("{t} 优惠话术带动购买意向", "限时优惠和库存提示同时出现，这一段是全场转化最强的区间。", "conversion", "high", 82, (90, 120)),
            ("{t} 开场互动偏弱", "开场只做了打招呼，没有抛出钩子，开场阶段在线人数增长缓慢。", "engagement", "medium", 48, (0, 30)),
            ("{t} 收尾直接下播", "结尾没有预告下一场或引导关注，错过了沉淀粉丝的时机。", "content", "low", 45, (150, 180)),
        ],
        "recommendations": [
            ("conversion", "high", "把优惠信息提前到产品介绍之后立刻说，不要等到后半场", "本场优惠话术出现的时间点，正是在线人数峰值前后"),
            ("engagement", "medium", "开场 30 秒内抛一个互动问题，例如让观众扣 1 选肤质", "开场互动弹幕数明显低于中段"),
        ],
        "notes": [(95, "优惠话术节奏不错，下一场开场就用这一句。")],
    },
    {
        "anchor": "xiaoman",
        "title": "夏日防晒专场（示例）",
        "days_ago": 3,
        "composite": 78.0, "engagement": 76.0, "conversion": 74.0, "content": 84.0,
        "gmv": "6240.00", "orders": 72, "followers_gain": 260, "danmaku": 640,
        "peak_at": 60,
        "lines": [
            ("interaction", "宝子们晚上好，先扣个 1，让我知道你们是什么肤质。", 74),
            ("interaction", "好多油皮的朋友，今天这一场就是专门讲你们的。", 76),
            ("teaching", "防晒有三个误区，第一个是阴天不用涂，其实紫外线照样穿透。", 79),
            ("teaching", "第二个误区是涂一次管一天，出汗之后两小时就要补涂。", 77),
            ("product_intro", "这款防晒是清爽质地，上脸不搓泥，我现场给大家试色。", 82),
            ("product_intro", "SPF50 加 PA 四个加，通勤和户外都够用。", 80),
            ("promotion", "今晚直播间专属价，比日常便宜三十，限量三百份。", 85),
            ("promotion", "倒数十个数，我们现在就上链接，三、二、一。", 88),
            ("interaction", "有人问敏感肌能不能用，我自己用了三年，没有问题。", 73),
            ("interaction", "好，大家的问题我都看到了，再回答最后两个。", 70),
            ("product_intro", "补充一下，卸妆一定要用卸妆油，防晒才能洗干净。", 72),
            ("interaction", "下一场我们讲秋季保湿，今天关注的朋友优先提醒。", 78),
        ],
        "highlights": [
            (90, 120, 88.0, "倒数上链接的节奏带来全场最高转化，弹幕刷屏"),
            (60, 75, 80.0, "现场试色拉高停留时长"),
            (0, 30, 74.0, "开场用扣 1 互动，在线人数起步快"),
        ],
        "insights": [
            ("{t} 倒数上链接带来峰值转化", "倒数加限量提示的组合效果明显优于上一场的纯口头优惠。", "conversion", "high", 88, (90, 120)),
            ("{t} 开场互动明显改善", "用扣 1 的方式开场，相比上一场开场互动弹幕增长显著。", "engagement", "medium", 74, (0, 30)),
            ("{t} 预告下一场", "结尾预告了下一场主题并引导关注，新增粉丝高于上一场。", "content", "low", 78, (150, 180)),
        ],
        "recommendations": [
            ("conversion", "high", "继续使用倒数加限量的上链接方式，并尝试在中段再做一次", "本场峰值转化出现在倒数之后"),
            ("content", "low", "把防晒三个误区整理成固定开场片段，方便复用", "教学类片段的停留表现稳定"),
        ],
        "notes": [(90, "倒数上链接的话术可以做成固定模板。"), (60, "现场试色的镜头角度很好，保持。")],
    },
    {
        "anchor": "aye",
        "title": "周末露营装备分享（示例）",
        "days_ago": 5,
        "composite": 70.0, "engagement": 71.0, "conversion": 62.0, "content": 77.0,
        "gmv": "2480.00", "orders": 29, "followers_gain": 85, "danmaku": 290,
        "peak_at": 120,
        "lines": [
            ("chitchat", "兄弟们好，今天带大家看看周末露营要带哪些东西。", 55),
            ("teaching", "第一件事是选营地，离水源近但不要扎在河滩上。", 68),
            ("teaching", "帐篷要选双层的，防雨和防结露都靠外面这一层。", 70),
            ("product_intro", "这顶帐篷三分钟能搭好，我现场给大家演示一遍。", 76),
            ("product_intro", "重量只有两公斤出头，背着走一公里也不累。", 72),
            ("interaction", "有朋友问能不能抗风，六级风以内没问题，要多打地钉。", 69),
            ("teaching", "睡袋要看舒适温度，不要只看极限温度。", 67),
            ("product_intro", "这款睡袋舒适温度五度，春秋季完全够用。", 71),
            ("promotion", "今天帐篷加睡袋组合价，比单买便宜一百五。", 74),
            ("promotion", "只有一百套，想去露营的朋友现在就可以拍。", 73),
            ("interaction", "好多人问有没有炉具，下次专门做一期做饭。", 64),
            ("chitchat", "谢谢大家，周末玩得开心，注意安全。", 50),
        ],
        "highlights": [
            (60, 90, 76.0, "现场搭帐篷演示带来互动高峰"),
            (120, 150, 72.0, "组合价提示后下单意向上升"),
        ],
        "insights": [
            ("{t} 现场演示拉高互动", "搭帐篷的实时演示是本场互动最强的片段，评论里出现大量追问。", "engagement", "high", 76, (60, 90)),
            ("{t} 组合价转化一般", "组合价说得比较平，缺少紧迫感，转化低于互动热度。", "conversion", "medium", 62, (120, 150)),
        ],
        "recommendations": [
            ("conversion", "medium", "组合价加一个具体的限时节点，例如整点前下单赠送地钉", "互动热度高但转化没有跟上"),
        ],
        "notes": [(75, "搭帐篷这段可以单独剪成短视频。")],
    },
]


def _sc(sec: float) -> int:
    return int(round(sec * _K))


def _mmss(sec: int) -> str:
    return f"{sec // 60}:{sec % 60:02d}"


def _viewer_curve(peak_at: int, base: int, peak: int) -> list[tuple[int, int, int]]:
    """Deterministic audience curve: (timestamp_sec, viewer_count, like_count)."""
    pts = []
    likes = 0
    for t in range(0, DEMO_DURATION_SEC + 1, _SNAPSHOT_STEP_SEC):
        bump = math.exp(-((t - peak_at) ** 2) / (2 * (28.0 * _K) ** 2))
        ramp = min(1.0, 0.55 + t / (120.0 * _K))
        viewers = int(base * ramp + (peak - base) * bump)
        likes += int(viewers * 0.35) + 20
        pts.append((t, viewers, likes))
    return pts


def _intent_funnel(total: int, conversion: float) -> dict:
    """Audience-intent funnel the report page reads (semantic.intent_funnel).

    Strong-purchase share rises with the conversion score so the page's headline
    ("转化效率高/一般") agrees with the scores shown next to it.
    """
    strong = round(conversion / 4.5, 1)
    hesitation, comparison = 18.0, 12.0
    consideration = round(max(8.0, 100 - 40.0 - hesitation - comparison - strong), 1)
    interaction = round(100 - hesitation - comparison - consideration - strong, 1)
    shares = {
        "interaction": interaction, "hesitation": hesitation, "comparison": comparison,
        "purchase_consideration": consideration, "strong_purchase": strong,
    }
    return {k: {"count": int(round(total * v / 100)), "pct": v} for k, v in shares.items()}


def build_demo_dataset(now: datetime | None = None) -> dict:
    """Return the full synthetic dataset as plain dicts. Pure; no I/O."""
    now = now or datetime.now(timezone.utc)
    anchors = [{"key": k, "name": n, "followers": f} for k, n, f in _ANCHORS]
    sessions = []
    for spec in _SESSIONS:
        start = (now - timedelta(days=spec["days_ago"])).replace(
            hour=20, minute=0, second=0, microsecond=0)
        base = 300 if spec["composite"] < 70 else 420
        peak = base + (260 if spec["composite"] >= 70 else 180)
        curve = _viewer_curve(_sc(spec["peak_at"]), base, peak)
        viewers = [c[1] for c in curve]

        segments = []
        n = len(spec["lines"])
        for i, (stype, text, score) in enumerate(spec["lines"]):
            segments.append({
                "start_sec": round(i * DEMO_DURATION_SEC / n),
                "end_sec": round((i + 1) * DEMO_DURATION_SEC / n),
                "text": text, "script_type": stype, "score": float(score),
            })

        highlights = [
            {"start_sec": _sc(s), "end_sec": _sc(e), "score": sc,
             "time": _mmss(_sc(s)), "type": "conversion_spike",
             "reason": reason}
            for s, e, sc, reason in spec["highlights"]
        ]
        insights = [
            {"text": text.format(t=f"{_mmss(_sc(a))}-{_mmss(_sc(b))}"), "claim": claim,
             "impact": impact, "priority": prio, "score": float(score),
             "time_range": {"start_sec": _sc(a), "end_sec": _sc(b)}}
            for text, claim, impact, prio, score, (a, b) in spec["insights"]
        ]
        recs = [
            {"category": cat, "priority": prio, "action": action, "reason": reason}
            for cat, prio, action, reason in spec["recommendations"]
        ]
        summary = {
            "composite_score": spec["composite"],
            "engagement_score": spec["engagement"],
            "conversion_score": spec["conversion"],
            "content_score": spec["content"],
            "scores": {
                "composite": spec["composite"], "engagement": spec["engagement"],
                "conversion": spec["conversion"], "content": spec["content"],
            },
            "normalized_metrics": {
                "avg_viewer_count": sum(viewers) / len(viewers),
                "engaged_audience_rate": round(spec["engagement"] / 400, 3),
                "purchase_intent_per_viewer": round(spec["conversion"] / 900, 3),
                "strong_purchase_count": spec["orders"],
                "unique_speakers": 1,
            },
            "semantic": {
                "intent_funnel": _intent_funnel(spec["danmaku"], spec["conversion"]),
            },
            "insight_report": {
                "generated_at": now.isoformat(),
                "summary": {
                    "overall_score": spec["composite"],
                    "engagement_score": spec["engagement"],
                    "conversion_score": spec["conversion"],
                    "content_score": spec["content"],
                    "duration": _mmss(DEMO_DURATION_SEC),
                },
                "insights": insights,
                "recommendations": recs,
                "timeline_highlights": highlights,
            },
            "demo": True,
        }
        sessions.append({
            "anchor": spec["anchor"],
            "title": spec["title"],
            "start_time": start,
            "end_time": start + timedelta(seconds=DEMO_DURATION_SEC),
            "duration_sec": DEMO_DURATION_SEC,
            "viewer_peak": max(viewers),
            "viewer_avg": int(sum(viewers) / len(viewers)),
            "total_danmaku": spec["danmaku"],
            "total_gmv": Decimal(spec["gmv"]),
            "total_orders": spec["orders"],
            "new_followers": spec["followers_gain"],
            "composite_score": Decimal(str(spec["composite"])),
            "analysis_summary": summary,
            "segments": segments,
            "viewer_snapshots": curve,
            "notes": [{"timestamp_sec": _sc(t), "note_text": n} for t, n in spec["notes"]],
        })
    return {"anchors": anchors, "sessions": sessions}


def _generate_sample_video(path: str) -> None:
    """11-minute portrait test-pattern clip with a running timestamp + tone."""
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    cmd = [
        ffmpeg, "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc2=size=270x480:rate=10",
        "-f", "lavfi", "-i", "sine=frequency=330:sample_rate=44100",
        "-t", str(DEMO_DURATION_SEC),
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "32", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "48k", "-movflags", "+faststart", path,
    ]
    subprocess.run(cmd, check=True, timeout=300)


def _ensure_sample_video(bucket: str) -> str | None:
    """Create + upload the sample video once. Returns "bucket/object" or None."""
    from app.api.workspace import _minio_client

    client = _minio_client(public=False)
    try:
        client.stat_object(bucket, _VIDEO_OBJECT)
        return f"{bucket}/{_VIDEO_OBJECT}"
    except Exception:
        pass
    try:
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "demo-sample.mp4")
            _generate_sample_video(out)
            client.fput_object(bucket, _VIDEO_OBJECT, out, content_type="video/mp4")
        return f"{bucket}/{_VIDEO_OBJECT}"
    except Exception as e:  # ffmpeg/MinIO missing: demo still works, minus video
        logger.warning("[demo-seed] sample video unavailable (%s) — sessions will have no video", e)
        return None


async def ensure_demo_seeded() -> None:
    """Idempotent: seed synthetic data when DEMO_MODE=1. No-op otherwise."""
    from sqlalchemy import select

    from app.config import get_settings
    from app.database import async_session_factory
    from app.models import (
        Anchor, LiveSession, ScriptSegment, SessionNote, User, ViewerSnapshot,
    )

    settings = get_settings()
    if not settings.demo_mode:
        return

    async with async_session_factory() as db:
        owner = (await db.execute(
            select(User).where(User.role == "admin"))).scalars().first()
        if owner is None:
            logger.warning("[demo-seed] no admin user (set ADMIN_EMAIL/ADMIN_PASSWORD) — skipped")
            return
        already = (await db.execute(
            select(Anchor).where(Anchor.owner_user_id == owner.id,
                                 Anchor.platform == DEMO_PLATFORM))).scalars().first()
        if already:
            logger.info("[demo-seed] demo data already present — skipped")
            return

        video_path = await asyncio.to_thread(_ensure_sample_video, settings.minio_bucket)
        data = build_demo_dataset()

        anchor_ids: dict[str, str] = {}
        for a in data["anchors"]:
            row = Anchor(owner_user_id=owner.id, name=a["name"], canonical_name=a["name"],
                         platform=DEMO_PLATFORM, followers=a["followers"])
            db.add(row)
            await db.flush()
            anchor_ids[a["key"]] = row.id

        for s in data["sessions"]:
            sess = LiveSession(
                owner_user_id=owner.id, anchor_id=anchor_ids[s["anchor"]],
                platform=DEMO_PLATFORM, title=s["title"],
                start_time=s["start_time"], end_time=s["end_time"],
                duration_sec=s["duration_sec"], viewer_peak=s["viewer_peak"],
                viewer_avg=s["viewer_avg"], total_danmaku=s["total_danmaku"],
                total_gmv=s["total_gmv"], total_orders=s["total_orders"],
                new_followers=s["new_followers"], composite_score=s["composite_score"],
                analysis_summary=s["analysis_summary"], status="analyzed",
                master_video_path=video_path,
            )
            db.add(sess)
            await db.flush()
            for seg in s["segments"]:
                db.add(ScriptSegment(session_id=sess.id, **seg))
            for t, viewers, likes in s["viewer_snapshots"]:
                db.add(ViewerSnapshot(session_id=sess.id, timestamp_sec=t,
                                      viewer_count=viewers, like_count=likes))
            for n in s["notes"]:
                db.add(SessionNote(owner_user_id=owner.id, session_id=sess.id, **n))
        await db.commit()
        logger.info("[demo-seed] seeded %d anchors, %d sessions",
                    len(data["anchors"]), len(data["sessions"]))
