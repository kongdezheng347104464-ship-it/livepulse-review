"""Keyword extractor using jieba.lcut + Counter.

Avoids jieba.analyse.extract_tags which caused MemoryError on Windows.
Uses stopword filtering for cleaner results.
"""
from collections import Counter
from ..utils.config_loader import load_stopwords


def extract_keywords(text: str, top_k: int = 10) -> list[str]:
    """Extract keywords from text using jieba segmentation.

    Filters out:
    - Single characters
    - Pure punctuation
    - Stopwords

    Args:
        text: Input text.
        top_k: Number of top keywords to return.

    Returns:
        List of keywords sorted by frequency.
    """
    import jieba

    stopwords = load_stopwords()
    words = jieba.lcut(text)

    filtered = [
        w for w in words
        if len(w) >= 2
        and w not in stopwords
        and not all(ch in '，。！？、；：""''（）…—·' for ch in w)
    ]

    return [w for w, _ in Counter(filtered).most_common(top_k)]
