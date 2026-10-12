"""Early signals extractor — pure objective measurements from script-only data.

Per B5 真分层 设计（2026-05-30 与 owner 多轮收敛）：
  - 这一层 ONLY 产出客观数字，**绝对不带判断** (no "high/low/good/bad")
  - 任何 status / severity / target_range 的判断都在 early_insight_generator
    里基于 threshold_set 算出，不在这里
  - B4 Anchor Dashboard 直接复用这层数据做跨场聚合（"最近 10 场平均
    hook_score"），所以 signal 必须语义稳定可比

7 个 signal 全部可由 analyzer.py 的输出 (analyzed_segments) 算出，
**不依赖** OCR / 弹幕 / embedding tactics。这是 _step_early_analysis
能紧跟 _step_script_analysis 立刻 fire 的根本原因。

禁止扩展规则：如果未来想加涉及 interaction / 转化 / 成交 / 留存 /
观众质量 / 弹幕情绪 的 signal —— 那是 full insight 的事，不能加到
early。详见 docs/intent-classification-roadmap.md 第 5 守则。
"""

from __future__ import annotations


def extract_early_signals(
    analyzed_segments: list[dict],
    session_duration_sec: float | None,
) -> dict:
    """Compute 7 script-only early signals.

    Args:
        analyzed_segments: Output of script_analyzer.analyze (each dict has
            start_sec, end_sec, script_type, analysis_tags).
        session_duration_sec: Total session length. Used by pace_score; if
            None we fall back to (max end_sec - min start_sec).

    Returns:
        dict with 7 fields. All are floats (0-1 ratios) or scaled integers
        (0-100 scores). None ONLY for signals that genuinely can't be
        computed (e.g., empty segments).
    """
    if not analyzed_segments:
        return _empty_signals()

    # Explicit None → fall back to inferred. Explicit 0 → treat as
    # invalid (caller said "this session has zero duration"; we honor
    # that and return zeroed signals rather than silently re-inferring).
    if session_duration_sec is None:
        total_duration = _infer_duration(analyzed_segments)
    else:
        total_duration = session_duration_sec
    if total_duration <= 0:
        return _empty_signals()

    return {
        "hook_score":             _compute_hook_score(analyzed_segments),
        "teaching_ratio":         _compute_type_ratio(analyzed_segments, "teaching", total_duration),
        "product_intro_ratio":    _compute_type_ratio(analyzed_segments, "product_intro", total_duration),
        "promotion_density":      _compute_type_ratio(analyzed_segments, "promotion", total_duration),
        "pain_point_coverage":    _compute_pain_point_coverage(analyzed_segments),
        "topic_stability_score":  _compute_topic_stability(analyzed_segments),
        "pace_score":             _compute_pace_score(analyzed_segments, total_duration),
    }


# ----- Individual signal functions -----

def _compute_hook_score(segments: list[dict]) -> int:
    """0-100. Did the broadcaster open with a value-prop hook within 60s?

    100 if is_opening starts strictly before 30s mark
     60 if is_opening starts at 30s ≤ start ≤ 60s
      0 if no opening detected within first 60s

    Rationale: small streamers losing viewers in 0-60s is the biggest
    funnel leak. Detecting whether the host bothered to deliver any hook
    at all is a proxy for "did they take retention seriously".
    """
    for seg in segments:
        start = seg.get("start_sec", 0)
        if start > 60:
            break
        if seg.get("analysis_tags", {}).get("is_opening"):
            return 100 if start < 30 else 60
    return 0


def _compute_type_ratio(segments: list[dict], target_type: str, total_duration: float) -> float:
    """0.0-1.0. What fraction of session time was spent in this script_type?"""
    if total_duration <= 0:
        return 0.0
    matched_dur = 0.0
    for seg in segments:
        if seg.get("script_type") == target_type:
            matched_dur += max(0, seg.get("end_sec", 0) - seg.get("start_sec", 0))
    return round(matched_dur / total_duration, 4)


def _compute_pain_point_coverage(segments: list[dict]) -> int:
    """Count of distinct pain points mentioned across all segments.

    Dedupes by string. "斑驳" mentioned 5 times counts once. This measures
    "did the host address multiple user concerns" not "how often they
    repeat the same one".
    """
    seen: set[str] = set()
    for seg in segments:
        for pp in seg.get("analysis_tags", {}).get("pain_points", []) or []:
            if pp:
                seen.add(pp.strip())
    return len(seen)


def _compute_topic_stability(segments: list[dict]) -> float:
    """0.0-1.0. 1 - (script_type changes between consecutive segments / total).

    1.0 = same script_type throughout (e.g., 100% teaching from start to end)
    0.0 = every segment switches type (chaotic)
    Typical healthy live: 0.5-0.7 (some switching, mostly coherent).

    Note: chitchat→chitchat doesn't count as a change. Only inter-type
    transitions matter.
    """
    if len(segments) <= 1:
        return 1.0
    changes = 0
    prev = segments[0].get("script_type")
    for seg in segments[1:]:
        curr = seg.get("script_type")
        if curr != prev:
            changes += 1
        prev = curr
    # n segments → at most n-1 transitions
    transitions_possible = len(segments) - 1
    return round(1.0 - (changes / transitions_possible), 4)


def _compute_pace_score(segments: list[dict], total_duration: float) -> int:
    """0-100. Average segment duration mapped to a 0-100 pace score.

    avg_duration >= 30s → 100 (sustained narrative, deep dives)
    avg_duration 15-30s → 60-100 (normal pace)
    avg_duration 8-15s  → 30-60  (slightly fragmented)
    avg_duration < 8s   → 0-30   (rapid topic switching)

    Why segment duration (not segments_per_min): ASR cuts at natural
    speech pauses, so segment length correlates with how long the host
    sustains a single point. Per-min count is noisier (depends on session
    length and pause distribution).
    """
    if not segments:
        return 0
    durations = [
        max(0, seg.get("end_sec", 0) - seg.get("start_sec", 0))
        for seg in segments
    ]
    avg = sum(durations) / len(durations) if durations else 0
    if avg >= 30:
        return 100
    if avg >= 15:
        # 15s → 60, 30s → 100 (linear)
        return int(60 + (avg - 15) * 40 / 15)
    if avg >= 8:
        # 8s → 30, 15s → 60 (linear)
        return int(30 + (avg - 8) * 30 / 7)
    # 0s → 0, 8s → 30 (linear)
    return int(avg * 30 / 8)


# ----- Helpers -----

def _empty_signals() -> dict:
    return {
        "hook_score": 0,
        "teaching_ratio": 0.0,
        "product_intro_ratio": 0.0,
        "promotion_density": 0.0,
        "pain_point_coverage": 0,
        "topic_stability_score": 0.0,
        "pace_score": 0,
    }


def _infer_duration(segments: list[dict]) -> float:
    """Fallback when session_duration_sec is None.

    Uses (max end_sec - min start_sec). Caller should prefer the
    authoritative session.duration_sec.
    """
    if not segments:
        return 0
    starts = [s.get("start_sec", 0) for s in segments]
    ends = [s.get("end_sec", 0) for s in segments]
    return max(ends) - min(starts)
