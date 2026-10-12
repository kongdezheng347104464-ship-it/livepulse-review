"""Live stream rhythm analyzer.

Analyzes the timing and sequence of script type phases in a live stream.
Detects: opening, teaching blocks, product cycles, interaction peaks,
and overall rhythm quality.
"""
from collections import Counter
import logging

logger = logging.getLogger(__name__)


def _merge_phases(segments: list[dict]) -> list[dict]:
    """Merge consecutive segments of the same script_type into phases.

    Args:
        segments: List of segment dicts with start_sec, end_sec, script_type.

    Returns:
        List of phase dicts: {type, start_sec, end_sec, duration_sec, segment_count}.
    """
    if not segments:
        return []

    phases = []
    current_type = segments[0].get("script_type", "chitchat")
    current_start = segments[0]["start_sec"]
    current_end = segments[0]["end_sec"]
    current_count = 1

    for s in segments[1:]:
        s_type = s.get("script_type", "chitchat")
        # Schema integrity check: every segment MUST have script_type
        if "script_type" not in s:
            logger.warning(
                "[rhythm] segment missing script_type, falling back to 'chitchat'. "
                "This indicates upstream schema pollution. segment keys: %s",
                list(s.keys())[:10]
            )
        if s_type == current_type:
            current_end = max(current_end, s["end_sec"])
            current_count += 1
        else:
            phases.append({
                "type": current_type,
                "start_sec": current_start,
                "end_sec": current_end,
                "duration_sec": round(current_end - current_start, 1),
                "segment_count": current_count,
            })
            current_type = s_type
            current_start = s["start_sec"]
            current_end = s["end_sec"]
            current_count = 1

    # Last phase
    phases.append({
        "type": current_type,
        "start_sec": current_start,
        "end_sec": current_end,
        "duration_sec": round(current_end - current_start, 1),
        "segment_count": current_count,
    })

    return phases


def _compute_type_distribution(phases: list[dict], total_duration: int) -> dict:
    """Compute duration distribution by script type."""
    type_durations = Counter()
    type_counts = Counter()
    for p in phases:
        type_durations[p["type"]] += p["duration_sec"]
        type_counts[p["type"]] += 1

    total = max(total_duration, 1)
    return {
        t: {
            "count": type_counts[t],
            "total_duration_sec": round(type_durations[t], 1),
            "pct": round(type_durations[t] / total * 100, 1),
        }
        for t in type_durations
    }


def _compute_phase_switches(phases: list[dict]) -> list[dict]:
    """Track type-to-type transitions."""
    switches = []
    prev_type = None
    for p in phases:
        switches.append({
            "from": prev_type,
            "to": p["type"],
            "at_sec": p["start_sec"],
        })
        prev_type = p["type"]
    return switches


def _generate_recommendations(
    phases: list[dict],
    distribution: dict,
    total_duration: int,
) -> list[str]:
    """Generate rhythm improvement suggestions."""
    recs = []

    # Check opening
    if phases and phases[0]["type"] not in ("interaction", "teaching", "product_intro"):
        recs.append("开场建议使用互动或教学话术快速吸引观众")

    # Check teaching ratio
    teaching_data = distribution.get("teaching", {})
    teaching_pct = teaching_data.get("pct", 0)
    if teaching_pct < 10:
        recs.append(f"教学话术仅占{teaching_pct}%，建议增加化妆技巧教学内容")
    elif teaching_pct > 70:
        recs.append(f"教学话术占比{teaching_pct}%较高，可适当穿插产品介绍和促销")

    # Check promotion ratio
    promo_data = distribution.get("promotion", {})
    promo_pct = promo_data.get("pct", 0)
    if promo_pct < 3:
        recs.append("促单话术偏少，建议在讲解产品后增加逼单环节")

    # Check interaction frequency
    inter_data = distribution.get("interaction", {})
    inter_pct = inter_data.get("pct", 0)
    if inter_pct < 2:
        recs.append("互动话术偏少，建议多引导观众扣1、评论互动")

    # Check phase switching frequency
    avg_phase_duration = total_duration / max(len(phases), 1)
    if avg_phase_duration > 600:
        recs.append(f"话术切换频率偏低（平均每{avg_phase_duration:.0f}秒切换一次），建议加快节奏")
    elif avg_phase_duration < 30:
        recs.append("话术切换过于频繁，建议保持每个话题2-3分钟以上")

    if not recs:
        recs.append("直播节奏整体良好")

    return recs


def analyze_rhythm(segments: list[dict], total_duration_sec: int) -> dict:
    """Analyze the rhythm of a live stream.

    Args:
        segments: List of segment dicts with start_sec, end_sec, script_type.
        total_duration_sec: Total duration of the stream in seconds.

    Returns:
        Dict with phases, transitions, distribution, metrics, and recommendations.
    """
    if not segments:
        return {
            "total_duration_sec": total_duration_sec,
            "phase_transitions": 0,
            "phases": [],
            "recommendations": ["暂无数据"],
        }

    phases = _merge_phases(segments)
    distribution = _compute_type_distribution(phases, total_duration_sec)
    switches = _compute_phase_switches(phases)
    recommendations = _generate_recommendations(phases, distribution, total_duration_sec)

    return {
        "total_duration_sec": total_duration_sec,
        "phase_transitions": max(len(switches) - 1, 0),
        "avg_phase_duration_sec": round(
            total_duration_sec / max(len(phases), 1), 1
        ),
        "phases": phases,
        "phase_switches": switches,
        "type_distribution": distribution,
        "recommendations": recommendations,
    }
