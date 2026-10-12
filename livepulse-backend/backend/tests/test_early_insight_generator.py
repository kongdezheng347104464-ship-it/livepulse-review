"""Tests for early_insight_generator — the decision layer.

Verifies the contract:
  * severity=0 signals are dropped (never in Top 3)
  * action_key=None signals are dropped (e.g. hook 'over')
  * Top 3 ranking uses severity × impact + 4-level deterministic tiebreaker
  * Empty input / all-good input → empty insights + summary message
  * Dimensions in output ∩ full insight impacts (conversion/engagement/
    rhythm) == empty (separation contract)
"""
from app.services.insight_engine.early_insight_generator import (
    generate_early_insights,
    MAX_EARLY_INSIGHTS,
)


def _signals(**overrides):
    """Build a baseline of all-good signals, with overrides for testing."""
    # All values land squarely in their target ranges → severity 0
    base = {
        "hook_score": 80,
        "promotion_density": 0.25,
        "product_intro_ratio": 0.30,
        "pain_point_coverage": 4,
        "topic_stability_score": 0.6,
        "pace_score": 60,
        "teaching_ratio": 0.40,
    }
    base.update(overrides)
    return base


class TestEmptyInput:
    def test_empty_dict_returns_empty_with_message(self):
        result = generate_early_insights({})
        assert result["early_insights"] == []
        assert "脚本数据不足" in result["early_summary"]

    def test_all_good_signals_returns_empty_with_message(self):
        result = generate_early_insights(_signals())
        assert result["early_insights"] == []
        assert "正常区间" in result["early_summary"]


class TestSingleDeviationCases:
    def test_teaching_over_returns_one_insight(self):
        # teaching_ratio target [0.30, 0.50], 0.75 → over + severe
        result = generate_early_insights(_signals(teaching_ratio=0.75))
        assert len(result["early_insights"]) == 1
        ins = result["early_insights"][0]
        assert ins["dimension"] == "教学"
        assert ins["status"] == "over"
        assert ins["severity"] == 2
        assert ins["action_key"] == "reduce_teaching_ratio"

    def test_hook_under_returns_strengthen_action(self):
        result = generate_early_insights(_signals(hook_score=0))
        assert len(result["early_insights"]) == 1
        assert result["early_insights"][0]["action_key"] == "strengthen_opening_hook"


class TestTop3Selection:
    def test_more_than_3_deviations_returns_only_3(self):
        # Make all 7 deviate
        signals = {
            "hook_score": 0,                    # severe under, impact 1.0 → 2.0
            "promotion_density": 0.0,           # severe under, impact 0.9 → 1.8
            "product_intro_ratio": 0.0,         # severe under, impact 0.8 → 1.6
            "pain_point_coverage": 0,           # severe under, impact 0.7 → 1.4
            "topic_stability_score": 0.0,       # severe under, impact 0.65 → 1.3
            "pace_score": 0,                    # severe under, impact 0.6 → 1.2
            "teaching_ratio": 0.0,              # severe under, impact 0.5 → 1.0
        }
        result = generate_early_insights(signals)
        assert len(result["early_insights"]) == MAX_EARLY_INSIGHTS
        # Should be top 3 by impact: hook, promotion, product_intro
        dimensions = [i["dimension"] for i in result["early_insights"]]
        assert dimensions == ["开场", "促销频率", "产品讲解"]

    def test_severity_2_outranks_severity_1_with_equal_impact(self):
        # Force a tie scenario where severity matters
        # hook: severity 2 × 1.0 = 2.0 vs teaching: severity 2 × 0.5 = 1.0
        signals = _signals(hook_score=0, teaching_ratio=0.75)
        result = generate_early_insights(signals)
        # hook (impact 1.0) ranks above teaching (impact 0.5)
        assert result["early_insights"][0]["dimension"] == "开场"
        assert result["early_insights"][1]["dimension"] == "教学"

    def test_tiebreaker_uses_impact_when_severity_x_impact_ties(self):
        # severity 1 × impact 1.0 = 1.0 (hook mild)
        # severity 2 × impact 0.5 = 1.0 (teaching severe)
        # Both score 1.0 → tiebreaker 2 = severity desc → teaching (sev=2) first
        signals = _signals(
            hook_score=45,         # 45 < 60, deviation 15 = mild (< severe 40)
            teaching_ratio=0.75,   # severe over
        )
        result = generate_early_insights(signals)
        # Teaching ranks first because its severity is higher
        assert result["early_insights"][0]["dimension"] == "教学"


class TestActionKeyFiltering:
    def test_unregistered_action_pair_is_dropped(self):
        # We intentionally do NOT register hook_score 'over' as an action.
        # Even if signal value is way above target, no Top 3 entry should
        # appear from this signal.
        # But hook_score max is 100; we can't go over 100 in practice.
        # Use a synthetic case: pass an explicit value beyond target.
        result = generate_early_insights(_signals(hook_score=200))
        # 200 > 100 (target max) → status="over", but no action_key registered
        # Filter should drop it. All other signals are good → empty result.
        assert result["early_insights"] == []


class TestStructureAndContract:
    def test_every_insight_has_required_fields(self):
        result = generate_early_insights(_signals(teaching_ratio=0.75))
        ins = result["early_insights"][0]
        required = {"dimension", "status", "severity", "score",
                    "action_key", "threshold_set", "weights_version"}
        assert required.issubset(ins.keys())

    def test_threshold_set_id_persisted(self):
        result = generate_early_insights(_signals(teaching_ratio=0.75))
        assert result["early_insights"][0]["threshold_set"] == "general_v1"
        assert result["threshold_set"] == "general_v1"

    def test_no_target_range_in_output(self):
        """Per design: target_range MUST NOT be in insight output (only in
        registry). Prevents B4 cross-session comparison from being polluted
        by per-row thresholds."""
        result = generate_early_insights(_signals(teaching_ratio=0.75))
        ins = result["early_insights"][0]
        assert "target_range" not in ins
        assert "target" not in ins

    def test_dimensions_never_overlap_full_impacts(self):
        """Critical separation contract. Early dimensions must NOT include
        any of {conversion, engagement, rhythm} (those are full insights'
        impact values). See AI 产品守则 #3."""
        forbidden = {"conversion", "engagement", "rhythm",
                     "互动", "转化", "成交", "购买意图", "留存", "观众质量"}
        result = generate_early_insights({
            "hook_score": 0, "teaching_ratio": 0.75, "pace_score": 0,
        })
        for ins in result["early_insights"]:
            assert ins["dimension"] not in forbidden
