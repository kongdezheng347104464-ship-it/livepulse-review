"""SessionNote — anchor's review notes pinned at specific timestamps.

Why a dedicated table (not a JSON column on live_sessions):
  Notes are user-authored content, anchor-by-anchor, written
  asynchronously during replay sessions that can stretch over hours.
  JSON-column writes would mean read-modify-write the entire session
  row on every save, fighting the pipeline's writes to the same row
  for status / composite_score / analysis_summary. A row-per-note
  table eliminates the contention entirely.

  Equally important for the product: each note is a navigable
  bookmark on the Replay Timeline Workspace. Queries like "list all
  notes for this session ordered by timestamp" or future "all notes
  by this anchor across her last 30 days" are trivially expressible
  on a row table; awkward to impossible inside a JSON array.

Schema-level decisions:
  - timestamp_sec is INTEGER (PG INT4). Notes are pinned to one-second
    resolution — finer granularity is meaningless for review use
    (the user thinks "at 15:32" not "at 15:32.471").
  - note_text is TEXT (no length cap). Anchors write thoughts, not
    metadata; a 500-char or 1k-char cap would surface as a frustrating
    truncation right when the user found something worth recording.
  - ON DELETE CASCADE on session_id: if the parent session is deleted
    (test data cleanup, retention policy), orphan notes serve no
    purpose. The notes are intrinsically about that session's video.
  - No `updated_at` — MVP is read-only after save (decided 2026-05-29
    with the user). When v1.5 adds edit, we'll add updated_at then.
  - No `anchor_id` denormalization — current product is single-author,
    notes belong to whoever's running the dev box. Multi-user comes
    with auth, not before.

Composite index on (session_id, timestamp_sec):
  Workspace will issue `SELECT * FROM session_notes WHERE session_id=$1
  ORDER BY timestamp_sec` on every Workspace page load. The index
  covers both the filter and the sort.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.mixins import OwnedMixin


class SessionNote(Base, OwnedMixin):
    __tablename__ = "session_notes"
    __table_args__ = (
        Index("ix_session_notes_session_time", "session_id", "timestamp_sec"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4()),
    )
    session_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("live_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Where on the timeline the note is anchored — seconds from broadcast
    # start. Matches the anchor of script_segments / viewer_snapshots so
    # the Workspace can place a ★ marker on the same axis everything else
    # already lives on.
    timestamp_sec: Mapped[int] = mapped_column(Integer, nullable=False)
    note_text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    session = relationship("LiveSession", back_populates="notes")
