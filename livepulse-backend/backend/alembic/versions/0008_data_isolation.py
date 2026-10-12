"""A2 Day 1 (2026-05-31): data isolation — add owner_user_id.

Schema rationale (see docs/adr/0001-indirect-table-ownership.md for
the full architectural decision record):

  * 4 direct tables (live_sessions / anchors / session_notes /
    session_analyses) get `owner_user_id VARCHAR(36) NOT NULL` with
    FK + ON DELETE CASCADE to users(id).
  * Indirect tables (danmakus / script_segments / viewer_snapshots /
    pipeline_steps) do NOT get owner_user_id. Ownership is verified
    via JOIN to live_sessions. ADR documents when we'd upgrade.
  * Anchor's UNIQUE constraint changes from
    `(canonical_name, platform)` to `(owner_user_id, canonical_name, platform)`.
    Two users with anchors named "小满" on Douyin = two independent rows
    (per A1 decision: anchor is a per-user analysis space, not a global
    identity).

Three-step migration pattern (protects existing 20 sessions / 7 anchors
/ 5 notes / 1 analysis from temporary inaccessibility):
  1. ADD COLUMN owner_user_id NULLABLE (no rows fail)
  2. UPDATE ... SET owner_user_id = (SELECT id FROM users WHERE role='admin')
  3. ALTER COLUMN owner_user_id SET NOT NULL

Pre-flight:
  Migration refuses to proceed if no admin user exists. This protects
  against bricking a fresh DB where admin_seed hasn't run yet.

Revision ID: 0008_data_isolation
Revises: 0007_users_invitations
Create Date: 2026-05-31
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0008_data_isolation"
down_revision: Union[str, Sequence[str], None] = "0007_users_invitations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


OWNED_TABLES = ("live_sessions", "anchors", "session_notes", "session_analyses")


def upgrade() -> None:
    conn = op.get_bind()

    # ---- Pre-flight: refuse if no admin user ----
    admin_row = conn.execute(
        sa.text("SELECT id FROM users WHERE role='admin' LIMIT 1")
    ).fetchone()
    if not admin_row:
        raise RuntimeError(
            "A2 migration aborted: no admin user exists. Set ADMIN_EMAIL + "
            "ADMIN_PASSWORD in .env and restart backend to trigger admin_seed "
            "before running this migration."
        )
    admin_id = admin_row[0]

    # ---- Step 1: ADD COLUMN nullable on all 4 tables ----
    for tbl in OWNED_TABLES:
        op.add_column(
            tbl,
            sa.Column("owner_user_id", sa.String(36), nullable=True),
        )

    # ---- Step 2: backfill all existing rows to admin ----
    for tbl in OWNED_TABLES:
        conn.execute(
            sa.text(f"UPDATE {tbl} SET owner_user_id = :admin_id"),
            {"admin_id": admin_id},
        )

    # ---- Step 3: ALTER NOT NULL + add FK + index ----
    for tbl in OWNED_TABLES:
        op.alter_column(tbl, "owner_user_id", nullable=False)
        op.create_foreign_key(
            f"fk_{tbl}_owner_user_id",
            tbl, "users",
            ["owner_user_id"], ["id"],
            ondelete="CASCADE",
        )
        op.create_index(
            f"ix_{tbl}_owner_user_id",
            tbl,
            ["owner_user_id"],
        )

    # ---- Anchor: replace UNIQUE constraint with composite (owner-scoped) ----
    # Old: UNIQUE(canonical_name, platform) — global identity
    # New: UNIQUE(owner_user_id, canonical_name, platform) — per-user identity
    # Per A1 decision: anchor is a per-user analysis space, not a global
    # broadcaster identity. Two users can each have their own "小满".
    op.drop_constraint("uq_anchor_canonical_platform", "anchors", type_="unique")
    op.create_unique_constraint(
        "uq_anchor_owner_canonical_platform",
        "anchors",
        ["owner_user_id", "canonical_name", "platform"],
    )


def downgrade() -> None:
    # Reverse the schema changes. Note: dropping owner_user_id discards
    # the ownership data — only safe in dev rollback, never in prod.
    op.drop_constraint("uq_anchor_owner_canonical_platform", "anchors", type_="unique")
    op.create_unique_constraint(
        "uq_anchor_canonical_platform",
        "anchors",
        ["canonical_name", "platform"],
    )
    for tbl in reversed(OWNED_TABLES):
        op.drop_index(f"ix_{tbl}_owner_user_id", table_name=tbl)
        op.drop_constraint(f"fk_{tbl}_owner_user_id", tbl, type_="foreignkey")
        op.drop_column(tbl, "owner_user_id")
