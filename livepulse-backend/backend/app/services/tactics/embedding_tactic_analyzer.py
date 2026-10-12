"""Embedding-based tactic analyzer with keyword fallback.

8-type tactic classification with strength scoring.
Falls back to closing.json keyword rules when embedding is unavailable.
"""

import logging
import numpy as np

from app.services.embedding.embedding_service import EmbeddingService
from app.services.tactics.tactic_prototypes import (
    get_tactic_manager, TACTIC_TYPES, SEMANTIC_CONFIDENCE_THRESHOLD,
)
from app.services.script_analyzer.utils.config_loader import load_json_config

logger = logging.getLogger(__name__)

# Mapping from old closing.json keys to new tactic types
_CLOSING_TO_TACTIC = {
    "time_urgency": "urgency",
    "stock_urgency": "scarcity",
    "price_comparison": "price_anchor",
    "guarantee": "risk_reversal",
}


class EmbeddingTacticAnalyzer:
    """Analyze host speech for sales tactics using semantic similarity.

    Returns per-tactic strength (not just presence/absence),
    supporting later ranking by conversion power.
    """

    def analyze(self, text: str) -> dict:
        """Analyze a speech segment for sales tactics.

        Args:
            text: Speech segment text.

        Returns:
            {
                "tactics": [{"type": str, "strength": float, "matched_prototype": str}],
                "method": "embedding"|"keyword"|"hybrid",
                "score_distribution": {type: float, ...},
            }
        """
        semantic = self._semantic_analyze(text)
        keyword = self._keyword_analyze(text)

        if not semantic and not keyword:
            return {
                "tactics": [],
                "method": "none",
                "score_distribution": {t: 0.0 for t in TACTIC_TYPES},
            }

        # Merge: if both found, prefer semantic but boost with keyword matches
        if semantic and keyword:
            semantic_types = {t["type"] for t in semantic["tactics"]}
            for kw_tactic in keyword["tactics"]:
                if kw_tactic["type"] not in semantic_types:
                    semantic["tactics"].append(kw_tactic)
                else:
                    # Boost strength for keyword-confirmed tactics
                    for t in semantic["tactics"]:
                        if t["type"] == kw_tactic["type"]:
                            t["strength"] = min(1.0, t["strength"] + 0.1)
            semantic["method"] = "hybrid"
            # Merge distributions
            for ttype, score in keyword.get("score_distribution", {}).items():
                if score > semantic["score_distribution"].get(ttype, 0):
                    semantic["score_distribution"][ttype] = score
            return semantic

        return semantic or keyword

    def analyze_batch(self, texts: list[str]) -> list[dict]:
        """Batch analyze speech segments for tactics."""
        if not texts:
            return []

        manager = get_tactic_manager()
        if not manager.is_ready or not EmbeddingService.is_available():
            return [self.analyze(t) for t in texts]

        vectors = EmbeddingService.encode(texts)
        if vectors is None:
            return [self.analyze(t) for t in texts]

        results = []
        for text, vec in zip(texts, vectors):
            result = self._classify_vector(vec, manager)
            # Also run keyword and merge
            keyword = self._keyword_analyze(text)
            if keyword and keyword["tactics"]:
                existing = {t["type"] for t in result["tactics"]}
                for kw_t in keyword["tactics"]:
                    if kw_t["type"] not in existing:
                        result["tactics"].append(kw_t)
                    else:
                        for t in result["tactics"]:
                            if t["type"] == kw_t["type"]:
                                t["strength"] = min(1.0, t["strength"] + 0.1)
                result["method"] = "hybrid"
            results.append(result)

        return results

    def _semantic_analyze(self, text: str) -> dict | None:
        manager = get_tactic_manager()
        if not manager.is_ready:
            return None

        vec = EmbeddingService.encode(text)
        if vec is None:
            return None

        return self._classify_vector(vec, manager)

    def _classify_vector(self, vec: np.ndarray, manager) -> dict:
        distribution = {}
        matched_protos = {}

        for tactic_type in TACTIC_TYPES:
            mean_vec = manager.get_mean_vector(tactic_type)
            if mean_vec is not None:
                sim = float(np.dot(vec, mean_vec))
                distribution[tactic_type] = round(sim, 4)
            else:
                distribution[tactic_type] = 0.0

        # Find tactics above threshold
        tactics = []
        for tactic_type in sorted(distribution, key=distribution.get, reverse=True):
            score = distribution[tactic_type]
            if score < SEMANTIC_CONFIDENCE_THRESHOLD:
                continue

            # Find closest prototype
            proto_vecs = manager.get_prototype_vectors(tactic_type)
            matched_proto = ""
            if proto_vecs is not None:
                sims = np.dot(proto_vecs, vec)
                best_idx = int(np.argmax(sims))
                matched_proto = manager.get_prototypes()[tactic_type][best_idx]

            tactics.append({
                "type": tactic_type,
                "strength": min(1.0, score),
                "matched_prototype": matched_proto,
            })

        return {
            "tactics": tactics,
            "method": "embedding",
            "score_distribution": distribution,
        }

    def _keyword_analyze(self, text: str) -> dict | None:
        """Legacy keyword-based fallback using closing.json."""
        text_lower = text.lower()
        closing_config = load_json_config("closing.json")

        found = {}
        for old_key, patterns in closing_config.items():
            if old_key.startswith("_"):
                continue
            if any(p in text_lower for p in patterns):
                new_key = _CLOSING_TO_TACTIC.get(old_key, old_key)
                found[new_key] = 0.7  # Default strength for keyword matches

        if not found:
            # Check engagement keywords for interactive_hook
            eng_config = load_json_config("engagement.json")
            for prompt_type, patterns in eng_config.items():
                if prompt_type.startswith("_"):
                    continue
                if any(p in text_lower for p in patterns):
                    found["interactive_hook"] = 0.65
                    break

        if not found:
            return None

        tactics = [
            {"type": t, "strength": s, "matched_prototype": ""}
            for t, s in found.items()
        ]

        dist = {t: 0.0 for t in TACTIC_TYPES}
        for t, s in found.items():
            dist[t] = s

        return {
            "tactics": tactics,
            "method": "keyword",
            "score_distribution": dist,
        }


_analyzer: EmbeddingTacticAnalyzer | None = None


def get_tactic_analyzer() -> EmbeddingTacticAnalyzer:
    global _analyzer
    if _analyzer is None:
        _analyzer = EmbeddingTacticAnalyzer()
    return _analyzer