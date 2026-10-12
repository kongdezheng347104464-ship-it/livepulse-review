"""session_notes — anchor's review notes pinned at timeline positions.

Why this table exists (2026-05-29):
  The Replay Timeline Workspace lets anchors scrub through their
  OBS recording, pause at any moment, and pin a written observation
  to that timestamp. The pinned moment becomes a navigable bookmark
  on the timeline (the ★ marker) and the note is shown alongside
  the video when the anchor returns to it.

  Notes are the workspace's primary user-asset. Without them, the
  Workspace is just a video player. With them, it becomes a structured
  review document the anchor accumulates across sessions.

Schema decisions (see app/models/session_note.py for the long rationale):
  - PK: UUID stored as String(36) — matches the rest of the codebase
    (the postgresql.UUID migration is deliberately deferred).
  - session_id: FK to live_sessions.id with ON DELETE CASCADE — if the
    parent session disappears, the notes lose their anchoring video
    and have no standalone meaning.
  - timestamp_sec: INT4 — one-second resolution is enough for review
    (the user thinks "at 15:32", not "at 15:32.471").
  - note_text: TEXT, no length cap — anchors write thoughts, not metadata.
  - created_at: TIMESTAMPTZ default now() — UTC server time.
  - No `updated_at`: MVP is read-only after save. v1.5 edit adds it.

Composite index (session_id, timestamp_sec):
  Workspace's primary query is "all notes for this session, ordered
  by timestamp". This index covers both the filter and the sort in
  one B-tree.

Revision ID: 0004_session_notes
Revises: 0003_normalized_username
Create Date: 2026-05-29
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0004_session_notes"
down_revision: Union[str, Sequence[str], None] = "0003_normalized_username"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "session_notes",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(length=36),
            sa.ForeignKey("live_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("timestamp_sec", sa.Integer(), nullable=False),
        sa.Column("note_text", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_session_notes_session_time",
        "session_notes",
        ["session_id", "timestamp_sec"],
    )


def downgrade() -> None:
    op.drop_index("ix_session_notes_session_time", table_name="session_notes")
    op.drop_table("session_notes")
