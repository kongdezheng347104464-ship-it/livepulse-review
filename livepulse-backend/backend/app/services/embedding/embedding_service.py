"""Embedding service - singleton model loader with batch encode, cache, and registry."""

import logging
import hashlib
import os
import time
import sys
from pathlib import Path
from typing import Union

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
#  Model cache directory + HuggingFace endpoint
# ---------------------------------------------------------------------------
# Local cache for SentenceTransformer / HF Hub downloads. Was hard-coded to
# "f:/models" (Windows path) which silently fell back to re-downloading from
# HuggingFace on Mac — but cdn-lfs.huggingface.co is blocked in our network,
# so each cold start crashed mid-download and the embedding service degraded
# to "unavailable", which is the upstream cause of the QA 2026-05-29
# "Semantic tactic analysis failed: name 'nn' is not defined" silent failure.
#
# Switching to ~/.cache/workbuddy_models. Also pointing HF Hub to the
# mirror via HF_ENDPOINT (set BEFORE any transformers / huggingface_hub
# import — we set it on module import so it's effective for the lazy
# sentence_transformers import below).
_MODEL_CACHE_DIR = str(Path.home() / ".cache" / "workbuddy_models")
os.makedirs(_MODEL_CACHE_DIR, exist_ok=True)

# hf-mirror.com is the de facto China-friendly HuggingFace mirror.
# Only set if user hasn't already configured a different endpoint, so
# operators can override via env (e.g. corporate proxy).
if not os.environ.get("HF_ENDPOINT"):
    os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

# ---------------------------------------------------------------------------
#  Embedding cache (in-memory LRU)
# ---------------------------------------------------------------------------

_embedding_cache: dict[str, np.ndarray] = {}
_CACHE_MAX = 10000


