"""ViewerSnapshot — per-frame live stats time series.

TECH_DEBT — soft rename pending (2026-06-03):
  Despite the name, this table now stores BOTH viewer_count and like_count
  (and may add fan-club rank / heat etc. in future). The semantically
  correct name is `live_stats_snapshots`. We don't rename yet because:
    1. Frontend already reads `viewer_count` via established field paths
    2. ORM relationship name `viewer_snapshots` is referenced in
       LiveSession.viewer_snapshots backref and 6+ query callsites
    3. M1 doesn't have spare cycles for a touchscape rename

  Rename trigger: a 3rd metric joins (e.g. fan_club_rank) — at that point
  the cost of NOT renaming exceeds the cost of doing it. Same pattern as
  `danmakus` → `LiveEvent` soft rename (see danmaku.py model docstring).

Why a separate table (not a JSON column on live_sessions):
  Audience analytics need rolling averages, spike detection, growth
  rate, cross-session baselines, time-window aggregation, SQL filters.
  All of those are painful (or impossible) on a JSON column. A row-based
  table with a (session_id, timestamp_sec) index gives clean SQL access
  for every analytical need.

Granularity:
  One row per OCR frame that successfully extracted a viewer count from
  the top-right "在线观众·N" region. Currently 30-second cadence (matching
  the danmaku OCR frame interval). The natural rate of viewer-count
  change is minutes, not seconds, so 30s sampling is more than enough.

Schema-level decisions (documented for future maintainers):
  - viewer_count is INTEGER (PG INT4): even huge livestreams stay under
    2^31. Using BIGINT is over-provisioning.
  - like_count is BIGINT: top-tier creators hit 千万 / 亿-level cumulative
    likes per session, INT (2.1e9 cap) leaves no margin.
  - like_count is NULLABLE: legacy sessions analyzed before the like-OCR
    pass existed have no value; frontend treats NULL as "—" break, NOT
    as 0 (would visually flatline the trend chart).
  - timestamp_sec is INTEGER, not FLOAT — sample times are always
    multiples of the OCR frame interval (integer seconds).
  - No `created_at` separate from `timestamp_sec` — the sample's logical
    time IS its position in the recording; ingestion wall-clock isn't
    analytically useful here.
"""
import uuid

from sqlalchemy import BigInteger, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class ViewerSnapshot(Base):
    __tablename__ = "viewer_snapshots"
    __table_args__ = (
        # Time-window queries within a session: rolling avg, range filter,
        # spike detection. Covers (session_id) prefix queries as well.
        Index("ix_viewer_snapshots_session_time", "session_id", "timestamp_sec"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4()),
    )
    session_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("live_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Seconds from broadcast start. Equals the OCR frame's `idx * interval_sec`.
    timestamp_sec: Mapped[int] = mapped_column(Integer, nullable=False)
    # Raw count extracted from the "在线观众·N" overlay. NULL when this
    # frame's OCR couldn't find that label (e.g. 抖音 web OBS recordings
    # often hide it). NULL viewer_count + non-NULL like_count is a valid
    # row — see migration 0010 for the relaxation rationale.
    # A row with BOTH viewer_count and like_count NULL should NEVER exist
    # (caller skips the frame); enforced at write site, not in schema.
    viewer_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Cumulative "本场点赞" from the top-left avatar region. NULL = OCR
    # didn't read a like number on this frame (either pattern didn't match
    # or the frame predates the like-OCR pass). Stored raw (no monotonic
    # clamp); read-side cummax fixes occasional OCR misreads.
    like_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    session = relationship("LiveSession", back_populates="viewer_snapshots")
