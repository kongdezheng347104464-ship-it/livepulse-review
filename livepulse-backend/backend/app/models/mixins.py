"""Reusable ORM mixins (A2 Day 1, 2026-05-31).

`OwnedMixin` adds the `owner_user_id` column to any model whose rows
belong to exactly one user. Apply by inheriting it ALONGSIDE Base:

    class LiveSession(Base, OwnedMixin):
        __tablename__ = "live_sessions"
        ...

The mixin makes the foreign key + column type / constraint identical
across every owned table, so a future audit (e.g., "does every owned
table have ON DELETE CASCADE?") only has one place to look.

NOT a mixin: ownership scoping logic. That lives in
`app/services/ownership.apply_owner_scope` so endpoints have a single
helper to call, mockable for tests.
"""
from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column


class OwnedMixin:
    """Declares `owner_user_id`. NOT NULL + FK to users(id) + CASCADE."""
    owner_user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
