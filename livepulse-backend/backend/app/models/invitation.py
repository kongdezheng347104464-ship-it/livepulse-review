"""Invitation ORM model (A1 2026-05-31).

The code is the primary key — invitation codes are naturally unique
random strings (16 chars), and no other tables FK against this row,
so a separate UUID id would add storage + index cost for no lookup
benefit. See alembic/versions/0007_users_invitations.py for the
schema design discussion.

Usage flow:
  * admin generates → row inserted with used_by_user_id=NULL
  * user redeems at register → row updated with used_by_user_id +
    used_at; new user.id is the value of used_by_user_id
  * single-use: register endpoint must check `used_by_user_id IS NULL`
    before accepting; this is a service-layer concern, not a DB
    constraint, because we want to be able to inspect "who used what"
    for support / fraud cases after the fact.
"""
from datetime import datetime
from sqlalchemy import String, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class Invitation(Base):
    __tablename__ = "invitations"

    code: Mapped[str] = mapped_column(String(16), primary_key=True)
    created_by: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    used_by_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
