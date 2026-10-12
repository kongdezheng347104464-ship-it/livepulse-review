"""A1 Day 1 (2026-05-31): users + invitations tables.

Schema rationale (see docs/auth-design.md):
  * users.email: CITEXT would be cleaner but adds extension dep; we
    instead enforce lowercase normalization at the service layer + a
    functional UNIQUE index on lower(email). Existing dev data has none.
  * users.password_changed_at: M1 doesn't expose password change yet,
    but the column lets M2 add "all existing tokens invalid" guard
    (compare jwt.iat < password_changed_at) without a migration.
  * users.role: server-only enum, register endpoint hardcodes 'user'.
    Only admin user is seeded by environment vars (never via API).
  * invitations.code as PRIMARY KEY: codes are naturally unique random
    strings; saves a separate UUID id + index. No FK references exist.

NOT in this migration (per scope):
  * owner_user_id columns on live_sessions / anchors / etc. — that's A2.
  * SMS / OAuth identity columns — M2.
  * credits_balance — M2 payment.

Revision ID: 0007_users_invitations
Revises: 0006_session_analysis_range
Create Date: 2026-05-31
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0007_users_invitations"
down_revision: Union[str, Sequence[str], None] = "0006_session_analysis_range"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- users ---
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), primary_key=True),  # UUID stored as string
        # Email stored as user typed it; uniqueness enforced via LOWER(email)
        # index below. Application layer normalizes to lower() for queries.
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        # role is server-only: 'user' for register, 'admin' only via seed.
        sa.Column("role", sa.String(20), nullable=False, server_default="user"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        # M2 token-revocation seed (see auth-design doc)
        sa.Column("password_changed_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("role IN ('user', 'admin')", name="ck_users_role_enum"),
    )
    # Case-insensitive uniqueness on email.
    op.execute("CREATE UNIQUE INDEX uq_users_email_lower ON users (LOWER(email))")

    # --- invitations ---
    op.create_table(
        "invitations",
        sa.Column("code", sa.String(16), primary_key=True),
        sa.Column("created_by", sa.String(36),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("used_by_user_id", sa.String(36),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Help future admin UI list "未使用 / 已使用" without full scan.
    op.create_index("ix_invitations_used_by", "invitations", ["used_by_user_id"])


def downgrade() -> None:
    op.drop_index("ix_invitations_used_by", table_name="invitations")
    op.drop_table("invitations")
    op.execute("DROP INDEX IF EXISTS uq_users_email_lower")
    op.drop_table("users")
