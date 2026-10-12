"""2026-06-03: split video asset into master (analysis) + playback (delivery).

Root cause this fixes: playback_encode used to re-encode the source to 720p
H.264 and upload it BACK to the same MinIO key, DESTROYING the original.
Any re-analysis (rerun OCR, scoring v5→v6 re-run) then operated on the
degraded 720p where small UI text (在线观众 / 本场点赞) is unreadable. One
object was carrying two incompatible lifecycles.

New model — two explicit columns, never overwrite the master:
  * master_video_path   — the uploaded original. Analysis ALWAYS reads this.
                          (renamed from video_path; the rename removes the
                          ambiguity of "is video_path original or playback?")
  * playback_video_path — browser-friendly delivery rendition. Frontend
                          plays this (falls back to master if NULL). May
                          EQUAL master_video_path when the source is already
                          browser-friendly (no transcode needed).
  * was_transcoded      — ops/debug flag: true = a separate playback file
                          was produced; false = master served directly.

Backfill: existing video_path data becomes master_video_path (the column
rename preserves it). playback_video_path stays NULL → frontend falls back
to master, so existing sessions keep playing. was_transcoded defaults false.

Caveat (irreversible, pre-migration): sessions analyzed before this change
already had their original overwritten by 720p in MinIO. The rename can't
recover the lost fidelity — master_video_path now points at the 720p for
those rows. Only new uploads get a true preserved master.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011_master_playback_split"
down_revision: Union[str, Sequence[str], None] = "0010_viewer_count_nullable"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Rename original column → master (preserves all existing data).
    op.alter_column("live_sessions", "video_path", new_column_name="master_video_path")
    # Add the playback rendition pointer (NULL = serve master directly).
    op.add_column(
        "live_sessions",
        sa.Column("playback_video_path", sa.String(500), nullable=True),
    )
    # Ops flag — did we produce a separate transcoded playback file?
    op.add_column(
        "live_sessions",
        sa.Column(
            "was_transcoded", sa.Boolean(), nullable=False, server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("live_sessions", "was_transcoded")
    op.drop_column("live_sessions", "playback_video_path")
    op.alter_column("live_sessions", "master_video_path", new_column_name="video_path")
