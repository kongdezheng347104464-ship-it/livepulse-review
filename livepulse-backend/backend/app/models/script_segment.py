import uuid
from datetime import datetime
from decimal import Decimal
from sqlalchemy import String, Integer, Float, Numeric, DateTime, ForeignKey, Index, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base


class ScriptSegment(Base):
    __tablename__ = "script_segments"
    __table_args__ = (
        # Cursor pagination by start_sec, and score-desc strategy queries
        Index("ix_script_segments_session_start", "session_id", "start_sec"),
        Index("ix_script_segments_session_score", "session_id", "score"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4()),
    )
    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("live_sessions.id", ondelete="CASCADE"), nullable=False,
    )
    start_sec: Mapped[int] = mapped_column(Integer, nullable=False)
    end_sec: Mapped[int] = mapped_column(Integer, nullable=False)
    # Text (unbounded) — VAD-merged long Chinese sentences can exceed 5000 chars
    text: Mapped[str] = mapped_column(Text, nullable=False)
    script_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    speech_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    keywords: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    product_mentions: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    analysis_tags: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
    )

    session = relationship("LiveSession", back_populates="script_segments")
