"""Early-insight generator — transforms early_signals into Top 3 narrative
insights using the threshold_set + action_registry.

This is the **decision layer** of B5 真分层. Per the 2026-05-30 design:
  * Signals (early_signals_extractor) = pure objective measurements
  * Decisions (this file) = threshold_set + impact_weight applied to signals
  * Narratives (frontend) = action_registry text rendered from action_key

Output schema (`early_insights[i]`):
  {
    "dimension": "教学",                  # 7-of-7 set, no overlap with full
    "status": "over" | "under",            # 'good' insights are dropped
    "severity": 1 | 2,                     # 0 excluded; discrete only
    "score": 0.71,                          # raw signal value
    "action_key": "reduce_teaching_ratio",  # registry lookup key
    "threshold_set": "general_v1",          # auditability
    "weights_version": "v1"
  }

Ranking: primary = severity × impact_weight (desc), tiebreakers see
_rank_key. Top 3 are taken. If <3 signals deviate, fewer are returned
(per 空 Top 3 fallback rule — never pad).
"""

from __future__ import annotations
from typing import Iterable

from .early_threshold_sets import (
    classify_signal,
    get_impact_weight,
    get_dimension_label,
    get_threshold_set,
)
from .early_action_registry import find_action_key


EARLY_WEIGHTS_VERSION = "v1"
MAX_EARLY_INSIGHTS = 3


def generate_early_insights(
    early_signals: dict,
    threshold_set_name: str = "general_v1",
) -> dict:
    """Run the classify → rank → top3 pipeline.

    Returns:
        {
          "early_insights": [...],
          "early_summary": str,           # human-readable, used when list is empty
          "threshold_set": "general_v1",
          "weights_version": "v1"
        }
    """
    if not early_signals:
        return _empty_result(threshold_set_name, "本场脚本数据不足，等待完整分析")

    # Stable iteration order = order in threshold_set spec. Important for
    # tiebreaker determinism (Python 3.7+ preserves dict insertion order).
    spec = get_threshold_set(threshold_set_name)
    signal_order = {name: idx for idx, name in enumerate(spec["signals"].keys())}

    # Classify every signal that the threshold set knows about
    candidates = []
    for sig_name, sig_value in early_signals.items():
        if sig_name not in signal_order:
            # Unknown signal — silently skip (signal may be extracted but
            # not yet registered in this threshold_set version)
            continue
        status, severity = classify_signal(sig_name, sig_value, threshold_set_name)
        if severity == 0:
            continue  # 'good' — never enter Top 3

        impact = get_impact_weight(sig_name, threshold_set_name)
        action_key = find_action_key(sig_name, status)
        if action_key is None:
            # No registered action for this (signal, status) — e.g. hook
            # 'over' isn't a real problem. Skip silently rather than
            # surface a partial card.
            continue

        candidates.append({
            "signal_name": sig_name,           # internal, dropped before output
            "dimension": get_dimension_label(sig_name, threshold_set_name),
            "status": status,
            "severity": severity,
            "score": sig_value,
            "action_key": action_key,
            "impact_weight": impact,           # internal, dropped before output
            "_order": signal_order[sig_name],  # tiebreaker
        })

    if not candidates:
        return _empty_result(
            threshold_set_name,
            "本场脚本各维度都在正常区间，等待完整数据进一步分析",
        )

    # Sort by composite rank key (see _rank_key for the 4-level
    # deterministic ordering)
    candidates.sort(key=_rank_key)
    top = candidates[:MAX_EARLY_INSIGHTS]

    # Strip internal fields before returning
    insights = []
    for c in top:
        insights.append({
            "dimension": c["dimension"],
            "status": c["status"],
            "severity": c["severity"],
            "score": c["score"],
            "action_key": c["action_key"],
            "threshold_set": threshold_set_name,
            "weights_version": EARLY_WEIGHTS_VERSION,
        })

    return {
        "early_insights": insights,
        "early_summary": _build_summary(insights),
        "threshold_set": threshold_set_name,
        "weights_version": EARLY_WEIGHTS_VERSION,
    }


def _rank_key(candidate: dict) -> tuple:
    """4-level deterministic sort key (smaller = higher priority).

    1. severity × impact_weight (desc)  — primary product score
    2. severity (desc)                  — ties broken by severity (severe > mild)
    3. impact_weight (desc)             — then by raw impact
    4. signal definition order (asc)    — finally by stable threshold_set order

    Python `sort()` ascends by default; negate descending fields.
    """
    primary = -(candidate["severity"] * candidate["impact_weight"])
    return (
        primary,
        -candidate["severity"],
        -candidate["impact_weight"],
        candidate["_order"],
    )


def _empty_result(threshold_set_name: str, summary: str) -> dict:
    return {
        "early_insights": [],
        "early_summary": summary,
        "threshold_set": threshold_set_name,
        "weights_version": EARLY_WEIGHTS_VERSION,
    }


def _build_summary(insights: list[dict]) -> str:
    """One-line summary when Top 3 has content. Frontend may render
    this above the cards as section title prefix."""
    if not insights:
        return ""
    n = len(insights)
    dims = "、".join(i["dimension"] for i in insights)
    return f"脚本分析已完成，发现 {n} 个优先改进点：{dims}"
