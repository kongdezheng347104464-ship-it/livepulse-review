"""session_analyses — LLM-produced range analysis over selected ScriptSegments.

Why this table (2026-05-29):
  The Replay Workspace adds an "AI 分析" action: user selects 1-60
  ScriptSegments via Cmd/Shift-click in the transcript panel, clicks
  a floating button, and the system runs Claude on those segments
  (plus ±30s context) to produce structured advice — what to keep,
  what to optimize, what to spend less time on.

  Notes (session_notes) and Analyses (this table) are kept apart on
  purpose: notes are user-authored prose, analyses are machine-
  authored structured output. Mixing them muddles "list my notes"
  semantics and complicates future operations like "rerun all
  analyses against the v2 prompt" or "show me Claude's keep
  recommendations across the last 10 sessions".

Schema:
  id              UUID (str36 — matches the project's pending UUID
                  migration which hasn't happened yet)
  session_id      FK live_sessions.id, CASCADE
  segment_ids     JSONB array of script_segment.id strings, denormalised
                  (no junction table — analyses are immutable, no JOIN
                  use case)
  prompt_version  str(20) — bumped in code when prompt template changes
                  so future "stale analysis" detection is possible
  result          JSONB — { summary, keep[], remove[], optimize[] };
                  shape may evolve, JSONB tolerates without migration
  token_usage     int — input + output combined; for cost monitoring
  created_at      TIMESTAMPTZ default now()

Composite index (session_id, created_at):
  Workspace's primary query is "newest analyses first for this session"
  to show the latest result at top + history below. Covers filter + sort.

Revision ID: 0005_session_analyses
Revises: 0004_session_notes
Create Date: 2026-05-29
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0005_session_analyses"
down_revision: Union[str, Sequence[str], None] = "0004_session_notes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "session_analyses",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(length=36),
            sa.ForeignKey("live_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("segment_ids", postgresql.JSONB(), nullable=False),
        sa.Column("prompt_version", sa.String(length=20), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=False),
        sa.Column("token_usage", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_session_analyses_session_time",
        "session_analyses",
        ["session_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_session_analyses_session_time", table_name="session_analyses")
    op.drop_table("session_analyses")
