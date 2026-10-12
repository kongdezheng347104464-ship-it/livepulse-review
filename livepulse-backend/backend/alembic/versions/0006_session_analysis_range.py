"""session_analyses: add range_start_sec / range_end_sec.

Why (2026-05-29 v1.5):
  The Replay Workspace's AnalysisPanel surfaces a "跳到选段开头" affordance
  on each analysis card so a reviewer can re-watch the moment Claude
  was talking about. Computing the range bounds from `segment_ids` at
  read time would mean a per-card JOIN against script_segments on
  every page load. Denormalising the bounds at create time keeps the
  read path a single SELECT.

  Both columns are nullable. There are no production analyses at this
  migration's land time (feature was ungated for less than a day in
  internal testing only). We intentionally don't backfill — the
  workspace UI tolerates None by hiding the jump button.

Revision ID: 0006_session_analysis_range
Revises: 0005_session_analyses
Create Date: 2026-05-29
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0006_session_analysis_range"
down_revision: Union[str, Sequence[str], None] = "0005_session_analyses"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "session_analyses",
        sa.Column("range_start_sec", sa.Integer(), nullable=True),
    )
    op.add_column(
        "session_analyses",
        sa.Column("range_end_sec", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("session_analyses", "range_end_sec")
    op.drop_column("session_analyses", "range_start_sec")
