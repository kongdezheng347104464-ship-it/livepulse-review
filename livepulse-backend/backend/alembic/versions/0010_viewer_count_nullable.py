"""B4 fix 2026-06-03 follow-up: viewer_snapshots.viewer_count → NULLABLE.

Why this column needs to be nullable now:
  The 0009 migration added `like_count NULL` so the same row can carry
  EITHER viewer_count OR like_count OR both. But viewer_count was still
  NOT NULL, which meant frames where the OCR matched ONLY 本场点赞
  (e.g. 抖音 web OBS recordings that don't render "在线观众") were
  dropped entirely — losing the like data we DID capture.

Net effect of dropping NOT NULL:
  * Frame yields viewer + like → row inserted with both     (unchanged)
  * Frame yields viewer only   → row inserted, like_count=NULL (unchanged)
  * Frame yields like   only   → row inserted, viewer_count=NULL (NEW)
  * Frame yields neither       → no row (unchanged — gap = no data)

Existing rows (where viewer_count is always populated) are not affected
by relaxing the constraint.

Downgrade caveat:
  If there are rows with viewer_count IS NULL when downgrade runs, the
  ALTER COLUMN SET NOT NULL would fail. Downgrade therefore first
  deletes those rows. Acceptable because they're a strictly larger set
  than pre-0010 schema could have stored.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0010_viewer_count_nullable"
down_revision: Union[str, Sequence[str], None] = "0009_viewer_snapshot_like_count"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "viewer_snapshots",
        "viewer_count",
        existing_type=sa.Integer(),
        nullable=True,
    )


def downgrade() -> None:
    # Reject rows that the original constraint would have rejected.
    op.execute("DELETE FROM viewer_snapshots WHERE viewer_count IS NULL")
    op.alter_column(
        "viewer_snapshots",
        "viewer_count",
        existing_type=sa.Integer(),
        nullable=False,
    )
