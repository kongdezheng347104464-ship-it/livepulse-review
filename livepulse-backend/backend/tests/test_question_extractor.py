"""Unit tests for app.services.anchor.question_extractor (B4 Dashboard core).

This module is the heart of "观众反复在问什么" — the most differentiated
B4 product value (vs 爱复盘 / AI 核心复盘). The folding rules are simple
but load-bearing; these tests pin them down so future tweaks don't
silently shift what gets surfaced to anchors.

Covered:
  * is_question — question-mark and interrogative-word detection
  * normalize_question — filler prefix strip, punctuation strip, length gate
  * _merge_substrings — shorter canonical absorbs longer form
  * extract_top_questions — full pipeline, including the
    (session_count DESC, session_ratio DESC, message_count DESC)
    sort that makes cross-session recurrence beat same-session spam.
"""
from __future__ import annotations

from app.services.anchor.question_extractor import (
    _merge_substrings,
    extract_top_questions,
    is_question,
    normalize_question,
)


# ---------------------------------------------------------------------------
# is_question — the front gate
# ---------------------------------------------------------------------------

class TestIsQuestion:
    def test_question_mark_full_width(self):
        assert is_question("这款多少钱？")

    def test_question_mark_half_width(self):
        assert is_question("这款多少钱?")

    def test_interrogative_words(self):
        # Every word in the regex should match at least once. Sampling the
        # most common ones rather than enumerating exhaustively.
        for q in [
            "怎么下单",
            "什么色号",
            "哪个适合干皮",
            "多少毫升",
            "为什么这么贵",
            "能不能现货",
            "有没有库存",
            "是不是敏感肌",
        ]:
            assert is_question(q), f"missed interrogative: {q!r}"

    def test_plain_statement_filtered(self):
        # No question mark, no interrogative word → not a question.
        assert not is_question("这个我买过很好用")
        assert not is_question("已下单坐等收货")
        assert not is_question("主播声音真好听")

    def test_empty_string(self):
        assert not is_question("")


# ---------------------------------------------------------------------------
# normalize_question — canonicalization
# ---------------------------------------------------------------------------

class TestNormalizeQuestion:
    def test_strips_question_mark(self):
        # Punctuation removal lets "多少钱" and "多少钱？" group together.
        assert normalize_question("多少钱？") == "多少钱"
        assert normalize_question("多少钱?") == "多少钱"

    def test_strips_at_mention_prefix(self):
        assert normalize_question("@主播 这个多少钱？") == "这个多少钱"

    def test_strips_address_prefix(self):
        assert normalize_question("主播，这个多少钱？") == "这个多少钱"
        assert normalize_question("姐姐 怎么下单") == "怎么下单"

    def test_strips_stacked_filler_prefixes(self):
        # "请问" + "主播姐姐" + actual question. The loop runs up to 3
        # iterations to handle these chains.
        out = normalize_question("请问主播姐姐 这个怎么用？")
        assert out == "这个怎么用", f"got {out!r}"

    def test_strips_punctuation(self):
        assert normalize_question("怎么。下单！") == "怎么下单"

    def test_too_short_returns_none(self):
        # After stripping there should be at least 2 chars; otherwise
        # it's noise (e.g. single emoji + punctuation).
        assert normalize_question("？") is None
        assert normalize_question("a") is None
        assert normalize_question("") is None
        assert normalize_question(None) is None  # defensive

    def test_idempotent(self):
        # normalize(normalize(x)) == normalize(x) — important so
        # already-canonical strings don't drift on re-normalization.
        once = normalize_question("@主播 这款多少钱？")
        twice = normalize_question(once)
        assert once == twice


# ---------------------------------------------------------------------------
# _merge_substrings — collapse verbose into canonical
# ---------------------------------------------------------------------------

