"""live event telemetry — source_type ENUM, username, raw_ocr_text,
viewer_snapshots table, sessions.source_resolution

Why this migration exists (2026-05-28):
  The OCR pipeline used to crop the floating-danmaku region of the video
  frame, which mixed gift icons / system messages / badges into the same
  "danmaku" rows as actual speech. This poisoned the Q1 conversion
  insight: ~67 "purchase intent" entries on session b6dcb1c6 were
  actually gift names / "为主播点赞了" / leaderboard noise.

  The fix is to crop the right-hand interaction list region instead,
  which is a half-structured event protocol (username + 「：」 + content,
  with reliable prefixes "送出了 / 进入直播间 / 为主播点赞了"). That lets
  us tag every event with source_type at ingestion time and let the Q1
  invariant reject anything that isn't speech.

  Same migration also lays the foundation for viewer_count normalization:
  the top-right "在线观众·N" overlay is OCR-extractable too, giving us a
  reliable denominator for engagement_rate / conversion_rate going forward.

Soft-rename context:
  The danmakus table semantically becomes a `live_events` stream.
  The physical table / class / API field names are KEPT as `danmakus`
  to avoid a 22-file frontend contract migration that has no functional
  benefit. See backend/app/models/danmaku.py module docstring for the
  triggers that would justify lifting the soft rename.

Revision ID: 0002_live_event_telemetry
Revises: 0001_baseline
Create Date: 2026-05-28
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0002_live_event_telemetry"
down_revision: Union[str, Sequence[str], None] = "0001_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Keep the value list in lockstep with app.models.source_type.SourceType.
# Order matters for PG ENUM ordering; we put 'speech' first because it's
# both the most frequent value and the only one that passes the Q1 invariant.
SOURCE_TYPE_VALUES = ("speech", "gift", "system", "unknown")
SOURCE_TYPE_NAME = "source_type_enum"


def upgrade() -> None:
    # ---- 1. PG ENUM type for source_type ----
    # Create the type FIRST so columns can reference it. Using a real ENUM
    # (rather than a CHECK constraint or a free string) means typos become
    # write-time errors at the DB level, not silent classification drift.
    source_type_enum = sa.Enum(*SOURCE_TYPE_VALUES, name=SOURCE_TYPE_NAME)
    source_type_enum.create(op.get_bind(), checkfirst=True)

    # ---- 2. danmakus table: add new columns ----
    # source_type defaults to 'speech' so the existing 9 historical sessions
    # remain semantically intact under the legacy filter ("treat everything
    # as speech"). They will be re-classified during the P0-Evt-6 backfill
    # rerun against the new interaction-region OCR. server_default keeps
    # legacy code paths working even if they don't set the column.
    op.add_column(
        "danmakus",
        sa.Column(
            "source_type",
            source_type_enum,
            nullable=False,
            server_default="speech",
        ),
    )
    # Display name extracted by the new interaction-region OCR. NULL for
    # legacy rows (they came from the floating-danmaku region which has no
    # username info) and for system events that have no associated user.
    op.add_column(
        "danmakus",
        sa.Column("username", sa.String(length=100), nullable=True),
    )
    # Raw OCR line BEFORE source_classifier normalization. Captured so we
    # can audit any classification decision without re-running OCR; storage
    # cost is trivial relative to debug value.
    op.add_column(
        "danmakus",
        sa.Column("raw_ocr_text", sa.String(length=500), nullable=True),
    )

    # Deliberately NOT creating a (session_id, source_type) composite index
    # yet — at <10 sessions and ~10k total rows, PostgreSQL seq-scans this
    # in <10ms. Adding the index has real write cost (every insert/update
    # touches it) and trivial read benefit at this scale. Re-evaluate at
    # ~100 sessions or when pipeline.log shows slow Q1 filters.

    # ---- 3. viewer_snapshots: new table ----
    # Row-based, NOT JSON on live_sessions. SQL aggregation (rolling avg,
    # spike detection, growth rate, cross-session JOIN) all require this
    # to be a proper table — storing the time series in a JSONB column
    # would make analytical queries painful or impossible.
    op.create_table(
        "viewer_snapshots",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(length=36),
            sa.ForeignKey("live_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Frame timestamp in seconds from broadcast start; matches the OCR
        # frame cadence (currently 30s). NOT a wall-clock datetime — the
        # logical time is the recording position.
        sa.Column("timestamp_sec", sa.Integer(), nullable=False),
        # Extracted viewer count. NOT NULL: if OCR failed on a frame we
        # don't insert a row, making gaps explicit rather than encoding
        # them as 0 (which would corrupt averages).
        sa.Column("viewer_count", sa.Integer(), nullable=False),
    )
    op.create_index(
        "ix_viewer_snapshots_session_time",
        "viewer_snapshots",
        ["session_id", "timestamp_sec"],
    )

    # ---- 4. live_sessions: add source_resolution ----
    # Captured at upload time from ffprobe so frame_cropper can convert
    # relative ROI coords (e.g. {0.82, 0.10, 0.97, 0.17}) into absolute
    # pixels. Legacy sessions have NULL and default to 1920x1080 at the
    # crop site (matching the existing recording convention).
    op.add_column(
        "live_sessions",
        sa.Column("source_resolution", sa.String(length=20), nullable=True),
    )


def downgrade() -> None:
    # Reverse order: drop columns/tables first, then the ENUM type.
    # If you only want to rollback, run `alembic downgrade -1`.

    op.drop_column("live_sessions", "source_resolution")

    op.drop_index("ix_viewer_snapshots_session_time", table_name="viewer_snapshots")
    op.drop_table("viewer_snapshots")

    op.drop_column("danmakus", "raw_ocr_text")
    op.drop_column("danmakus", "username")
    op.drop_column("danmakus", "source_type")

    # DROP TYPE must run AFTER columns using it are dropped, otherwise
    # PG refuses with "cannot drop type because other objects depend on it".
    source_type_enum = sa.Enum(*SOURCE_TYPE_VALUES, name=SOURCE_TYPE_NAME)
    source_type_enum.drop(op.get_bind(), checkfirst=True)
