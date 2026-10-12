"""Action Recommender: insights → operational recommendations.

Generates specific, actionable advice for live stream optimization.
"""


def generate_recommendations(
    insights: list[dict],
    patterns: dict,
    summary: dict,
    duration_sec: float,
) -> list[dict]:
    """Generate operational recommendations.

    Returns:
        List of recommendation dicts with {priority, category, action, reason}.
    """
    recs = []

    # ---- Engagement recommendations ----
    eng = summary.get("engagement", {})
    rate = eng.get("prompts_per_minute", 0)
    if rate < 0.5:
        recs.append({
            "priority": "high",
            "category": "互动",
            "action": "每3分钟插入一次互动提问（如「扣1让我看看多少人想学」）",
            "reason": f"当前互动频率 {rate:.1f}次/分钟，低于建议的1次/分钟",
        })

    # ---- Conversion recommendations ----
    # Only emit when danmaku data is actually present — otherwise "未检测到
    # 转化窗口" might just mean "OCR didn't run", not "主播没逼单".
    completeness = summary.get("data_completeness", {})
    has_danmaku = completeness.get("danmaku") == "ok"
    conv = patterns.get("conversion_windows", [])
    if has_danmaku and not conv:
        closing_total = summary.get("closing", {}).get("total_tactics", 0) or 0
        if closing_total < 3:
            recs.append({
                "priority": "high",
                "category": "转化",
                "action": "在讲解完每个产品后增加逼单话术（限时/库存/价格对比）",
                "reason": f"全场逼单话术仅 {closing_total} 次，未观测到购买意向窗口",
            })

    # ---- Rhythm recommendations ----
    # phase_transitions == max(len(phases) - 1, 0), so the previous condition
    # `transitions > phases * 0.8 and phases > 5` was always true once phases>5
    # — every long session got the "switching too often" advice regardless of
    # actual pacing. The real signal is average phase duration: phases shorter
    # than 60s indicate genuinely choppy topic-hopping.
    rhythm = summary.get("rhythm", {})
    phases_list = rhythm.get("phases", [])
    phases = len(phases_list)
    avg_phase_dur = rhythm.get("avg_phase_duration_sec", 0) or 0
    if phases >= 8 and avg_phase_dur > 0 and avg_phase_dur < 60:
        recs.append({
            "priority": "medium",
            "category": "节奏",
            "action": "话题切换偏快，建议每个产品讲解保持 2-3 分钟",
            "reason": f"全场 {phases} 个阶段，平均每个阶段仅 {avg_phase_dur:.0f}s",
        })

    # ---- Chitchat reframe (C3, 2026-06-03) ----
    # When chitchat dominates, DON'T scold it. Heavy chitchat is the
    # 陪伴 (companionship) style of small/hobby streamers — it's how they
    # build 亲和力, not a defect (matches the v5.1 soft -0.2 coefficient
    # that stopped punishing it). Reframe as a strength with an actionable
    # upgrade path: convert companionship into conversion.
    type_dist = rhythm.get("type_distribution", {})
    chitchat_pct = (type_dist.get("chitchat", {}) or {}).get("pct", 0) or 0
    if chitchat_pct >= 50:
        recs.append({
            "priority": "medium",
            "category": "内容",
            "action": "每聊 5 分钟，自然过渡到 1 次产品介绍或互动提问",
            "reason": f"闲聊占比 {chitchat_pct:.0f}% 是你的陪伴风格、建立亲和力的方式，"
                      f"不是缺点；把陪伴顺势接到产品上就能转化",
        })

    # ---- Hot segments ----
    hot = patterns.get("hot_segments", [])
    if hot:
        best = max(hot, key=lambda h: h["danmaku"])
        recs.append({
            "priority": "medium",
            "category": "内容",
            "action": "在开场5分钟内复刻高光时段的话术模式",
            "reason": f"高光时段（弹幕{best['danmaku']}条）的内容策略值得复用",
        })

    # ---- Drop zones ----
    drops = patterns.get("drop_segments", [])
    if drops:
        recs.append({
            "priority": "medium",
            "category": "互动",
            "action": "在低互动时段插入福利预告或限时活动",
            "reason": f"检测到 {len(drops)} 个低互动区域",
        })

    # ---- Pain points ----
    pain = summary.get("pain_points", {})
    top_pps = pain.get("top_pain_points", [])
    if top_pps:
        recs.append({
            "priority": "high",
            "category": "内容",
            "action": f"针对「{top_pps[0]['keyword']}」等用户痛点设计专门的解决方案展示",
            "reason": f"{top_pps[0]['keyword']} 是最多提及的痛点（{top_pps[0]['count']}次）",
        })

    # Removed: hardcoded "baseline" recommendation that was appended on
    # every call regardless of data. It misled users into thinking the
    # advice came from their session's analysis. If we want generic best-
    # practice tips, surface them in a separate "教程" section of the UI,
    # not mixed with data-driven recommendations.

    return recs
