"""Unit tests for _extract_like_count — B4 cumulative-likes OCR parser.

Pins down the regex behavior we worked out empirically against:
  * Desktop 抖音 web (OBS recording, "11.1万本场点赞" badge in top-left)
  * 手机 抖音 (similar layout, smaller / cropped variant)
  * Common OCR misreads (character splitting, swapped order, comma separator)

The negative-control case (`为主播点赞了` per-user system event) is critical:
the old wrong B4 data path counted those, and the new regex must NEVER
match them or we re-introduce the bug we just fixed.

These tests are pure regex — no Aliyun API calls — so they survive
service expiry / network outage / migration to a different OCR vendor.
"""
import pytest

from app.services.aliyun_ocr_service import _extract_like_count, _parse_like_count


class TestExtractLikeCount:
    def test_desktop_obs_format(self):
        """Owner's OBS screenshot: 小鱼儿教化妆 / 11.1万本场点赞."""
        texts = ["小鱼儿教化妆", "11.1万本场点赞", "加粉丝团"]
        assert _extract_like_count(texts) == 111_000

    def test_phone_format(self):
        """Owner's phone screenshot: 小林会长 / 2.5万本场点赞."""
        texts = ["小林会长", "2.5万本场点赞", "小时榜"]
        assert _extract_like_count(texts) == 25_000

    def test_split_across_lines(self):
        """OCR sometimes breaks number and label onto separate lines."""
        texts = ["小鱼儿教化妆", "11.1万", "本场点赞"]
        assert _extract_like_count(texts) == 111_000

    def test_ignores_adjacent_hourly_rank_number(self):
        """🔴 regression guard (2026-06-03): the real OCR blob is
        '11.1万本场点赞 100+' where '100+' is the 小时榜 badge. An earlier
        'label-then-number' regex variant wrongly matched '本场点赞 100'.
        We must extract 111000 (the number BEFORE the label), never 100.
        """
        texts = ["11.1万本场点赞", "100+", ")+", "1/12"]
        assert _extract_like_count(texts) == 111_000

    def test_full_frame_blob(self):
        """Exact reproduction of the owner's frame-0 OCR output — the
        single joined blob 抖音 web returns. Must extract 111000."""
        texts = [
            "小鱼儿教化妆 人气榜 基础展馆 只此青绿礼物 二在线观众·954② "
            "加粉丝团 11.1万本场点赞 100+ )+ 1/12 5.28-6遇见中式恩漫 全部",
        ]
        assert _extract_like_count(texts) == 111_000

    def test_small_streamer_no_unit(self):
        """Sub-万 likes display as bare integer in 抖音 UI."""
        texts = ["小主播", "9982本场点赞"]
        assert _extract_like_count(texts) == 9982

    def test_top_tier_streamer_yi_unit(self):
        """Top-tier broadcasters can hit 亿-level cumulative likes."""
        texts = ["头部主播", "5亿本场点赞"]
        assert _extract_like_count(texts) == 500_000_000

    def test_does_not_match_per_user_like_event(self):
        """🔴 critical regression guard: the OLD wrong data source counted
        per-user '为主播点赞了' SYSTEM events. The new regex MUST NOT
        match those — re-matching them would re-introduce the very bug
        this whole change was meant to fix.
        """
        texts = ["张三 为主播点赞了", "李四 为主播点赞了"]
        assert _extract_like_count(texts) is None

    def test_comma_separated_integer(self):
        """Bare integer can come with locale comma separator (number
        before label, as the real UI always renders it)."""
        texts = ["某主播", "1,500本场点赞"]
        assert _extract_like_count(texts) == 1500

    def test_character_level_split_of_label(self):
        """OCR occasionally puts spaces between every char of the 4-char
        label. The `\\s*` between each char in the regex handles this.
        Number stays before the label per real UI layout.
        """
        texts = ["小主播", "25,000本 场 点 赞"]
        assert _extract_like_count(texts) == 25_000

    def test_coexists_with_viewer_count_line(self):
        """The two stats live in the same OCR output — extracting one
        must not be confused by the other being present."""
        texts = ["在线观众·1234", "11.1万本场点赞"]
        assert _extract_like_count(texts) == 111_000

    def test_empty_input(self):
        assert _extract_like_count([]) is None

    def test_no_match(self):
        """Text with no 本场点赞 anywhere returns None (gap, not 0)."""
        texts = ["其他文本", "抖音直播", "随机内容"]
        assert _extract_like_count(texts) is None


class TestParseLikeCount:
    """Edge cases for the number-and-unit parser specifically."""

    def test_万_multiplier(self):
        assert _parse_like_count("11.1", "万") == 111_000

    def test_亿_multiplier(self):
        assert _parse_like_count("1.5", "亿") == 150_000_000

    def test_no_unit_integer(self):
        assert _parse_like_count("9982", "") == 9982

    def test_no_unit_with_comma(self):
        assert _parse_like_count("1,500", "") == 1500

    def test_garbage_input_returns_none(self):
        assert _parse_like_count("not a number", "万") is None

    def test_empty_string_returns_none(self):
        assert _parse_like_count("", "") is None

    def test_implausible_huge_value_rejected(self):
        """Sanity ceiling at 1 万亿 (10^12) — anything past is OCR junk
        (e.g. misread of an unrelated number near the badge)."""
        # 99,999 * 1e8 = 1e13 > ceiling
        assert _parse_like_count("99999", "亿") is None
