import uuid
from datetime import datetime
from sqlalchemy import String, Integer, DateTime, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base
from app.models.mixins import OwnedMixin


class Anchor(Base, OwnedMixin):
    __tablename__ = "anchors"
    __table_args__ = (
        # A2 (2026-05-31): anchor is now per-user. Same display name across
        # different owners → independent rows. Migration 0008 dropped the
        # old (canonical_name, platform) constraint and added this.
        # Per-platform because same name on different platforms = different
        # broadcaster identity even within one owner's space.
        UniqueConstraint(
            "owner_user_id", "canonical_name", "platform",
            name="uq_anchor_owner_canonical_platform",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    # Canonical form of `name` per app.services.anchor.canonicalize_anchor_name.
    # Always populated on write; the unique constraint guarantees no two
    # rows share the same (canonical_name, platform).
    canonical_name: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    platform: Mapped[str] = mapped_column(String(20), nullable=False, default="douyin")
    avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    followers: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
