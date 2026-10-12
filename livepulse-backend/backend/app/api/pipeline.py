"""Pipeline trigger API — kick off (or re-trigger) analysis for a session."""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.auth.current_user import UserContext, get_admin_or_user
from app.services.ownership import get_owned_session
from app.tasks import trigger_analysis

router = APIRouter(prefix="/pipeline", tags=["pipeline"])


@router.post("/{session_id}/trigger")
async def trigger_pipeline(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    ctx: UserContext = Depends(get_admin_or_user),
):
    """Trigger analysis pipeline for a session (async, non-blocking).

    Resets session to 'uploaded' if it was in a failed/locked state,
    then dispatches the pipeline via Celery (USE_CELERY=true) or
    in-process asyncio (USE_CELERY=false). See trigger_analysis docstring.
    """
    session = await get_owned_session(session_id, ctx, db)

    # Reset if stuck in locked state or error
    if session.status in ("processing", "analyzing", "running", "error", "failed"):
        session.status = "uploaded"
        session.error_message = None
        session.analysis_started_at = None
        await db.commit()

    mode = trigger_analysis(session_id)

    return {
        "code": 0,
        "data": {"session_id": session_id, "status": "triggered", "mode": mode},
        "message": "pipeline triggered",
    }
