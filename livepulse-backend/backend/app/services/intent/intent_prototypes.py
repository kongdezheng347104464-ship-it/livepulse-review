"""Intent prototype manager - loads and embeds prototypes once at startup."""

import json
import logging
from pathlib import Path

import numpy as np

from app.services.embedding.embedding_service import EmbeddingService

logger = logging.getLogger(__name__)

CONFIG_DIR = Path(__file__).parent.parent / "script_analyzer" / "config"

# 5-layer intent hierarchy (from weakest to strongest purchase signal)
INTENT_FUNNEL = [
    "interaction",          # 普通互动
    "hesitation",           # 犹豫
    "comparison",           # 比价
    "purchase_consideration",  # 购买考虑
    "strong_purchase",      # 强购买
]

# Semantic confidence threshold - below this, fallback to keyword system
SEMANTIC_CONFIDENCE_THRESHOLD = 0.55


class IntentPrototypeManager:
    """Manages intent prototypes: load JSON, embed once, cache in memory.

    Prototypes are loaded and embedded on first use, then reused for all
    subsequent intent analyses. This avoids re-reading JSON and re-encoding
    on every call.
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
        self._prototypes: dict[str, list[str]] = {}
        self._prototype_vectors: dict[str, np.ndarray] = {}  # {type: (n_protos, dim)}
        self._mean_vectors: dict[str, np.ndarray] = {}       # {type: (dim,)}
        self._initialized = True

    def load(self) -> bool:
        """Load prototypes from JSON and embed them.

        Returns True if successful, False otherwise.
        """
        proto_path = CONFIG_DIR / "intent_prototypes.json"
        if not proto_path.exists():
            logger.error("Intent prototypes file not found: %s", proto_path)
            return False

        with open(proto_path, "r", encoding="utf-8") as f:
            self._prototypes = json.load(f)

        # Remove metadata keys
        self._prototypes = {k: v for k, v in self._prototypes.items() if not k.startswith("_")}

        if not EmbeddingService.is_available():
            logger.warning("Embedding service unavailable, prototypes not embedded")
            return False

        # Batch embed all prototypes
        all_texts = []
        type_ranges = {}
        for intent_type, examples in self._prototypes.items():
            start = len(all_texts)
            all_texts.extend(examples)
            type_ranges[intent_type] = (start, len(all_texts))

        vectors = EmbeddingService.encode(all_texts)
        if vectors is None:
            return False

        for intent_type, (start, end) in type_ranges.items():
            type_vecs = vectors[start:end]
            self._prototype_vectors[intent_type] = type_vecs
            self._mean_vectors[intent_type] = np.mean(type_vecs, axis=0)
            self._mean_vectors[intent_type] /= np.linalg.norm(self._mean_vectors[intent_type])

        logger.info(
            "Intent prototypes loaded: %d types, %d total examples",
            len(self._prototypes), sum(len(v) for v in self._prototypes.values()),
        )
        return True

    @property
    def is_ready(self) -> bool:
        return len(self._prototype_vectors) > 0

    def get_prototypes(self) -> dict[str, list[str]]:
        return self._prototypes

    def get_mean_vector(self, intent_type: str) -> np.ndarray | None:
        return self._mean_vectors.get(intent_type)

    def get_prototype_vectors(self, intent_type: str) -> np.ndarray | None:
        return self._prototype_vectors.get(intent_type)

    def intent_types(self) -> list[str]:
        return list(self._prototypes.keys())


# Global singleton accessor
_manager: IntentPrototypeManager | None = None


def get_intent_manager() -> IntentPrototypeManager:
    global _manager
    if _manager is None:
        _manager = IntentPrototypeManager()
        _manager.load()
    return _manager