"""Insight Engine v4 orchestrator.

Patterns → Ranking (filter/merge/score/diversify) → Insights → Recommendations.
"""
import logging

from .pattern_extractor import extract_patterns
from .ranking import interpret
from .insight_generator import generate_insights
from .action_recommender import generate_recommendations

_logger = logging.getLogger(__name__)


class InsightContractError(Exception):
    """An insight violated the segment/session scope contract.

    Insights are consumed by frontend components that branch on
    `scope`: segment-scope drives the conversion-trigger / engagement-
    trigger cards (which dereference `script_text` and `time_range`),
    while session-scope describes whole-broadcast aggregates with no
    moment in time. The 2026-05-27 incident showed that a single
    misclassified insight (session-scope leaking into a conversion
    list) produces empty-quote artifacts that LOOK like a UI bug but
    are actually a backend contract violation. Raising loudly here is
    the only way to keep the contract honest — silent fallback would
    bring back the original failure mode.
    """
    pass


def _enforce_insight_contract(insights: list[dict]) -> None:
    """Validate the segment/session scope contract on every insight.

    Rules (mirrored in insight_generator.py docstring — keep in sync):
      * Every insight MUST declare `scope` and it MUST be "segment" or "session".
      * scope == "segment" ⟹ `time_range` AND `script_text` REQUIRED.
        (Frontend dereferences both; without them the UI shows empty quotes.)
      * scope == "session" ⟹ `time_range` MUST NOT be set.
        ("Whole session" isn't a time range; if it has one, the insight
        was misclassified and would be ranked against segment-scope ones.)

    On violation, raise InsightContractError with details. The caller
    (pipeline._step_scoring) propagates this as a StepError, which flips
    session.status to "error" — the user SEES the problem rather than
    getting a session full of empty-quote artifacts.
    """
    violations: list[str] = []
    for idx, ins in enumerate(insights):
        scope = ins.get("scope")
        text_preview = (ins.get("text") or "")[:40]
        if scope not in ("segment", "session"):
            violations.append(
                f"insight[{idx}] '{text_preview}' has invalid scope={scope!r}"
            )
            continue
        if scope == "segment":
            if not ins.get("script_text"):
                violations.append(
                    f"insight[{idx}] '{text_preview}' scope=segment but script_text is empty"
                )
            if not ins.get("time_range"):
                violations.append(
                    f"insight[{idx}] '{text_preview}' scope=segment but time_range missing"
                )
        elif scope == "session":
            if ins.get("time_range"):
                violations.append(
                    f"insight[{idx}] '{text_preview}' scope=session but has time_range "
                    f"(session insights describe whole-broadcast, time_range is meaningless)"
                )
    if violations:
        msg = "insight contract violations: " + " | ".join(violations)
        _logger.error("[insight-contract] %s", msg)
        raise InsightContractError(msg)


def run_insight_engine(
    script_segments: list[dict],
    danmakus: list[dict],
    analysis_summary: dict,
    duration_sec: float,
) -> dict:
    # Step 1: Extract patterns
    patterns = extract_patterns(script_segments, danmakus, duration_sec)

    # Step 2: Interpret — absolute filters, merge, significance score, diversity
    total_dm = len(danmakus)
    narratives = interpret(patterns, total_dm, duration_sec)

    # Step 3: Generate insights from narratives + summary context.
    # script_segments passed as SSOT for looking up original script text.
    insights = generate_insights(narratives, analysis_summary, patterns, duration_sec, script_segments)

    # Contract check — turn silent leakage into loud failure. See
    # InsightContractError for the rationale; in short, a session-scope
    # insight tagged as conversion-impact will rank into the UI's
    # "high-conversion script" list and produce empty quotes there.
    _enforce_insight_contract(insights)

    # Step 3: Generate action recommendations
    recommendations = generate_recommendations(insights, patterns, analysis_summary, duration_sec)

    # Step 4: Build timeline highlights (for frontend tag display)
    highlights = _build_highlights(insights, patterns)

    # Step 5: Read scores from analysis_summary (SSOT — computed in pipeline._step_scoring).
    # NEVER recompute here. Engine v4 previously had its own divergent formula
    # (total_prompts * 3, total_tactics * 5, pain_mentions * 3) which produced
    # numbers that disagreed with the list-card scores and used "pain point
    # mentions" as a content quality proxy — both were misleading.
    scores = analysis_summary.get("scores") or {}
    engagement_score = float(scores.get("engagement", 0))
    conversion_score = float(scores.get("conversion", 0))
    content_score = float(scores.get("content", 0))
    overall = round((engagement_score + conversion_score + content_score) / 3, 1)

    # Extract top patterns for memory
    insights_texts = [i["text"] for i in insights]

    return {
        "summary": {
            "duration": _format_duration(duration_sec),
            "engagement_score": engagement_score,
            "conversion_score": conversion_score,
            "content_score": content_score,
            "overall_score": overall,
        },
        "insights": insights,  # ranked list with score/evidence/time_range
        "timeline_highlights": highlights,
        "recommendations": recommendations,
        "profile_snapshot": {  # InsightMemory seed
            "hot_patterns": [
                i["text"] for i in insights if i["impact"] == "engagement" and i["score"] >= 60
            ][:3],
            "conversion_patterns": [
                i["text"] for i in insights if i["impact"] == "conversion"
            ][:3],
            "weak_spots": [
                i["text"] for i in insights if i["score"] < 40
            ][:3],
            "timestamp": None,  # filled by runner
        },
        "versions": {
            "engine": "v4",
            "ranking": "sig_v1",
            "scoring": "5dim_weighted",
        },
    }


def _build_highlights(insights: list[dict], patterns: dict) -> list[dict]:
    highlights = []
    for ins in insights[:5]:
        tr = ins.get("time_range", {})
        if tr.get("start_sec"):
            m, s = divmod(int(tr["start_sec"]), 60)
            highlights.append({
                "time": f"{m}:{s:02d}",
                "type": (
                    "conversion_spike" if ins["impact"] == "conversion"
                    else "engagement_peak" if ins["impact"] == "engagement"
                    else "insight"
                ),
                "reason": ins["text"][:80],
                "start_sec": tr["start_sec"],
                "end_sec": tr.get("end_sec", tr["start_sec"]),
                "score": ins["score"],
            })
    return highlights


def _format_duration(sec: float) -> str:
    m, s = divmod(int(sec), 60)
    if m >= 60:
        h, m = divmod(m, 60)
        return f"{h}h{m}m{s}s"
    return f"{m}m{s}s"
