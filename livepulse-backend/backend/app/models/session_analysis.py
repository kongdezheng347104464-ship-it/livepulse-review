"""SessionAnalysis — LLM-produced range analysis over selected
ScriptSegments.

Why a SEPARATE table from session_notes (decided 2026-05-29):
  Notes are user-authored, AI analyses are machine-authored. Stuffing
  both in session_notes would force every UI consumer to filter by
  origin, would muddle "list my notes" semantics, and would prevent
  future per-anchor AI insight aggregation queries from being clean.

  Keeping them separate also means future destructive operations
  (rerun analysis with newer prompt, batch-delete analyses cheaper
  than $X token cost, A/B prompt comparison) don't touch user assets.

Schema decisions:
  - segment_ids stored as JSONB (Postgres) — denormalised list of
    script_segment.id values selected by the user. We do NOT use a
    junction table (session_analysis_segments). Rationale: analyses
    are immutable after creation (MVP), the list is read-only as a
    bag of references, no need to JOIN/index against it.

  - prompt_version is a string ("v1", "v2", ...). When the prompt
    template changes, bump this in code. Stored per row so a future
    "rerun all analyses against new prompt" job knows what's stale.

  - result is JSONB with the structured Claude response:
        { summary, keep, remove, optimize }
    Schema may grow over time — JSONB lets us evolve without alembic
    churn. UI tolerates missing fields.

  - token_usage is an int (input + output combined). Cheap diagnostic
    field for cost monitoring; not surfaced to the user in MVP.

ON DELETE CASCADE on session_id: an analysis is meaningless without
its session (refers to segments by id which lose meaning post-delete).
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.mixins import OwnedMixin


class SessionAnalysis(Base, OwnedMixin):
    __tablename__ = "session_analyses"
    __table_args__ = (
        Index("ix_session_analyses_session_time", "session_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4()),
    )
    session_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("live_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )

    # List of script_segment.id values the user selected. Stored as a
    # JSONB array of strings. Order preserved (matches user selection
    # order, which is by timestamp ASC in our frontend).
    segment_ids: Mapped[list] = mapped_column(JSONB, nullable=False)

    # Versioned identifier of the prompt template used to generate
    # `result`. Bumped in code when the template changes.
    prompt_version: Mapped[str] = mapped_column(String(20), nullable=False)

    # Structured result from the LLM. Shape:
    #   { summary: str, keep: [str], remove: [str], optimize: [str] }
    # may add fields later (conversion_breakpoints, suggested_rewrite)
    # without schema migration.
    result: Mapped[dict] = mapped_column(JSONB, nullable=False)

    # Combined input + output tokens. Cheap to record, useful for cost
    # monitoring once we see real usage volume. 0 if the provider
    # didn't return usage stats.
    token_usage: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # The earliest start_sec / latest end_sec across `segment_ids`.
    # Denormalised at create time so the workspace can "jump to range
    # start" + render time chips without joining back to script_segments.
    # Nullable to tolerate legacy rows (none exist at v1.5 ship — feature
    # had no users yet — but the migration leaves them null rather than
    # backfilling, since segment_ids might reference rows that have since
    # been deleted from another session).
    range_start_sec: Mapped[int | None] = mapped_column(Integer, nullable=True)
    range_end_sec: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    session = relationship("LiveSession", back_populates="analyses")
