"""Pipeline step execution record.

Tracks each step in the analysis pipeline for observability and debugging.
"""
import uuid
from datetime import datetime
from sqlalchemy import String, Integer, DateTime, ForeignKey, Index, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base


class PipelineStep(Base):
    """Tracks one execution of a pipeline step."""

    __tablename__ = "pipeline_steps"
    __table_args__ = (
        Index("ix_pipeline_steps_session_order", "session_id", "step_order"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4()),
    )
    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("live_sessions.id", ondelete="CASCADE"), nullable=False,
    )
    step_name: Mapped[str] = mapped_column(
        String(30), nullable=False,
    )
    step_order: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(
        String(15), default="pending",
    )  # pending | running | done | failed | skipped

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    input_info: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    output_info: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
    )

    session = relationship("LiveSession", back_populates="pipeline_steps")
