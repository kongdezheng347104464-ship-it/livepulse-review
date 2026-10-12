"""Tests for early_signals_extractor — pure script-only signal extraction.

Contract: signals are objective measurements only (no judgment, no
thresholds, no status). Tests verify the 7 signal functions produce
expected values for canonical inputs.
"""
from app.services.insight_engine.early_signals_extractor import (
    extract_early_signals,
    _compute_hook_score,
    _compute_type_ratio,
    _compute_pain_point_coverage,
    _compute_topic_stability,
    _compute_pace_score,
)


def _seg(start, end, script_type="chitchat", **tags):
    """Build a minimal analyzed segment dict for tests."""
    return {
        "start_sec": start,
        "end_sec": end,
        "script_type": script_type,
        "analysis_tags": tags,
    }


class TestEmptyInput:
    def test_empty_segments_returns_zeroed_signals(self):
        result = extract_early_signals([], 100)
        assert result["hook_score"] == 0
        assert result["teaching_ratio"] == 0.0
        assert result["pain_point_coverage"] == 0
        assert result["topic_stability_score"] == 0.0

    def test_zero_duration_returns_zeroed_signals(self):
        result = extract_early_signals([_seg(0, 10, "teaching")], 0)
        assert result["teaching_ratio"] == 0.0

    def test_none_duration_falls_back_to_inferred(self):
        segs = [_seg(0, 30, "teaching"), _seg(30, 60, "promotion")]
        result = extract_early_signals(segs, None)
        # 30s teaching out of 60s inferred → 0.5
        assert result["teaching_ratio"] == 0.5


class TestHookScore:
    def test_opening_at_0s_scores_100(self):
        segs = [_seg(0, 20, "interaction", is_opening=True)]
        assert _compute_hook_score(segs) == 100

    def test_opening_at_45s_scores_60(self):
        segs = [
            _seg(0, 30, "chitchat"),
            _seg(30, 60, "interaction", is_opening=True),
        ]
        assert _compute_hook_score(segs) == 60

    def test_opening_after_60s_scores_0(self):
        segs = [
            _seg(0, 60, "chitchat"),
            _seg(60, 90, "chitchat"),
            _seg(90, 120, "interaction", is_opening=True),
        ]
        assert _compute_hook_score(segs) == 0

    def test_no_opening_anywhere_scores_0(self):
        segs = [_seg(0, 60, "chitchat"), _seg(60, 120, "teaching")]
        assert _compute_hook_score(segs) == 0

    def test_first_opening_wins_even_if_later_ones_exist(self):
        segs = [
            _seg(0, 20, "interaction", is_opening=True),
            _seg(20, 50, "interaction", is_opening=True),
        ]
        # earliest opening at 0s → 100
        assert _compute_hook_score(segs) == 100


class TestTypeRatio:
    def test_full_teaching_session_ratio_is_1(self):
        segs = [_seg(0, 100, "teaching")]
        assert _compute_type_ratio(segs, "teaching", 100) == 1.0

    def test_60_percent_teaching_ratio(self):
        segs = [_seg(0, 60, "teaching"), _seg(60, 100, "chitchat")]
        assert _compute_type_ratio(segs, "teaching", 100) == 0.6

    def test_no_matching_type_ratio_is_0(self):
        segs = [_seg(0, 60, "chitchat")]
        assert _compute_type_ratio(segs, "promotion", 60) == 0.0


class TestPainPointCoverage:
    def test_dedup_duplicate_pain_points(self):
        segs = [
            _seg(0, 30, pain_points=["斑驳", "痘印"]),
            _seg(30, 60, pain_points=["斑驳", "泛红"]),  # 斑驳 repeats
        ]
        assert _compute_pain_point_coverage(segs) == 3  # 斑驳/痘印/泛红

    def test_empty_pain_points_returns_0(self):
        segs = [_seg(0, 30), _seg(30, 60)]
        assert _compute_pain_point_coverage(segs) == 0

    def test_whitespace_strip(self):
        segs = [
            _seg(0, 30, pain_points=["斑驳 ", " 斑驳"]),  # stripped to same
        ]
        assert _compute_pain_point_coverage(segs) == 1


class TestTopicStability:
    def test_all_same_type_stability_is_1(self):
        segs = [_seg(0, 10, "teaching"), _seg(10, 20, "teaching"), _seg(20, 30, "teaching")]
        assert _compute_topic_stability(segs) == 1.0

    def test_every_change_stability_is_0(self):
        segs = [
            _seg(0, 10, "teaching"),
            _seg(10, 20, "promotion"),
            _seg(20, 30, "teaching"),
            _seg(30, 40, "promotion"),
        ]
        # 3 transitions, 3 possible → 0.0
        assert _compute_topic_stability(segs) == 0.0

    def test_half_changes_stability_is_05(self):
        segs = [
            _seg(0, 10, "teaching"),
            _seg(10, 20, "teaching"),
            _seg(20, 30, "promotion"),
            _seg(30, 40, "promotion"),
            _seg(40, 50, "teaching"),
        ]
        # 2 transitions, 4 possible → 0.5
        assert _compute_topic_stability(segs) == 0.5

    def test_single_segment_stability_is_1(self):
        assert _compute_topic_stability([_seg(0, 30, "teaching")]) == 1.0


class TestPaceScore:
    def test_avg_30s_segments_score_100(self):
        segs = [_seg(0, 30), _seg(30, 60), _seg(60, 90)]
        assert _compute_pace_score(segs, 90) == 100

    def test_avg_15s_segments_score_60(self):
        segs = [_seg(0, 15), _seg(15, 30), _seg(30, 45), _seg(45, 60)]
        assert _compute_pace_score(segs, 60) == 60

    def test_avg_8s_segments_score_30(self):
        segs = [_seg(i * 8, (i + 1) * 8) for i in range(10)]
        assert _compute_pace_score(segs, 80) == 30

    def test_very_short_segments_score_low(self):
        segs = [_seg(i * 2, (i + 1) * 2) for i in range(30)]
        score = _compute_pace_score(segs, 60)
        assert 0 <= score < 30


class TestIntegrationFullExtract:
    def test_realistic_session_produces_all_signals(self):
        segs = [
            _seg(0, 25, "interaction", is_opening=True, pain_points=["斑驳"]),
            _seg(25, 60, "teaching", pain_points=["痘印"]),
            _seg(60, 90, "teaching"),
            _seg(90, 120, "product_intro", pain_points=["泛红"]),
            _seg(120, 150, "promotion"),
        ]
        result = extract_early_signals(segs, 150)

        assert result["hook_score"] == 100  # opening at 0s
        assert result["teaching_ratio"] == round(65 / 150, 4)  # 25-90s
        assert result["product_intro_ratio"] == round(30 / 150, 4)
        assert result["promotion_density"] == round(30 / 150, 4)
        assert result["pain_point_coverage"] == 3
        # Transitions: interaction→teaching (1), teaching→teaching (0),
        # teaching→product_intro (1), product_intro→promotion (1) = 3
        # out of 4 possible → stability = 1 - 3/4 = 0.25
        assert result["topic_stability_score"] == 0.25
        # avg duration 30s → 100
        assert result["pace_score"] == 100

    def test_returns_exactly_7_signal_keys(self):
        result = extract_early_signals([_seg(0, 30, "teaching")], 30)
        expected_keys = {
            "hook_score", "teaching_ratio", "product_intro_ratio",
            "promotion_density", "pain_point_coverage",
            "topic_stability_score", "pace_score",
        }
        assert set(result.keys()) == expected_keys
