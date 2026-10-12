"""danmakus add normalized_username — rule-based OCR cleanup output.

Why this column exists (2026-05-28):
  After the P0-Evt full-frame OCR landed, source_classifier extracts
  username:content pairs but the OCR engine sometimes clusters
  multiple visual regions into one "line", prepending UI chrome to
  the actual username:

      raw "username" column might hold:
        "◆10 唐***"
        "讲解中产品介绍 ◆5 小明"
        "全部商品 X*****"
        "￥69.9 抢 ◆1 c*****"

  Aggregating COUNT(DISTINCT username) under these conditions inflates
  unique_speakers by 5-10x — every chat from "唐***" splits across
  many ghost identities. Engaged Audience Rate (= unique_speakers /
  viewer_count, the headline analytics metric for this product)
  becomes meaningless.

  This column stores the output of a deterministic rule-based
  normalizer (see app/services/live_event/username_normalizer.py).
  Aggregation queries are expected to use `normalized_username`,
  not `username`, going forward.

NULL semantics:
  normalized_username = NULL means the row's raw username failed
  validation:
    - too short (< 2 chars after stripping UI prefixes)
    - pure digit string
    - anonymous platform-issued pattern "^手机用户\d+$"
    - fully stripped to empty by UI-chrome rules

  All four cases should be EXCLUDED from unique_speakers cardinality.
  The single rule `WHERE normalized_username IS NOT NULL` covers all
  four — DO NOT add per-case logic at query sites.

Backfill strategy (2026-05-28 decision):
  Existing rows have normalized_username = NULL by default. The
  pipeline starts populating it for newly-ingested events as soon
  as this migration applies. The user decided NOT to retroactively
  populate the 9 historical sessions — those sessions came from
  the pre-2026-05-28 OCR pipeline which never captured username,
  so there's nothing to normalize. Cross-session metric continuity
  will resume as fresh sessions are uploaded.

Revision ID: 0003_normalized_username
Revises: 0002_live_event_telemetry
Create Date: 2026-05-28
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0003_normalized_username"
down_revision: Union[str, Sequence[str], None] = "0002_live_event_telemetry"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Same length as `username` for symmetry; the normalizer never
    # produces output longer than its input (only strips, never adds).
    op.add_column(
        "danmakus",
        sa.Column("normalized_username", sa.String(length=100), nullable=True),
    )

    # Deliberately NOT adding an index on this column yet. At current
    # data volumes (<100k rows total), COUNT(DISTINCT) and IS NOT NULL
    # filters are seq-scan friendly. Add when we observe slow queries
    # in pipeline.log on real workloads. The whole point of P1
    # normalization is to make unique_speakers cardinality cheap and
    # stable — premature indexing is a different concern.


def downgrade() -> None:
    op.drop_column("danmakus", "normalized_username")
