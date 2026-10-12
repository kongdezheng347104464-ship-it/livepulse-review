"""Embedding model registry API."""

from fastapi import APIRouter
from app.services.embedding.embedding_service import EmbeddingService

router = APIRouter(prefix="/embedding", tags=["embedding"])


@router.get("/registry")
async def get_embedding_registry():
    """Get current embedding model metadata."""
    info = EmbeddingService.get_registry_info()
    return {"code": 0, "data": info, "message": "ok"}


@router.get("/health")
async def embedding_health():
    """Check if embedding service is available."""
    available = EmbeddingService.is_available()
    info = EmbeddingService.get_registry_info() if available else {}
    return {
        "code": 0,
        "data": {"available": available, **info},
        "message": "ok" if available else "unavailable",
    }