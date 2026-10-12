"""Semantic correlation engine v2 - Intent Transition Analysis.

Core insight: what matters is not just count, but HOW INTENT SHIFTS
in response to host tactics.

Intent Funnel: interaction -> comparison -> hesitation -> purchase_consideration -> strong_purchase

Tracks:
- Intent transition: which tactics drive upgrades down the funnel
- Top conversion triggers: scripts that drive intent upgrades
- Top engagement triggers: scripts that drive danmaku spikes
- Intent funnel: aggregate distribution
- Conversion phrase library: auto-accumulate patterns
"""

import json
import logging
from collections import Counter, defaultdict
from pathlib import Path

from app.services.intent.intent_prototypes import INTENT_FUNNEL

logger = logging.getLogger(__name__)

INTENT_ORDER = {t: i for i, t in enumerate(INTENT_FUNNEL)}

# Conversion phrase library path
LIBRARY_PATH = Path(__file__).parent.parent.parent / "data" / "conversion_patterns.json"


def compute_semantic_correlation(
    script_segments: list[dict],
    danmaku_records: list[dict],
    window_sec: int = 30,
) -> dict:
    """Compute semantic correlation with Intent Transition Analysis.

    Args:
        script_segments: Analyzed segments with analysis_tags (including embedding tactics).
        danmaku_records: Danmakus with intent_type, score_distribution.
        window_sec: Seconds after segment end to count danmaku. Default 30s
            covers the realistic broadcaster→audience reaction latency:
            speak → audience reads → thinks → types → sends → danmaku
            flies across screen → OCR captures (30s frame interval). The
            previous 10s window missed most genuine "tactic→reaction"
            pairs because the danmaku hadn't landed in the OCR buffer yet.

    Returns:
        Dict with:
            - top_conversion_triggers: scripts that drove intent upgrades
            - top_engagement_triggers: scripts that drove danmaku spikes
            - intent_funnel: aggregate funnel distribution
            - intent_transitions: significant before->after shifts
            - by_tactic_type: which tactics correlate with which intent shifts
            - funnel_flow: transition counts between intent stages
    """
    if not script_segments or not danmaku_records:
        return _empty_result()

    # ---- Step 1: Build per-segment intent profiles ----
    segment_profiles = []
    for seg in script_segments:
        start = seg["start_sec"]
        end = seg["end_sec"]
        s_type = seg.get("script_type", "chitchat")
        tags = seg.get("analysis_tags", {})
        tactics = tags.get("embedding_tactics", [])

        after_danmakus = [
            d for d in danmaku_records
            if end <= d.get("timestamp_sec", 0) <= end + window_sec
        ]
        before_danmakus = [
            d for d in danmaku_records
            if max(0, start - window_sec) <= d.get("timestamp_sec", 0) < start
        ]

        after_intent_dist = _intent_distribution(after_danmakus)
        before_intent_dist = _intent_distribution(before_danmakus)

        shift = _compute_intent_shift(before_intent_dist, after_intent_dist)

        segment_profiles.append({
            "start_sec": start,
            "end_sec": end,
            "text_preview": seg.get("text", "")[:80],
            "text_full": seg.get("text", ""),
            "script_type": s_type,
            "tactics": tactics,
            "danmaku_count_after": len(after_danmakus),
            "danmaku_count_before": len(before_danmakus),
            "before_intent": before_intent_dist,
            "after_intent": after_intent_dist,
            "intent_shift": shift,
            "engagement_delta": round(
                len(after_danmakus) / max(len(before_danmakus), 1), 1
            ) if before_danmakus else len(after_danmakus),
        })

    # ---- Step 2: Conversion scores ----
    for sp in segment_profiles:
        sp["conversion_score"] = _conversion_score(sp)

    top_conversion = sorted(
        segment_profiles, key=lambda x: x["conversion_score"], reverse=True
    )[:5]

    top_engagement = sorted(
        [sp for sp in segment_profiles if sp["danmaku_count_after"] >= 2],
        key=lambda x: x["engagement_delta"],
        reverse=True,
    )[:5]

    # ---- Step 3: Aggregate intent funnel ----
    funnel = _aggregate_funnel(danmaku_records)

    # ---- Step 4: Funnel flow (transition counts between stages) ----
    funnel_flow = _compute_funnel_flow(danmaku_records)

    # ---- Step 5: Tactic-intent correlation ----
    by_tactic = _tactic_intent_correlation(segment_profiles)

    # ---- Step 6: Intent transitions summary ----
    transitions = _summarize_transitions(segment_profiles)

    # ---- Step 7: Update conversion phrase library ----
    _update_conversion_library(top_conversion)

    return {
        "top_conversion_triggers": [
            _format_trigger(sp) for sp in top_conversion if sp["conversion_score"] > 0
        ],
        "top_engagement_triggers": [
            _format_engagement_trigger(sp) for sp in top_engagement
        ],
        "intent_funnel": funnel,
        "funnel_flow": funnel_flow,
        "by_tactic_type": by_tactic,
        "intent_transitions": transitions[:10],
    }


