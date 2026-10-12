"""Embedding-based intent analyzer with Top-K weighted voting and explainability.

5-layer semantic intent classification:
  strong_purchase > purchase_consideration > comparison > hesitation > interaction

Uses Top-K prototype similarity with weighted voting (not just top-1 cosine).
Includes semantic reason generation for explainability.
Falls back to keyword matching when embedding is unavailable or confidence is low.
"""

import logging
import numpy as np

from app.services.embedding.embedding_service import EmbeddingService
from app.services.intent.intent_prototypes import (
    get_intent_manager, INTENT_FUNNEL, SEMANTIC_CONFIDENCE_THRESHOLD,
)

logger = logging.getLogger(__name__)

# Intent display names for reason generation
_INTENT_LABELS = {
    "strong_purchase": "已准备购买",
    "purchase_consideration": "有购买兴趣",
    "comparison": "正在比价",
    "hesitation": "有所犹豫",
    "interaction": "普通互动",
}

# Legacy keyword rules (fallback)
_PURCHASE_KEYWORDS = [
    "怎么买", "多少钱", "链接", "哪里买", "下单", "购买",
    "想要", "好便宜", "划算", "已下单", "拍了", "抢到了",
    "1号", "2号", "3号", "链接", "橱窗", "小黄车",
]


