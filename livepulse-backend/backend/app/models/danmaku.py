"""Danmaku model — DEPRECATED NAME, see below.

================================================================
TECH_DEBT (2026-05-28): SOFT RENAME — `Danmaku` semantically is now
`LiveEvent`. The class/table/file kept the original name to avoid
churning the API contract surface (~79 frontend references across
22 files) at a moment when functional debt (gift-pollution of
conversion insights) was the actual blocker.

What changed conceptually:
  Before: `Danmaku` rows held only `speech`-class barrage text
          plus whatever noise the video-region OCR happened to
          catch (gifts, system messages, badges).
  Now:    `Danmaku` rows hold the full live-event stream —
          speech / gift / system / unknown — classified upstream
          by `source_type`. Same table, far broader semantic
          surface. Read as `LiveEvent` in your head.

When to do the actual rename:
  Any of these triggers should justify lifting the soft rename:
    • A major API version bump is happening anyway.
    • GraphQL / typed-SDK / OpenAPI codegen is being added
      (then frontend types get regenerated for free).
    • The event vocabulary expands beyond 4 source_types,
      making the misnomer actively misleading in code review.
  Until one of those, keep the soft rename — the cost-benefit
  doesn't justify a 22-file frontend migration just for naming.

Identity vs display: `user_id` and `username` are deliberately
separate. `user_id` is the (currently unused) slot for a stable
platform identifier; `username` is the OCR-captured display
name, which is unstable (anonymous "手机用户12345" patterns,
nickname changes, OCR quirks). Mixing them now would create a
6-month-out identity layer mess we can't undo.
================================================================
"""
import uuid
from datetime import datetime

from sqlalchemy import String, Integer, DateTime, ForeignKey, Index, Enum as SAEnum, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.source_type import SourceType


class Danmaku(Base):  # noqa: D101 — see module docstring (this is semantically LiveEvent)
    __tablename__ = "danmakus"
    __table_args__ = (
        # Session-scoped time-window queries (sessions.py danmaku-window,
        # detail cursor pagination) and intent drill-down.
        Index("ix_danmakus_session_time", "session_id", "timestamp_sec"),
        Index("ix_danmakus_session_intent", "session_id", "intent"),
        # NOTE: deliberately NOT adding (session_id, source_type) index yet.
        # At current data volumes (~10 sessions, few thousand rows total),
        # PostgreSQL seq-scans the whole table in <10ms; the write-amplification
        # cost of another index isn't worth it. Add when we hit ~100 sessions
        # OR start seeing slow Q1 filter queries in pipeline.log.
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4()),
    )
    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("live_sessions.id", ondelete="CASCADE"), nullable=False,
    )

    # --- IDENTITY layer (reserved) ---
    # Stable platform-issued user identifier. Currently always NULL because
    # we have no way to obtain it from screen-recording OCR. Reserved for
    # the future when we integrate the Douyin SDK directly. DO NOT repurpose
    # for OCR-display names — keeping identity and display separate now
    # avoids a 6-months-out unwinding job.
    user_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # --- DISPLAY layer ---
    # OCR-captured display name from the right-side interaction list
    # (e.g. "甜刘刘", "W-nov7-sco❤️", or anonymous "手机用户92810699155").
    # NULL is acceptable: floating-danmaku-region OCR can't capture
    # username, and some system events have no associated user.
    #
    # IMPORTANT: this is the RAW field. It may contain OCR-induced UI
    # pollution like "◆10 唐***" or "讲解中产品介绍 ◆5 小明" because
    # the engine sometimes clusters multiple visual regions into one
    # row. Aggregation queries that want stable cardinality (e.g.
    # unique_speakers for Engaged Audience Rate) MUST use
    # `normalized_username`, not this field. See username_normalizer
    # module docstring for the design rationale.
    username: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Normalized display name — output of rule-based OCR cleanup.
    # NULL means: this row's raw username failed validation (too short,
    # pure digits, anonymous "手机用户\d+" pattern, or fully stripped
    # to empty by UI-chrome prefix rules).
    #
    # AGGREGATION CONTRACT:
    #   SELECT COUNT(DISTINCT normalized_username)
    #   FROM danmakus
    #   WHERE source_type = 'speech'
    #     AND normalized_username IS NOT NULL
    # is THE canonical way to compute unique_speakers. The
    # NOT NULL check is the single rule that excludes anonymous +
    # invalid + OCR-noise rows from cardinality.
    #
    # Future work: if we ever need "anonymous fraction" or similar
    # diagnostics, add an `is_anonymous` boolean — don't repurpose
    # this column's NULL semantics.
    normalized_username: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Original cleaned text content of the event. For chat events this is
    # the message body; for gift events the post-prefix tail ("送出了 X ×1");
    # for system events the post-prefix tail ("为主播点赞了"). Kept as-is
    # from the OCR pipeline; classification metadata lives in `source_type`.
    content: Mapped[str] = mapped_column(String(500), nullable=False)

    # Frame timestamp (seconds from broadcast start). Same granularity as
    # the OCR frame interval — DO NOT confuse with sub-second event time.
    timestamp_sec: Mapped[int] = mapped_column(Integer, nullable=False)

    # --- CLASSIFICATION layer ---
    # Provenance tag, used by the Q1 conversion invariant and downstream
    # metric layers. ENUM at the DB level prevents string drift. See
    # source_type.py for the value catalog and rationale.
    source_type: Mapped[SourceType] = mapped_column(
        SAEnum(SourceType, name="source_type_enum", create_type=False,
               values_callable=lambda enum_cls: [m.value for m in enum_cls]),
        nullable=False,
        server_default=SourceType.SPEECH.value,
    )

    # Sentiment / intent labels — produced by downstream embedding analyzers.
    # Both nullable because they're computed late in the pipeline and may
    # be skipped for non-speech source_types in the future.
    sentiment: Mapped[str | None] = mapped_column(String(10), nullable=True)
    intent: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # --- DEBUG layer ---
    # Raw OCR line BEFORE source_classifier parsing. Lets us audit any
    # classification (not just unknowns) and reproduce edge cases. For a
    # row classified as gift with content="送出了 🎁 ×1", raw_ocr_text
    # might be "金闪闪：送出了 🎁 ×1" — i.e. includes the username prefix.
    # Storage cost ~100KB per 10k rows, negligible relative to debug value.
    raw_ocr_text: Mapped[str | None] = mapped_column(String(500), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
    )

    session = relationship("LiveSession", back_populates="danmakus")


# Alias for downstream code that wants to read this as the event model
# without the legacy name. NOTE: this is the SAME class — assignments
# to one are visible via the other. Useful for type hints in new code:
#   def process(event: LiveEvent) -> None: ...
LiveEvent = Danmaku