def _intent_distribution(danmakus: list[dict]) -> dict[str, int]:
    dist = Counter()
    for d in danmakus:
        intent = d.get("intent_type", "interaction") or "interaction"
        dist[intent] += 1
    return dict(dist)


def _compute_intent_shift(before: dict[str, int], after: dict[str, int]) -> dict:
    """Compute intent transition with before/after state."""
    before_total = sum(before.values()) or 1
    after_total = sum(after.values()) or 1

    before_avg = sum(INTENT_ORDER.get(t, 0) * c for t, c in before.items()) / before_total
    after_avg = sum(INTENT_ORDER.get(t, 0) * c for t, c in after.items()) / after_total

    net_lift = round(after_avg - before_avg, 3)

    # Find dominant transition direction
    dominant_shift = ""
    lift_pct = 0.0
    if net_lift > 0.2 and after_total >= 2:
        growth = {}
        for t in INTENT_FUNNEL:
            b = before.get(t, 0)
            a = after.get(t, 0)
            if a > b:
                growth[t] = a - b
        if growth:
            top_intent = max(growth, key=growth.get)
            dominant_shift = f"-> {top_intent}"
            before_count = before.get(top_intent, 0)
            after_count = after.get(top_intent, 0)
            if before_count > 0:
                lift_pct = round((after_count - before_count) / before_count * 100, 1)
            else:
                lift_pct = float(after_count * 100)

    return {
        "upgraded": max(0, int(net_lift * after_total)),
        "downgraded": max(0, int(-net_lift * after_total)),
        "net_lift": net_lift,
        "dominant_shift": dominant_shift,
        "lift_pct": lift_pct,
        "before_state": before,
        "after_state": after,
    }


def _conversion_score(sp: dict) -> float:
    shift = sp["intent_shift"]
    lift = shift["net_lift"]
    after = sp["after_intent"]
    strong = after.get("strong_purchase", 0)
    consideration = after.get("purchase_consideration", 0)

    tactic_names = [t["type"] if isinstance(t, dict) else t for t in sp.get("tactics", [])]
    has_conversion_tactic = any(
        t in ("scarcity", "urgency", "price_anchor", "risk_reversal") for t in tactic_names
    )

    score = (
        lift * 20
        + strong * 3
        + consideration * 1.5
        + (2 if has_conversion_tactic else 0)
    )
    return round(max(0, score), 2)


def _aggregate_funnel(danmakus: list[dict]) -> dict:
    dist = Counter()
    for d in danmakus:
        intent = d.get("intent_type", "interaction") or "interaction"
        dist[intent] += 1
    total = sum(dist.values()) or 1
    funnel = {}
    for t in INTENT_FUNNEL:
        count = dist.get(t, 0)
        funnel[t] = {"count": count, "pct": round(count / total * 100, 1)}
    return funnel


def _compute_funnel_flow(danmakus: list[dict]) -> dict:
    """Compute transition counts between consecutive intent stages.

    For each danmaku, check if the previous danmaku had a different intent type.
    This reveals actual user journey transitions.
    """
    transitions = Counter()
    prev_intent = None
    for d in sorted(danmakus, key=lambda x: x.get("timestamp_sec", 0)):
        intent = d.get("intent_type", "interaction") or "interaction"
        if prev_intent and prev_intent != intent:
            # Only count "upgrade" transitions (moving down the funnel)
            if INTENT_ORDER.get(intent, 0) > INTENT_ORDER.get(prev_intent, 0):
                transitions[f"{prev_intent}->{intent}"] += 1
        prev_intent = intent

    return dict(transitions)


