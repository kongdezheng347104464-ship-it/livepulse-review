"""Tactic prototype manager - loads and embeds tactic prototypes once."""

import json
import logging
from pathlib import Path

import numpy as np

from app.services.embedding.embedding_service import EmbeddingService

logger = logging.getLogger(__name__)

CONFIG_DIR = Path(__file__).parent.parent / "script_analyzer" / "config"

# 8 tactic types for live stream sales
# Semantic confidence threshold
SEMANTIC_CONFIDENCE_THRESHOLD = 0.55

TACTIC_TYPES = [
    "price_anchor",       # 价格锚定
    "scarcity",           # 稀缺感
    "urgency",            # 时间紧迫
    "social_proof",       # 从众心理
    "risk_reversal",      # 消除顾虑
    "authority",          # 专业背书
    "emotional_trigger",  # 情绪刺激
    "interactive_hook",   # 引导互动
]


class TacticPrototypeManager:
    """Manages tactic prototypes: load JSON, embed once, cache in memory."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._prototypes: dict[str, list[str]] = {}
        self._prototype_vectors: dict[str, np.ndarray] = {}
        self._mean_vectors: dict[str, np.ndarray] = {}
        self._initialized = True

    def load(self) -> bool:
        proto_path = CONFIG_DIR / "tactic_prototypes.json"
        if not proto_path.exists():
            logger.error("Tactic prototypes file not found: %s", proto_path)
            return False

        with open(proto_path, "r", encoding="utf-8") as f:
            self._prototypes = json.load(f)

        self._prototypes = {k: v for k, v in self._prototypes.items() if not k.startswith("_")}

        if not EmbeddingService.is_available():
            logger.warning("Embedding service unavailable, tactic prototypes not embedded")
            return False

        all_texts = []
        type_ranges = {}
        for tactic_type, examples in self._prototypes.items():
            start = len(all_texts)
            all_texts.extend(examples)
            type_ranges[tactic_type] = (start, len(all_texts))

        vectors = EmbeddingService.encode(all_texts)
        if vectors is None:
            return False

        for tactic_type, (start, end) in type_ranges.items():
            type_vecs = vectors[start:end]
            self._prototype_vectors[tactic_type] = type_vecs
            self._mean_vectors[tactic_type] = np.mean(type_vecs, axis=0)
            norm = np.linalg.norm(self._mean_vectors[tactic_type])
            if norm > 0:
                self._mean_vectors[tactic_type] /= norm

        logger.info(
            "Tactic prototypes loaded: %d types, %d total examples",
            len(self._prototypes), sum(len(v) for v in self._prototypes.values()),
        )
        return True

    @property
    def is_ready(self) -> bool:
        return len(self._prototype_vectors) > 0

    def get_prototypes(self) -> dict[str, list[str]]:
        return self._prototypes

    def get_mean_vector(self, tactic_type: str) -> np.ndarray | None:
        return self._mean_vectors.get(tactic_type)

    def get_prototype_vectors(self, tactic_type: str) -> np.ndarray | None:
        return self._prototype_vectors.get(tactic_type)


_manager: TacticPrototypeManager | None = None


def get_tactic_manager() -> TacticPrototypeManager:
    global _manager
    if _manager is None:
        _manager = TacticPrototypeManager()
        _manager.load()
    return _manager