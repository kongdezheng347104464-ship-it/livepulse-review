"""B4 fix 2026-06-03: viewer_snapshots add like_count for cumulative likes.

Why a new column on viewer_snapshots vs new table:
  Like count is OCR'd from the same top-left region as viewer count and
  arrives in the same frame's OCR output — colocating means one extra
  column instead of one extra table + one extra OCR call.

Type rationale:
  BIGINT chosen over INT because top-tier 抖音 broadcasters routinely
  hit 千万 / 亿-level cumulative likes per session (e.g. 12,300,000),
  which is within INT range (2.1e9) but leaves no safety margin for
  multi-hour streaming events.

Default NULL: legacy sessions analyzed before this column existed have
no OCR pass to fill it. Frontend renders NULL as a clean "—" break in
the trend chart rather than falling back to the old (wrong)
like_event_count proxy. See docs/adr decision in PR.

Tech debt — soft rename intent:
  This table is now `viewer_snapshots` but semantically it's becoming a
  general `live_stats_snapshots` (viewer count + like count + future
  fan-club rank etc.). We don't rename now because frontend reads via
  `viewer_count` field paths, but model docstring documents the future
  rename trigger (= 3rd metric joins → rename + ORM alias). Same
  pattern as `danmakus` → `LiveEvent` soft rename (see model docstring).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0009_viewer_snapshot_like_count"
down_revision: Union[str, Sequence[str], None] = "0008_data_isolation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "viewer_snapshots",
        sa.Column("like_count", sa.BigInteger(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("viewer_snapshots", "like_count")
