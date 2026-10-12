"""Tests for keyword_extractor — jieba segmentation with stopword filtering.

Edge cases: empty text, pure punctuation, single characters, stopwords.
"""
from app.services.script_analyzer.extractors.keyword_extractor import extract_keywords


class TestBasicExtraction:
    def test_normal_text_returns_keywords(self):
        text = "这个隔离霜非常好用，推荐大家购买"
        result = extract_keywords(text, top_k=5)
        assert len(result) > 0
        assert isinstance(result, list)
        assert all(isinstance(w, str) for w in result)

    def test_top_k_respected(self):
        text = "今天天气真好啊我们去公园散步吧"
        result = extract_keywords(text, top_k=3)
        assert len(result) <= 3


class TestEmptyAndShortText:
    def test_empty_text_returns_empty(self):
        result = extract_keywords("", top_k=5)
        assert result == []

    def test_single_char_text_returns_empty(self):
        """Single characters are filtered out."""
        result = extract_keywords("好", top_k=5)
        assert result == []

    def test_whitespace_only_returns_empty(self):
        result = extract_keywords("   \n  \t  ", top_k=5)
        assert result == []


class TestPunctuationFiltering:
    def test_pure_punctuation_returns_empty(self):
        result = extract_keywords("，。！？、；：", top_k=5)
        assert result == []

    def test_mixed_text_excludes_punctuation(self):
        result = extract_keywords("你好，世界！", top_k=5)
        assert "，" not in result
        assert "！" not in result
        # Chinese words should be extracted
        assert len(result) >= 1


class TestStopwordFiltering:
    def test_common_stopwords_filtered(self):
        """Helper words like 的/了/是 should not appear."""
        text = "这是我们的目标是什么呢"
        result = extract_keywords(text, top_k=10)
        # Common stopwords should be absent
        assert "的" not in result
        assert "了" not in result
        assert "是" not in result