class EmbeddingIntentAnalyzer:
    """Analyze danmaku text for purchase intent using semantic similarity.

    Features:
    - Top-K weighted voting (not just top-1)
    - Full score distribution
    - Semantic reason generation (explainability)
    - Keyword fallback with graceful degradation
    """

    # How many top prototypes to consider per intent type for voting
    TOP_K = 3

    def analyze(self, text: str) -> dict:
        """Analyze a single text for purchase intent with full explainability.

        Returns:
            {
                "intent_type": str,
                "confidence": float,
                "matched_prototypes": [{"text": str, "score": float, "type": str}],
                "semantic_reason": str,
                "top_keywords": list[str],
                "method": "embedding"|"keyword"|"unknown",
                "score_distribution": {type: float},
                "top_matches": [{"type": str, "score": float, "prototype": str}],
            }
        """
        # Try embedding-based analysis first
        result = self._semantic_analyze(text)
        if result and result["confidence"] >= SEMANTIC_CONFIDENCE_THRESHOLD:
            return result

        # Fallback to keyword rules
        kw_result = self._keyword_analyze(text)
        if kw_result:
            if result:
                kw_result["score_distribution"] = result["score_distribution"]
                kw_result["semantic_top"] = result["intent_type"]
                kw_result["semantic_confidence"] = result["confidence"]
                kw_result["top_matches"] = result.get("top_matches", [])
            return kw_result

        dist = result["score_distribution"] if result else {t: 0.0 for t in INTENT_FUNNEL}
        return {
            "intent_type": "interaction",
            "confidence": 0.0,
            "matched_prototypes": [],
            "semantic_reason": "未匹配到任何意图模式",
            "top_keywords": [],
            "method": "unknown",
            "score_distribution": dist,
            "top_matches": [],
        }

    def analyze_batch(self, texts: list[str]) -> list[dict]:
        """Batch analyze with embedding efficiency."""
        if not texts:
            return []

        manager = get_intent_manager()
        if not manager.is_ready or not EmbeddingService.is_available():
            return [self.analyze(t) for t in texts]

        vectors = EmbeddingService.encode(texts)
        if vectors is None:
            return [self.analyze(t) for t in texts]

        results = []
        for text, vec in zip(texts, vectors):
            result = self._classify_vector(vec, manager)
            if result["confidence"] < SEMANTIC_CONFIDENCE_THRESHOLD:
                kw_result = self._keyword_analyze(text)
                if kw_result:
                    kw_result["score_distribution"] = result["score_distribution"]
                    kw_result["top_matches"] = result.get("top_matches", [])
                    result = kw_result
            results.append(result)

        return results

    def _semantic_analyze(self, text: str) -> dict | None:
        manager = get_intent_manager()
        if not manager.is_ready:
            return None

        vec = EmbeddingService.encode(text)
        if vec is None:
            return None

        return self._classify_vector(vec, manager)

    def _classify_vector(self, vec: np.ndarray, manager) -> dict:
        """Classify using Top-K weighted voting across all prototype clusters."""
        distribution = {}
        top_matches = []
        matched_prototypes = []

        for intent_type in INTENT_FUNNEL:
            proto_vecs = manager.get_prototype_vectors(intent_type)
            if proto_vecs is None:
                distribution[intent_type] = 0.0
                continue

            # Compute similarity to ALL prototypes of this type
            sims = np.dot(proto_vecs, vec)

            # Top-K weighted voting: average of top-K similarities
            k = min(self.TOP_K, len(sims))
            top_k_sims = np.sort(sims)[-k:]
            weighted_score = float(np.mean(top_k_sims))
            distribution[intent_type] = round(weighted_score, 4)

            # Track best match for this type
            best_idx = int(np.argmax(sims))
            best_score = float(sims[best_idx])
            best_proto_text = manager.get_prototypes()[intent_type][best_idx]

            top_matches.append({
                "type": intent_type,
                "score": round(best_score, 4),
                "prototype": best_proto_text,
            })

            matched_prototypes.append({
                "text": best_proto_text,
                "score": round(best_score, 4),
                "type": intent_type,
            })

        # Sort top matches by score
        top_matches.sort(key=lambda x: x["score"], reverse=True)

        # Winner is the type with highest weighted score
        best_type = max(distribution, key=distribution.get)
        best_score = distribution[best_type]

        # Generate semantic reason
        semantic_reason = self._generate_reason(best_type, top_matches[:3], matched_prototypes)

        # Extract top keywords from matched prototypes
        top_keywords = [m["text"] for m in matched_prototypes if m["type"] == best_type][:3]

        return {
            "intent_type": best_type,
            "confidence": best_score,
            "matched_prototypes": matched_prototypes,
            "semantic_reason": semantic_reason,
            "top_keywords": top_keywords,
            "method": "embedding",
            "score_distribution": distribution,
            "top_matches": top_matches,
        }

    def _generate_reason(self, intent_type: str, top_matches: list, matched: list) -> str:
        """Generate a human-readable semantic explanation."""
        label = _INTENT_LABELS.get(intent_type, intent_type)
        best_match = matched[0] if matched else None
        proto_text = best_match["text"] if best_match else ""
        score = best_match["score"] if best_match else 0

        if score >= 0.8:
            confidence_word = "明确"
        elif score >= 0.65:
            confidence_word = "有一定"
        else:
            confidence_word = "可能存在"

        reason = f"文本{confidence_word}{label}倾向"
        if proto_text:
            reason += f"，语义接近「{proto_text}」"
        return reason

    def _keyword_analyze(self, text: str) -> dict | None:
        """Legacy keyword-based fallback."""
        text_lower = text.lower()

        for kw in _PURCHASE_KEYWORDS:
            if kw in text_lower:
                return {
                    "intent_type": "purchase_consideration",
                    "confidence": 0.7,
                    "matched_prototypes": [{"text": kw, "score": 0.7, "type": "keyword"}],
                    "semantic_reason": f"命中关键词「{kw}」，判定为购买考虑",
                    "top_keywords": [kw],
                    "method": "keyword",
                    "score_distribution": {},
                    "top_matches": [],
                }

        if "?" in text or "？" in text or "吗" in text:
            return {
                "intent_type": "hesitation",
                "confidence": 0.5,
                "matched_prototypes": [],
                "semantic_reason": "包含疑问语气，判定为犹豫或疑问",
                "top_keywords": [],
                "method": "keyword",
                "score_distribution": {},
                "top_matches": [],
            }

        return None


_analyzer: EmbeddingIntentAnalyzer | None = None


def get_intent_analyzer() -> EmbeddingIntentAnalyzer:
    global _analyzer
    if _analyzer is None:
        _analyzer = EmbeddingIntentAnalyzer()
    return _analyzer