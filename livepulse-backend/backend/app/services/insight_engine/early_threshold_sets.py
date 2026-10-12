"""Early-insight threshold sets — the "rule" layer that turns objective
signals into status (over/under/good) and severity (0/1/2).

⚠️  IMMUTABILITY CONTRACT  ⚠️
============================
A published threshold_set (`frozen: True`) MUST NEVER be edited in place.
To change a target_range / severity boundary / impact weight, BUMP a new
version (`general_v2`), keep the old one intact, and run a backfill if
needed. This is the only way `early_insights[].threshold_set` stays
auditable across sessions and B4 dashboard can compare like-for-like.

A test in tests/api/test_invariants.py asserts that frozen sets have not
been edited (by comparing structure hash). If you intentionally need to
change v1, also update the expected hash — the test failure is your
forced consideration moment, not a nuisance.

Schema (per signal):
  target: [min, max] — values in this range → status=good, severity=0
  mild:   distance from target boundary beyond which severity=1
  severe: distance from target boundary beyond which severity=2
  impact: weight in 0-1, multiplied with severity to rank Top 3

Direction interpretation:
  signal < target[0] → status="under"
  signal > target[1] → status="over"
  target[0] ≤ signal ≤ target[1] → status="good" (won't enter Top 3)
"""

from __future__ import annotations
from typing import Literal

Status = Literal["over", "under", "good"]


EARLY_THRESHOLD_SETS: dict = {
    "general_v1": {
        "frozen": True,
        "published_at": "2026-05-30",
        "description": "Default for small/MCN beauty streamers. Empirical "
                       "values; will be retuned after 50+ labeled sessions.",
        "signals": {
            "hook_score": {
                "target": [60, 100],
                "mild": 20, "severe": 40, "impact": 1.0,
                "dimension_label": "开场",
            },
            "promotion_density": {
                "target": [0.15, 0.35],
                "mild": 0.05, "severe": 0.12, "impact": 0.9,
                "dimension_label": "促销频率",
            },
            "product_intro_ratio": {
                "target": [0.20, 0.45],
                "mild": 0.08, "severe": 0.15, "impact": 0.8,
                "dimension_label": "产品讲解",
            },
            "pain_point_coverage": {
                "target": [2, 6],
                "mild": 1, "severe": 2, "impact": 0.7,
                "dimension_label": "痛点覆盖",
            },
            "topic_stability_score": {
                "target": [0.4, 0.8],
                "mild": 0.1, "severe": 0.25, "impact": 0.65,
                "dimension_label": "话题稳定度",
            },
            "pace_score": {
                "target": [40, 80],
                "mild": 10, "severe": 25, "impact": 0.6,
                "dimension_label": "节奏",
            },
            "teaching_ratio": {
                "target": [0.30, 0.50],
                "mild": 0.10, "severe": 0.20, "impact": 0.5,
                "dimension_label": "教学",
            },
        },
    },
}


def get_threshold_set(name: str) -> dict:
    """Returns the threshold set spec. Raises KeyError if unknown."""
    if name not in EARLY_THRESHOLD_SETS:
        raise KeyError(f"Unknown threshold_set: {name!r}. "
                       f"Known: {list(EARLY_THRESHOLD_SETS.keys())}")
    return EARLY_THRESHOLD_SETS[name]


def classify_signal(
    signal_name: str,
    signal_value: float,
    threshold_set_name: str = "general_v1",
) -> tuple[Status, int]:
    """Map a raw signal value to (status, severity).

    Returns:
        status: "over" | "under" | "good"
        severity: 0 (good) | 1 (mild deviation) | 2 (severe deviation)

    severity=0 means the signal is in normal range — caller should NOT
    surface it as a Top 3 insight (per spec: only deviations are worth
    showing).
    """
    spec = get_threshold_set(threshold_set_name)["signals"].get(signal_name)
    if spec is None:
        # Signal not configured in this threshold set — return "good" 0
        # so caller silently skips it rather than crashing.
        return "good", 0

    lo, hi = spec["target"]
    if lo <= signal_value <= hi:
        return "good", 0

    if signal_value < lo:
        deviation = lo - signal_value
        status: Status = "under"
    else:
        deviation = signal_value - hi
        status = "over"

    # Round to 6 dp to avoid floating-point "0.30 - 0.10 = 0.1999..."
    # silently demoting severity=2 to severity=1 on exact boundaries.
    deviation = round(deviation, 6)

    if deviation >= spec["severe"]:
        return status, 2
    if deviation >= spec["mild"]:
        return status, 1
    # Below the mild threshold — call it 1 (worth surfacing but lowest tier)
    return status, 1


def get_impact_weight(
    signal_name: str,
    threshold_set_name: str = "general_v1",
) -> float:
    """Get the impact weight for ranking Top 3."""
    spec = get_threshold_set(threshold_set_name)["signals"].get(signal_name)
    return spec["impact"] if spec else 0.0


def get_dimension_label(
    signal_name: str,
    threshold_set_name: str = "general_v1",
) -> str:
    """Get the human-readable dimension label (开场/教学/节奏...)."""
    spec = get_threshold_set(threshold_set_name)["signals"].get(signal_name)
    return spec["dimension_label"] if spec else signal_name