def _cache_key(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def _cache_get(text: str) -> Union[np.ndarray, None]:
    return _embedding_cache.get(_cache_key(text))


def _cache_put(text: str, vec: np.ndarray) -> None:
    if len(_embedding_cache) >= _CACHE_MAX:
        keys = list(_embedding_cache.keys())
        for k in keys[: _CACHE_MAX // 5]:
            del _embedding_cache[k]
    _embedding_cache[_cache_key(text)] = vec


# ---------------------------------------------------------------------------
#  EmbeddingModelRegistry - model metadata and selection
# ---------------------------------------------------------------------------

class EmbeddingModelRegistry:
    """Tracks loaded model metadata for observability and future rerank support."""

    _registry: dict = {}

    @classmethod
    def register(cls, model_name: str, vector_dim: int, language: str = "zh") -> None:
        mem_mb = 0
        try:
            import psutil
            mem_mb = round(psutil.Process().memory_info().rss / 1024 / 1024, 1)
        except Exception:
            pass
        cls._registry = {
            "model_name": model_name,
            "vector_dim": vector_dim,
            "language": language,
            "loaded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "memory_usage_mb": mem_mb,
            "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        }
        logger.info("Model registry: %s", cls._registry)

    @classmethod
    def get_info(cls) -> dict:
        return dict(cls._registry)

    @classmethod
    def get_dim(cls) -> int:
        return cls._registry.get("vector_dim", 0)


# ---------------------------------------------------------------------------
#  EmbeddingService singleton
# ---------------------------------------------------------------------------

class EmbeddingService:
    """Global singleton embedding service.

    - Lazy-loads the model on first use
    - All analyzers share the same model instance
    - Supports batch encode with cache
    - normalize_embeddings=True for cosine similarity
    - Registry for model metadata
    """

    _instance = None
    _model = None
    _model_name: str = ""
    _available: bool | None = None

    # QA 2026-05-30: 模型源 = ModelScope (国内可达,不依赖 hf-mirror redirect)。
    # 元组顺序:(huggingface-style name 给 sentence-transformers / Registry,
    #          modelscope_id, 语言, **本地路径**)。
    # 优先本地路径,本地缺失则尝试 modelscope SDK 下载(线上一次,后续永远 offline)。
    # 不再尝试 HuggingFace 直接下载 — cdn-lfs 被墙 + hf-mirror redirect 库
    # bug,实测两次失败。
    PREFERRED_MODELS = [
        {
            "registry_name": "BAAI/bge-small-zh-v1.5",
            "modelscope_id": "AI-ModelScope/bge-small-zh-v1.5",
            "lang": "zh",
            "local_subpath": "AI-ModelScope/bge-small-zh-v1.5",
        },
    ]

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def get_model(cls):
        if cls._model is not None:
            return cls._model

        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            logger.warning("sentence-transformers not installed")
            cls._available = False
            return None

        from pathlib import Path
        cache_root = Path(_MODEL_CACHE_DIR)

        for cfg in cls.PREFERRED_MODELS:
            local_dir = cache_root / cfg["local_subpath"]
            # 1. 优先本地 (ModelScope 之前已下载的 cache)
            if local_dir.is_dir() and any(local_dir.iterdir()):
                try:
                    logger.info("Loading embedding model FROM LOCAL: %s", local_dir)
                    cls._model = SentenceTransformer(str(local_dir))
                    cls._model_name = cfg["registry_name"]
                    cls._available = True
                    dim = cls._model.get_sentence_embedding_dimension()
                    EmbeddingModelRegistry.register(cfg["registry_name"], dim, cfg["lang"])
                    logger.info("Embedding model loaded LOCAL: %s (dim=%d)",
                                cfg["registry_name"], dim)
                    return cls._model
                except Exception as e:
                    logger.exception("Local load failed (%s): %s", local_dir, e)

            # 2. 本地缺失 → 尝试 ModelScope 拉模型(一次性,后续走本地)
            try:
                from modelscope.hub.snapshot_download import snapshot_download
                logger.info("Local cache miss, downloading from ModelScope: %s",
                            cfg["modelscope_id"])
                snapshot_download(cfg["modelscope_id"], cache_dir=str(cache_root))
                if local_dir.is_dir() and any(local_dir.iterdir()):
                    cls._model = SentenceTransformer(str(local_dir))
                    cls._model_name = cfg["registry_name"]
                    cls._available = True
                    dim = cls._model.get_sentence_embedding_dimension()
                    EmbeddingModelRegistry.register(cfg["registry_name"], dim, cfg["lang"])
                    logger.info("Embedding model loaded MODELSCOPE: %s (dim=%d)",
                                cfg["registry_name"], dim)
                    return cls._model
            except Exception as e:
                # exception() not warning() so a full traceback lands in
                # pipeline.log — this is what would have caught QA 2026-05-29
                # B-3 (name 'nn' is not defined) on the first cold start
                # instead of after a 22-min pipeline.
                logger.exception("ModelScope download/load failed for %s: %s",
                                 cfg["modelscope_id"], e)
                continue

        logger.error("All embedding models failed to load (local + ModelScope)")
        cls._available = False
        return None

    @classmethod
    def is_available(cls) -> bool:
        if cls._available is None:
            cls.get_model()
        return cls._available is True

    @classmethod
    def encode(cls, texts: Union[str, list[str]], batch_size: int = 64) -> Union[np.ndarray, None]:
        model = cls.get_model()
        if model is None:
            return None

        single = isinstance(texts, str)
        if single:
            texts = [texts]

        results: list[np.ndarray | None] = []
        uncached_indices: list[int] = []
        uncached_texts: list[str] = []

        for i, t in enumerate(texts):
            cached = _cache_get(t)
            if cached is not None:
                results.append(cached)
            else:
                results.append(None)
                uncached_indices.append(i)
                uncached_texts.append(t)

        if uncached_texts:
            try:
                vectors = model.encode(
                    uncached_texts,
                    batch_size=batch_size,
                    normalize_embeddings=True,
                    show_progress_bar=False,
                )
                for idx, text, vec in zip(uncached_indices, uncached_texts, vectors):
                    vec = np.array(vec, dtype=np.float32)
                    _cache_put(text, vec)
                    results[idx] = vec
            except Exception as e:
                logger.error("Batch encode failed: %s", e)
                return None

        stacked = np.stack(results)
        return stacked[0] if single else stacked

    @classmethod
    def clear_cache(cls):
        _embedding_cache.clear()

    @classmethod
    def get_registry_info(cls) -> dict:
        return EmbeddingModelRegistry.get_info()