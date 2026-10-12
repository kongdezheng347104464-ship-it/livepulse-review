"""User ORM model (A1 2026-05-31).

Schema rationale lives in alembic/versions/0007_users_invitations.py.
Key contract:
  * email: stored as user typed it, but ALWAYS compared via lower() at
    the service layer. The uq_users_email_lower index enforces this at
    DB level.
  * role: server-only enum {'user', 'admin'}. register endpoint must
    hardcode 'user'; admin is created via env-driven seed (see
    app/services/auth/admin_seed.py).
  * password_changed_at: written on register + on future password
    change. M2 token revocation guard: jwt.iat < password_changed_at →
    reject. M1 doesn't use this gate yet but the column is here so
    M2 doesn't need a migration.
"""
import uuid
from datetime import datetime
from sqlalchemy import String, Boolean, DateTime, CheckConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('user', 'admin')", name="ck_users_role_enum"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="user")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        server_default=func.now(), onupdate=func.now(),
    )
    password_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
