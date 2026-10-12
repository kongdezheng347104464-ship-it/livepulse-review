import uuid
from datetime import datetime
from decimal import Decimal
from sqlalchemy import String, Integer, DateTime, Numeric, ForeignKey, Index, Boolean, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import expression
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base
from app.models.mixins import OwnedMixin


class LiveSession(Base, OwnedMixin):
    __tablename__ = "live_sessions"
    __table_args__ = (
        # List view sorts by created_at desc and frequently filters by status / anchor
        Index("ix_live_sessions_status_created", "status", "created_at"),
        Index("ix_live_sessions_anchor_created", "anchor_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4()),
    )
    anchor_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("anchors.id", ondelete="CASCADE"), nullable=False,
    )
    platform: Mapped[str] = mapped_column(String(20), nullable=False, default="douyin")
    platform_room_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    cover_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Asset split (2026-06-03, migration 0011):
    #   master_video_path   — uploaded original. Analysis ALWAYS reads this.
    #   playback_video_path — browser-friendly rendition. Frontend plays this
    #                         (falls back to master when NULL). May equal
    #                         master when source is already H.264/faststart.
    #   was_transcoded      — true = a separate playback file was produced.
    # Never overwrite master — that's the whole point of the split.
    master_video_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    playback_video_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    was_transcoded: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=expression.false(),
    )
    start_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    end_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    duration_sec: Mapped[int | None] = mapped_column(Integer, nullable=True)
    viewer_peak: Mapped[int | None] = mapped_column(Integer, nullable=True)
    viewer_avg: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_danmaku: Mapped[int] = mapped_column(Integer, default=0)
    total_gmv: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True, default=None,
    )
    total_orders: Mapped[int | None] = mapped_column(Integer, nullable=True)
    new_followers: Mapped[int | None] = mapped_column(Integer, nullable=True)
    composite_score: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2), nullable=True, default=None,
    )
    analysis_summary: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="uploaded")
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    analysis_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    analysis_finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Set once when the Feishu webhook fires successfully — guards against
    # duplicate notifications when a session is re-analyzed (e.g. /rerun).
    feishu_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Recorded canvas resolution as "WIDTHxHEIGHT" (e.g. "1920x1080"). Used
    # by frame_cropper to convert relative ROI coordinates (0..1) into
    # absolute pixels, and to debug "the OCR worked before but stopped
    # working" failures — usually a resolution / aspect-ratio change.
    # NULL on legacy sessions analyzed before 2026-05-28; they default to
    # 1920x1080 at crop time per the recording convention in use.
    source_resolution: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
    )

    anchor = relationship("Anchor", lazy="selectin")
    danmakus = relationship("Danmaku", back_populates="session", lazy="selectin")
    # Viewer count time series — populated by the new _step_viewer_count
    # pipeline stage. Empty for legacy sessions; backfill plan tracked in
    # P0-Evt-6 (see project-next-steps memory).
    viewer_snapshots = relationship(
        "ViewerSnapshot", back_populates="session", lazy="selectin",
    )
    script_segments = relationship(
        "ScriptSegment", back_populates="session", lazy="selectin",
    )
    pipeline_steps = relationship(
        "PipelineStep", back_populates="session", lazy="selectin",
    )
    # Review notes pinned at timeline positions (Replay Workspace).
    # lazy="select" not "selectin": list endpoint and detail endpoint
    # don't render notes; only the Workspace GET /timeline does, where
    # we'd rather an explicit one-shot query than auto-loading on every
    # session fetch.
    notes = relationship(
        "SessionNote", back_populates="session", lazy="select",
        order_by="SessionNote.timestamp_sec",
    )
    # LLM-produced range analyses, ordered newest-first to match the UI's
    # "latest analysis at top" convention.
    analyses = relationship(
        "SessionAnalysis", back_populates="session", lazy="select",
        order_by="SessionAnalysis.created_at.desc()",
    )