def _tactic_intent_correlation(segment_profiles: list[dict]) -> dict:
    tactic_stats = defaultdict(lambda: {"count": 0, "avg_lift": 0.0, "lifts": [],
                                         "strong_purchase_avg": 0.0, "sp_counts": []})

    for sp in segment_profiles:
        lift = sp["intent_shift"]["net_lift"]
        after = sp["after_intent"]
        sp_count = after.get("strong_purchase", 0)
        tactics = sp.get("tactics", [])
        tactic_names = [t["type"] if isinstance(t, dict) else t for t in tactics]

        if not tactic_names:
            continue

        for name in tactic_names:
            tactic_stats[name]["count"] += 1
            tactic_stats[name]["lifts"].append(lift)
            tactic_stats[name]["sp_counts"].append(sp_count)

    result = {}
    for name, stats in tactic_stats.items():
        lifts = stats["lifts"]
        sp_counts = stats["sp_counts"]
        result[name] = {
            "count": stats["count"],
            "avg_lift": round(sum(lifts) / len(lifts), 3) if lifts else 0,
            "max_lift": round(max(lifts), 3) if lifts else 0,
            "avg_strong_purchase": round(sum(sp_counts) / len(sp_counts), 1) if sp_counts else 0,
        }
    return result


def _summarize_transitions(segment_profiles: list[dict]) -> list[dict]:
    transitions = []
    for sp in segment_profiles:
        shift = sp["intent_shift"]
        if abs(shift["net_lift"]) >= 0.2 and sp["danmaku_count_after"] >= 2:
            transitions.append({
                "time_sec": sp["start_sec"],
                "text_preview": sp["text_preview"],
                "tactics": [t["type"] if isinstance(t, dict) else t for t in sp.get("tactics", [])],
                "before": sp["before_intent"],
                "after": sp["after_intent"],
                "net_lift": shift["net_lift"],
                "lift_pct": shift["lift_pct"],
                "dominant_shift": shift["dominant_shift"],
            })
    return sorted(transitions, key=lambda x: abs(x["net_lift"]), reverse=True)


def _format_trigger(sp: dict) -> dict:
    tactics = sp.get("tactics", [])
    tactic_names = [t["type"] if isinstance(t, dict) else t for t in tactics]
    after = sp["after_intent"]
    purchase_total = after.get("strong_purchase", 0) + after.get("purchase_consideration", 0)
    strong_pct = round(after.get("strong_purchase", 0) / max(purchase_total, 1) * 100) if purchase_total > 0 else 0

    return {
        "script": sp["text_preview"],
        "tactics": tactic_names,
        "triggered_intents": after,
        "conversion_score": sp["conversion_score"],
        "net_lift": sp["intent_shift"]["net_lift"],
        "lift_pct": sp["intent_shift"]["lift_pct"],
        "dominant_shift": sp["intent_shift"]["dominant_shift"],
        "strong_purchase_pct": strong_pct,
    }


def _format_engagement_trigger(sp: dict) -> dict:
    tactics = sp.get("tactics", [])
    tactic_names = [t["type"] if isinstance(t, dict) else t for t in tactics]
    return {
        "script": sp["text_preview"],
        "tactics": tactic_names,
        "engagement_delta": sp["engagement_delta"],
        "triggered_comments": sp["danmaku_count_after"],
        "intent_shift": sp["intent_shift"]["dominant_shift"],
    }


def _update_conversion_library(top_triggers: list[dict]) -> None:
    """Auto-accumulate high-conversion phrases to persistent library."""
    if not top_triggers:
        return

    try:
        LIBRARY_PATH.parent.mkdir(parents=True, exist_ok=True)

        # Load existing
        library = []
        if LIBRARY_PATH.exists():
            with open(LIBRARY_PATH, "r", encoding="utf-8") as f:
                library = json.load(f)

        # Add new entries (dedupe by phrase)
        existing_phrases = {entry.get("phrase") for entry in library}
        for trigger in top_triggers:
            phrase = trigger.get("text_full", trigger.get("text_preview", ""))
            if not phrase or phrase in existing_phrases:
                continue
            library.append({
                "phrase": phrase,
                "conversion_score": trigger["conversion_score"],
                "triggered_intents": trigger["after_intent"],
                "tactics": [t["type"] if isinstance(t, dict) else t for t in trigger.get("tactics", [])],
                "industry": "beauty",
                "product_type": "generic",
            })
            existing_phrases.add(phrase)

        # Keep top 100
        library.sort(key=lambda x: x.get("conversion_score", 0), reverse=True)
        library = library[:100]

        with open(LIBRARY_PATH, "w", encoding="utf-8") as f:
            json.dump(library, f, ensure_ascii=False, indent=2)

    except Exception as e:
        logger.warning("Failed to update conversion library: %s", e)


def _empty_result() -> dict:
    return {
        "top_conversion_triggers": [],
        "top_engagement_triggers": [],
        "intent_funnel": {t: {"count": 0, "pct": 0} for t in INTENT_FUNNEL},
        "funnel_flow": {},
        "by_tactic_type": {},
        "intent_transitions": [],
    }