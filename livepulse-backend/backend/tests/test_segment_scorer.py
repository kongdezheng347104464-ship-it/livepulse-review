"""Tests for segment_scorer — pure scoring function.

All scoring decisions should be deterministic and boundary-tested.
"""
import pytest
from decimal import Decimal
from app.services.script_analyzer.scoring.segment_scorer import score_segment, CATEGORY_BASE_SCORES


class TestCategoryBaseScores:
    def test_teaching_scores_higher_than_chitchat(self):
        teach = score_segment("text", ["teaching"], speech_rate=150)
        chat = score_segment("text", ["chitchat"], speech_rate=150)
        assert teach > chat

    def test_product_intro_base_score(self):
        score = score_segment("text", ["product_intro"], speech_rate=150)
        assert float(score) >= CATEGORY_BASE_SCORES["product_intro"]


class TestMultiLabelBonus:
    def test_two_categories_get_bonus(self):
        single = score_segment("text", ["chitchat"], speech_rate=150)
        double = score_segment("text", ["teaching", "interaction"], speech_rate=150)
        # double should be higher: base(teaching=40) + bonus(5) vs base(chitchat=10)
        assert double > single

    def test_empty_categories_defaults_to_chitchat(self):
        score = score_segment("text", [], speech_rate=150)
        assert float(score) >= CATEGORY_BASE_SCORES["chitchat"]


class TestTextLengthDensity:
    def test_long_text_gets_full_bonus(self):
        long_text = "x" * 30  # 30 chars, >= 30 threshold
        short_text = "hi"
        long_score = score_segment(long_text, ["chitchat"], speech_rate=150)
        short_score = score_segment(short_text, ["chitchat"], speech_rate=150)
        assert long_score > short_score

    def test_medium_text_gets_half_bonus(self):
        medium_text = "x" * 15  # 15 chars, >= 10 but < 30
        tiny_text = "x"
        medium_score = score_segment(medium_text, ["chitchat"], speech_rate=150)
        tiny_score = score_segment(tiny_text, ["chitchat"], speech_rate=150)
        assert medium_score > tiny_score


class TestEngagementAndClosingBonus:
    def test_engagement_bonus_per_type(self):
        base = score_segment("hello world test message here", ["chitchat"], speech_rate=150)
        with_eng = score_segment("hello world test message here", ["chitchat"],
                                  speech_rate=150, engagement_types=["like", "follow"])
        assert with_eng > base
        # Each engagement type gives 5 points
        assert float(with_eng) - float(base) == pytest.approx(10, abs=1)

    def test_closing_bonus_per_type(self):
        base = score_segment("hello world test message here", ["chitchat"], speech_rate=150)
        with_close = score_segment("hello world test message here", ["chitchat"],
                                    speech_rate=150, closing_types=["time_urgency"])
        assert with_close > base
        assert float(with_close) - float(base) == pytest.approx(5, abs=1)


class TestSpeechRateQuality:
    def test_optimal_speech_rate_gets_max_bonus(self):
        optimal = score_segment("text", ["chitchat"], speech_rate=180)
        too_slow = score_segment("text", ["chitchat"], speech_rate=50)
        assert optimal > too_slow

    def test_marginal_speech_rate_gets_half_bonus(self):
        marginal = score_segment("text", ["chitchat"], speech_rate=90)  # 80-120 range
        too_slow = score_segment("text", ["chitchat"], speech_rate=50)  # out of range
        assert marginal > too_slow


class TestScoreCap:
    def test_score_never_exceeds_95(self):
        # Max possible: teaching(40) + multi-label(5) + long text(10) + 10 eng types(50) = 105
        # But cap should be 95
        high_score = score_segment(
            "x" * 50, ["teaching", "interaction"], speech_rate=180,
            engagement_types=["a", "b", "c", "d", "e", "f", "g", "h", "i", "j"],
        )
        assert float(high_score) <= 95.0


class TestReturnType:
    def test_returns_decimal(self):
        score = score_segment("text", ["chitchat"], speech_rate=150)
        assert isinstance(score, Decimal)
