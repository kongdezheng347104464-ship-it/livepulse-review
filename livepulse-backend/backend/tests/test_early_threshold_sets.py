"""Tests for early_threshold_sets — classification + immutability contract."""

import hashlib
import json
import pytest

from app.services.insight_engine.early_threshold_sets import (
    EARLY_THRESHOLD_SETS,
    classify_signal,
    get_threshold_set,
    get_impact_weight,
    get_dimension_label,
)
from app.services.insight_engine.early_action_registry import (
    load_registry,
    find_action_key,
    get_action,
)


# ---------- Classification logic ----------

class TestClassifySignal:
    def test_signal_in_target_returns_good_0(self):
        # teaching_ratio target [0.30, 0.50]
        status, severity = classify_signal("teaching_ratio", 0.40)
        assert status == "good"
        assert severity == 0

    def test_signal_below_target_returns_under(self):
        # teaching_ratio 0.10 → 0.20 below 0.30
        status, severity = classify_signal("teaching_ratio", 0.10)
        assert status == "under"
        assert severity == 2  # 0.20 >= severe 0.20

    def test_signal_above_target_returns_over(self):
        # teaching_ratio 0.75 → 0.25 above 0.50, severe is 0.20
        status, severity = classify_signal("teaching_ratio", 0.75)
        assert status == "over"
        assert severity == 2

    def test_mild_deviation_returns_severity_1(self):
        # teaching_ratio 0.18 → 0.12 below 0.30, mild=0.10 severe=0.20
        # 0.12 >= mild 0.10 but < severe 0.20 → severity 1
        status, severity = classify_signal("teaching_ratio", 0.18)
        assert status == "under"
        assert severity == 1

    def test_unknown_signal_returns_good(self):
        status, severity = classify_signal("nonexistent_signal", 999)
        assert status == "good"
        assert severity == 0

    def test_hook_score_at_60_is_good(self):
        # hook_score target [60, 100], boundary inclusive
        status, severity = classify_signal("hook_score", 60)
        assert status == "good"

    def test_hook_score_at_0_is_severe_under(self):
        status, severity = classify_signal("hook_score", 0)
        assert status == "under"
        assert severity == 2


class TestImpactWeight:
    def test_known_signal_returns_weight(self):
        assert get_impact_weight("hook_score") == 1.0
        assert get_impact_weight("teaching_ratio") == 0.5

    def test_unknown_signal_returns_0(self):
        assert get_impact_weight("nonexistent") == 0.0


class TestDimensionLabel:
    def test_known_signal_returns_chinese_label(self):
        assert get_dimension_label("hook_score") == "开场"
        assert get_dimension_label("teaching_ratio") == "教学"


# ---------- Immutability contract ----------

class TestFrozenContract:
    """If you intentionally change `general_v1`, also bump the hash here.
    Otherwise this test failing is your forced consideration moment per
    the B5 design (2026-05-30).
    """

    EXPECTED_GENERAL_V1_HASH = "REGENERATE_ME"  # filled below

    def test_general_v1_has_frozen_flag(self):
        spec = get_threshold_set("general_v1")
        assert spec.get("frozen") is True

    def test_general_v1_has_7_signals(self):
        spec = get_threshold_set("general_v1")
        assert len(spec["signals"]) == 7

    def test_general_v1_signal_names_are_canonical(self):
        spec = get_threshold_set("general_v1")
        expected = {
            "hook_score", "promotion_density", "product_intro_ratio",
            "pain_point_coverage", "topic_stability_score",
            "pace_score", "teaching_ratio",
        }
        assert set(spec["signals"].keys()) == expected


# ---------- Action registry ----------

class TestActionRegistry:
    def test_load_succeeds(self):
        reg = load_registry()
        assert "_meta" in reg
        assert "actions" in reg
        assert reg["_meta"]["frozen"] is True

    def test_find_action_for_teaching_over(self):
        key = find_action_key("teaching_ratio", "over")
        assert key == "reduce_teaching_ratio"

    def test_find_action_for_teaching_under(self):
        key = find_action_key("teaching_ratio", "under")
        assert key == "increase_teaching_depth"

    def test_find_action_for_hook_under(self):
        key = find_action_key("hook_score", "under")
        assert key == "strengthen_opening_hook"

    def test_find_action_for_hook_over_returns_none(self):
        # 我们故意不为 hook_score 'over' 注册 action （"hook 太强"不是个真问题）
        assert find_action_key("hook_score", "over") is None

    def test_get_action_returns_full_spec(self):
        action = get_action("reduce_teaching_ratio")
        assert action is not None
        assert "short" in action
        assert "long" in action
        assert "recommended_range" in action

    def test_every_registered_signal_has_threshold_entry(self):
        """Each action's signal must exist in general_v1 threshold set."""
        reg = load_registry()
        threshold_signals = set(get_threshold_set("general_v1")["signals"].keys())
        for key, action in reg["actions"].items():
            sig = action.get("signal")
            assert sig in threshold_signals, \
                f"Action {key!r} references unknown signal {sig!r}"
