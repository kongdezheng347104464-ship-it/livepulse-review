"""API Key authentication via FastAPI dependency injection.

Uses APIKeyHeader + Depends() — no hand-written middleware needed.
Health endpoint is exempt. OPTIONS preflight is handled by CORS middleware
(which runs before Depends evaluation).
"""
from fastapi import HTTPException, Request, Security
from fastapi.security import APIKeyHeader
from starlette.status import HTTP_403_FORBIDDEN

from app.config import get_settings

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def validate_api_key(
    request: Request,
    api_key: str | None = Security(api_key_header),
) -> str:
    """Validate API key from request header.

    Returns the validated key, or raises 403.
    Health endpoint is exempt.

    Usage:
        @router.get("/sessions/")
        async def list_sessions(_, key: str = Depends(validate_api_key)):
    """
    # Health endpoint is public
    if request.url.path in ("/api/v1/health", "/api/v1/health/"):
        return "public"

    settings = get_settings()

    # Empty API_KEY is only acceptable in debug mode — otherwise refuse all requests
    # to prevent accidentally exposing an unauthenticated service to the network.
    if not settings.api_key:
        if settings.debug:
            return "dev-no-auth"
        raise HTTPException(
            status_code=HTTP_403_FORBIDDEN,
            detail="API_KEY not configured on server (refusing all requests). "
                   "Set API_KEY in .env, or enable DEBUG=true for local dev.",
        )

    if not api_key or api_key != settings.api_key:
        raise HTTPException(
            status_code=HTTP_403_FORBIDDEN,
            detail="Invalid or missing API key",
        )

    return api_key
