"""Embedding-based sentiment analyzer for livestream danmaku.

Replaces SnowNLP (whose 2013 Sina Weibo dictionary categorized almost all
modern slang — yyds, 绝绝子, 姐妹冲 — as neutral). Uses the same prototype-
similarity approach as the intent/tactic analyzers: embed each text, compare
against per-class prototype centroids, pick the highest-similarity class.

Falls back to "neutral" (with a `method="fallback"` tag) when the embedding
service is unavailable, so the pipeline keeps running.
"""
import json
import logging
from pathlib import Path

import numpy as np

from app.services.embedding.embedding_service import EmbeddingService

logger = logging.getLogger(__name__)

CONFIG_PATH = (
    Path(__file__).parent.parent / "script_analyzer" / "config" / "sentiment_prototypes.json"
)

SENTIMENT_CLASSES = ["positive", "negative", "neutral"]

# Below this top-class similarity, the call is treated as uncertain and
# downgraded to "neutral" rather than guessing. Tuned against the prototype
# set: real positive/negative danmakus typically score ≥0.45 cosine to
# their cluster; neutral has higher baseline similarity to multiple
# classes so picking the top one is brittle below 0.45.
SENTIMENT_CONFIDENCE_THRESHOLD = 0.45


class SentimentPrototypeManager:
    """Singleton prototype loader.

    Loads prototype examples from JSON once, embeds them via the shared
    EmbeddingService, then caches both raw vectors and per-class centroids
    (mean) for fast classification.
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._prototype_vectors: dict[str, np.ndarray] = {}
        self._mean_vectors: dict[str, np.ndarray] = {}
        self._initialized = True

    def load(self) -> bool:
        if not CONFIG_PATH.exists():
            logger.error("Sentiment prototypes not found: %s", CONFIG_PATH)
            return False
        if not EmbeddingService.is_available():
            logger.warning("Embedding unavailable; sentiment analyzer will degrade to neutral")
            return False

        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
        # Strip metadata keys (leading _)
        protos = {k: v for k, v in raw.items() if not k.startswith("_")}

        all_texts: list[str] = []
        ranges: dict[str, tuple[int, int]] = {}
        for cls_name, texts in protos.items():
            start = len(all_texts)
            all_texts.extend(texts)
            ranges[cls_name] = (start, len(all_texts))

        vectors = EmbeddingService.encode(all_texts)
        if vectors is None:
            return False

        for cls_name, (s, e) in ranges.items():
            cls_vecs = vectors[s:e]
            self._prototype_vectors[cls_name] = cls_vecs
            mean = np.mean(cls_vecs, axis=0)
            mean /= np.linalg.norm(mean)
            self._mean_vectors[cls_name] = mean

        logger.info(
            "Sentiment prototypes loaded: %d classes, %d examples",
            len(protos), sum(len(v) for v in protos.values()),
        )
        return True

    @property
    def is_ready(self) -> bool:
        return len(self._mean_vectors) > 0

    def get_mean(self, cls_name: str) -> np.ndarray | None:
        return self._mean_vectors.get(cls_name)


_manager: SentimentPrototypeManager | None = None


def get_sentiment_manager() -> SentimentPrototypeManager:
    global _manager
    if _manager is None:
        _manager = SentimentPrototypeManager()
        _manager.load()
    return _manager


class EmbeddingSentimentAnalyzer:
    """Classify danmaku text into {positive, negative, neutral}."""

    def analyze(self, text: str) -> dict:
        result = self._semantic(text)
        if result is None:
            return {"sentiment": "neutral", "confidence": 0.0, "method": "fallback"}
        return result

    def analyze_batch(self, texts: list[str]) -> list[dict]:
        if not texts:
            return []
        manager = get_sentiment_manager()
        if not manager.is_ready:
            return [{"sentiment": "neutral", "confidence": 0.0, "method": "fallback"} for _ in texts]

        vectors = EmbeddingService.encode(texts)
        if vectors is None:
            return [{"sentiment": "neutral", "confidence": 0.0, "method": "fallback"} for _ in texts]

        return [self._classify_vector(v, manager) for v in vectors]

    def _semantic(self, text: str) -> dict | None:
        manager = get_sentiment_manager()
        if not manager.is_ready:
            return None
        vec = EmbeddingService.encode(text)
        if vec is None:
            return None
        return self._classify_vector(vec, manager)

    @staticmethod
    def _classify_vector(vec: np.ndarray, manager: SentimentPrototypeManager) -> dict:
        scores: dict[str, float] = {}
        for cls in SENTIMENT_CLASSES:
            mean = manager.get_mean(cls)
            scores[cls] = float(np.dot(vec, mean)) if mean is not None else 0.0

        best = max(scores, key=scores.get)
        best_score = scores[best]

        # Uncertainty handling: if the winning class is "neutral", trust it;
        # if it's positive/negative but below threshold, downgrade to neutral
        # to avoid false-flagging neutral text as emotional.
        if best in ("positive", "negative") and best_score < SENTIMENT_CONFIDENCE_THRESHOLD:
            best = "neutral"

        return {
            "sentiment": best,
            "confidence": round(best_score, 4),
            "method": "embedding",
            "score_distribution": {k: round(v, 4) for k, v in scores.items()},
        }


_analyzer: EmbeddingSentimentAnalyzer | None = None


def get_sentiment_analyzer() -> EmbeddingSentimentAnalyzer:
    global _analyzer
    if _analyzer is None:
        _analyzer = EmbeddingSentimentAnalyzer()
    return _analyzer
