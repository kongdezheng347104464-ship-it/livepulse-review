"""Danmaku statistical analyzer.

Intent classification is deferred to the embedding intent analyzer; sentiment
classification is now also embedding-based via
`sentiment.sentiment_analyzer`. The previous SnowNLP path classified almost
all modern livestream slang ("yyds", "绝绝子", "姐妹冲") as neutral because
its dictionary dates to 2013 Sina Weibo data — net-bad signal.

The legacy keyword `detect_purchase_intent` was also removed: "我不想下单"
contained "下单" and tagged false-positive, breaking conversion windows.
"""
import logging
from collections import Counter

logger = logging.getLogger(__name__)


def analyze_danmakus(records: list[dict]) -> dict:
    """Build statistical summary over danmaku records.

    Sentiment is computed via the embedding sentiment analyzer in batch.
    Intent enrichment is still deferred to the downstream embedding intent
    analyzer in pipeline._step_danmaku.

    Args:
        records: List of {"timestamp_sec": int, "content": str}.

    Returns:
        Dict with summary stats and enriched record list. `intent` is
        left None — filled by embedding pass.
    """
    if not records:
        return _empty_result()

    from app.services.sentiment.sentiment_analyzer import get_sentiment_analyzer

    texts = [r["content"] for r in records]
    sent_results = get_sentiment_analyzer().analyze_batch(texts)

    enriched = []
    sentiments = Counter()
    for r, sr in zip(records, sent_results):
        sentiment = sr["sentiment"]
        sentiments[sentiment] += 1
        enriched.append({
            "timestamp_sec": r["timestamp_sec"],
            "content": r["content"],
            "sentiment": sentiment,
            "intent": None,  # filled by embedding pass
        })

    total = len(records)
    minute_buckets: dict[int, int] = {}
    for r in records:
        minute = r["timestamp_sec"] // 60
        minute_buckets[minute] = minute_buckets.get(minute, 0) + 1

    peak_minute = max(minute_buckets, key=minute_buckets.get) if minute_buckets else 0
    peak_count = minute_buckets.get(peak_minute, 0)

    return {
        "total": total,
        "peak_minute": peak_minute,
        "peak_count": peak_count,
        "avg_per_minute": round(total / max(len(minute_buckets), 1), 1),
        "sentiment_distribution": {
            "positive": sentiments.get("positive", 0),
            "neutral": sentiments.get("neutral", 0),
            "negative": sentiments.get("negative", 0),
        },
        # purchase_intent_count / question_count now derived downstream from
        # the embedding intent taxonomy (strong_purchase, purchase_consideration,
        # hesitation, comparison, interaction) — see _build_semantic_summary.
        "purchase_intent_count": 0,
        "question_count": 0,
        "records": enriched,
    }


def _empty_result() -> dict:
    return {
        "total": 0,
        "peak_minute": 0,
        "peak_count": 0,
        "avg_per_minute": 0,
        "sentiment_distribution": {"positive": 0, "neutral": 0, "negative": 0},
        "purchase_intent_count": 0,
        "question_count": 0,
        "records": [],
    }