class TestMergeSubstrings:
    def _mk(self, message_count, *session_ids):
        return {"message_count": message_count, "session_ids": set(session_ids)}

    def test_shorter_absorbs_longer(self):
        # "这款多少钱" contains "多少钱" → counts merge into the shorter form.
        counts = {
            "多少钱": self._mk(2, "s1"),
            "这款多少钱": self._mk(3, "s2", "s3"),
        }
        out = _merge_substrings(counts)
        assert "这款多少钱" not in out, "longer form should be merged away"
        assert out["多少钱"]["message_count"] == 5  # 2 + 3
        assert out["多少钱"]["session_ids"] == {"s1", "s2", "s3"}

    def test_no_merge_when_not_substring(self):
        counts = {
            "多少钱": self._mk(2, "s1"),
            "怎么下单": self._mk(3, "s2"),
        }
        out = _merge_substrings(counts)
        assert set(out) == {"多少钱", "怎么下单"}

    def test_session_union_not_sum(self):
        # Same session asking both "多少钱" and "粉底多少钱" must count as 1
        # session_id in the merged result, not 2. This is what makes
        # session_count an honest "how many distinct livestreams asked".
        counts = {
            "多少钱": self._mk(1, "s1"),
            "粉底多少钱": self._mk(1, "s1"),
        }
        out = _merge_substrings(counts)
        assert out["多少钱"]["session_ids"] == {"s1"}
        assert out["多少钱"]["message_count"] == 2


# ---------------------------------------------------------------------------
# extract_top_questions — full pipeline
# ---------------------------------------------------------------------------

class TestExtractTopQuestions:
    def test_filters_non_questions(self):
        rows = [
            ("s1", "这个多少钱？"),
            ("s1", "已下单"),          # not a question
            ("s1", "好用吗"),
            ("s1", "声音真好"),         # not a question
        ]
        out = extract_top_questions(rows, total_sessions=1)
        questions = {item["question"] for item in out}
        assert questions == {"这个多少钱", "好用吗"}

    def test_dedup_via_normalize(self):
        # Two ways of asking "多少钱" should collapse into one bucket
        # by the normalization step alone, before substring merge fires.
        rows = [
            ("s1", "多少钱？"),
            ("s1", "多少钱?"),
            ("s1", "多少钱"),
        ]
        out = extract_top_questions(rows, total_sessions=1)
        assert len(out) == 1
        assert out[0]["question"] == "多少钱"
        assert out[0]["message_count"] == 3

    def test_session_count_uses_distinct_sessions(self):
        # 3 sessions × 1 mention each → session_count=3, message_count=3.
        rows = [
            ("s1", "多少钱？"),
            ("s2", "多少钱？"),
            ("s3", "多少钱？"),
        ]
        out = extract_top_questions(rows, total_sessions=3)
        assert out[0]["session_count"] == 3
        assert out[0]["message_count"] == 3

    def test_sort_prioritizes_session_count_over_message_count(self):
        """🔴 product invariant: cross-session recurrence beats same-session spam.

        Without this ordering, an anchor would see "这个真好用" floods
        ranked above "色号怎么选" recurring across 3 sessions — the
        latter is the actionable insight, the former is noise.
        """
        rows = (
            # Question A: spans 3 sessions, 1 mention each → 3 total
            [("s1", "色号怎么选？"), ("s2", "色号怎么选？"), ("s3", "色号怎么选？")]
            # Question B: 1 session, 10 mentions (弹幕暴风) → 10 total
            + [("s1", "多少钱？")] * 10
        )
        out = extract_top_questions(rows, total_sessions=3)
        assert out[0]["question"] == "色号怎么选", \
            f"sort tiebreak wrong: expected '色号怎么选' first, got {out[0]['question']!r}"
        assert out[0]["session_count"] == 3
        assert out[1]["question"] == "多少钱"
        assert out[1]["session_count"] == 1
        assert out[1]["message_count"] == 10

    def test_limit_applied(self):
        rows = [
            (f"s{i}", f"问题{i}？" if i % 2 == 0 else f"怎么用{i}")
            for i in range(20)
        ]
        out = extract_top_questions(rows, total_sessions=20, limit=5)
        assert len(out) == 5

    def test_empty_input_returns_empty_list(self):
        assert extract_top_questions([], total_sessions=0) == []

    def test_all_filtered_returns_empty_list(self):
        # Only non-questions → after filter, no rows survive.
        rows = [("s1", "已下单"), ("s2", "声音真好")]
        assert extract_top_questions(rows, total_sessions=2) == []

    def test_total_sessions_zero_no_div_error(self):
        # Defensive: ratio denominator falls back to 1 to avoid ZeroDivisionError.
        # Should not raise even if a caller passes total_sessions=0 by mistake.
        rows = [("s1", "多少钱？")]
        out = extract_top_questions(rows, total_sessions=0)
        assert len(out) == 1
