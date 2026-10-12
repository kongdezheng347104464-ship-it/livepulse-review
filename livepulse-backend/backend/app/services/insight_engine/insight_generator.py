"""Insight Generator v4 — Signal Interpretation Mode.

Uses ranking.py pipeline: filter → merge → score → diversify → narrate.
Insights are now compressed narratives, not per-window logs.
"""


def _fmt(sec: float) -> str:
    m, s = divmod(int(sec), 60)
    return f"{m}:{s:02d}"


def _lookup_script_text(script_segments: list[dict], start_sec: float, end_sec: float) -> str:
    """Look up original script text from segments (SSOT) within a time range.
    
    Returns concatenated text of all segments that overlap with [start_sec, end_sec].
    Falls back to empty string if no segments found.
    """
    texts = []
    for seg in script_segments:
        seg_start = seg.get("start_sec", 0)
        seg_end = seg.get("end_sec", seg_start)
        # Overlap check
        if seg_end >= start_sec and seg_start <= end_sec:
            text = seg.get("text", "")
            if text:
                texts.append(text.strip())
    return " ".join(texts)


def generate_insights(
    narratives: list[dict],
    summary: dict,
    patterns: dict,
    duration_sec: float,
    script_segments: list[dict] | None = None,
) -> list[dict]:
    """Generate narrative insights from ranked, merged signal windows.

    Schema contract (post-2026-05-27):
      * Every insight MUST have `scope: "segment" | "session"`.
      * `scope == "segment"` ⟹ `time_range` AND `script_text` REQUIRED.
      * `scope == "session"` ⟹ `time_range` MUST NOT be set (it would be
        meaningless — "whole session" isn't a time range).
      * Frontend consumers (conversion/engagement triggers) MUST filter
        by `scope == "segment"` before claiming "this is a specific
        moment". Failing to do so was the original bug where session-
        aggregate insights leaked into trigger cards with empty quotes.

    Previously this function also produced 5 session-scope insights
    (互动 N 次/分, 逼单 N 次, 购买意向漏斗, embedding coverage, drop zones).
    All 5 were redundant with other UI components (engagement/conversion
    scores, ConversionFunnel chart, tactic distribution panel, data_
    completeness banner). Removed to avoid "noise insights" — fewer
    truer signals beats more redundant ones.

    Args:
        script_segments: Original script segments (SSOT). Used to look up
            original transcript text for each insight — never re-generated.
    """
    if script_segments is None:
        script_segments = []
    insights = []
    meta = patterns.get("meta", {})
    dm_avg = meta.get("danmaku_avg", 0) or 0.1

    # ---- Narrative insights from ranked windows ----
    for n in narratives:
        t0 = _fmt(n["start_sec"])
        t1 = _fmt(n["end_sec"])
        dur = n.get("duration_sec", 30)

        if n["type"] == "conversion":
            title = f"{t0}-{t1} 触发购买意向 {n['purchase']} 条"
            claim = (
                f"{t0}-{t1}（{dur:.0f}s）逼单 {n['closing']} 次，"
                f"触发购买意向 {n['purchase']} 条"
            )
            # Look up original script text from segments (SSOT) — never re-generate
            script_text = _lookup_script_text(script_segments, n["start_sec"], n["end_sec"])
            insights.append({
                "text": title, "claim": claim,
                "script_text": script_text,
                "score": n["score"],
                "priority": "high" if n["score"] >= 80 else ("medium" if n["score"] >= 50 else "low"),
                "impact": "conversion",
                "scope": "segment",
                "time_range": {"start_sec": n["start_sec"], "end_sec": n["end_sec"]},
                "evidence": {
                    "purchase_intents": n["purchase"],
                    "closing_tactics": n["closing"],
                    "duration_sec": int(dur),
                    "dm_lift": f"{n['dm_lift']}x",
                },
                "related_events": [],
            })

        elif n["type"] == "engagement":
            dm = n["danmaku"]
            eng = n["engagement"]
            lift = n["dm_lift"]
            is_merged = (n["end_sec"] - n["start_sec"]) > 40

            if is_merged:
                title = f"{t0}-{t1} 持续互动高峰（{dur:.0f}s，弹幕 {dm} 条）"
                claim = (
                    f"{t0}-{t1} 为持续 {dur:.0f}s 的互动高峰，"
                    f"累计弹幕 {dm} 条，互动 {eng} 次，"
                    f"密度为全场平均的 {lift}x"
                )
            else:
                title = f"{t0}-{t1} 弹幕 {dm} 条（{lift}x 全场平均）"
                claim = (
                    f"{t0}-{t1} 弹幕 {dm} 条（全场平均 {dm_avg:.1f}条/30s），"
                    f"互动 {eng} 次"
                )

            # Look up original script text from segments (SSOT)
            script_text = _lookup_script_text(script_segments, n["start_sec"], n["end_sec"])
            insights.append({
                "text": title, "claim": claim,
                "script_text": script_text,
                "score": n["score"],
                "priority": "high" if n["score"] >= 80 else ("medium" if n["score"] >= 50 else "low"),
                "impact": "engagement",
                "scope": "segment",
                "time_range": {"start_sec": n["start_sec"], "end_sec": n["end_sec"]},
                "evidence": {
                    "danmaku": dm, "engagement": eng,
                    "lift": f"{lift}x", "duration_sec": int(dur),
                },
                "related_events": [],
            })

    # ---- Session-scope insights deliberately NOT generated ----
    #
    # Five session-scope insights used to be produced here:
    #   1. "互动引导 X次/分"      — redundant with scores.engagement card
    #   2. "逼单 N 次，主用 X"     — redundant with scores.conversion + tactic distribution panel
    #   3. "购买意向漏斗 N 条"     — ConversionFunnel chart shows the same data
    #   4. "语义模型覆盖 X%"      — belongs in data_completeness banner, not insights
    #   5. "检测到 N 个低互动区间" — already implied by scores.engagement; the range
    #                                spanned "from first drop to last drop" which is
    #                                misleading time information anyway
    #
    # Why removed (not just re-homed): every one of them was redundant
    # with information already visible in other UI components. The cost
    # of keeping them was real — they leaked into conversion trigger lists
    # without `script_text`, producing empty quote artifacts in the
    # "高转化话术证据链" drawer. See ANALYSIS_ENGINE_AUDIT.md "insight
    # semantic type leakage" for the full diagnosis.
    #
    # If a future genuine session-scope insight is needed (e.g. "you
    # repeated the same script type for 80% of the session"), it MUST
    # be emitted with `scope: "session"` and WITHOUT `time_range` /
    # `script_text`, and frontend consumers MUST filter accordingly.

    return insights
