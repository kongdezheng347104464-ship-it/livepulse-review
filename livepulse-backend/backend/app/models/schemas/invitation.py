"""A4 admin invitation schemas (2026-06-01)."""
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


class CreateInvitationRequest(BaseModel):
    """POST /admin/invitations payload. Optional `expires_in_days` lets
    admin time-bound a code so a leaked / forgotten code becomes invalid
    after a window. Default = no expiry (M1: trust admin not to leak)."""
    expires_in_days: Optional[int] = Field(default=None, ge=1, le=365)


class InvitationItem(BaseModel):
    """One row in the admin invitation list. `used_by_email` is joined
    from users (NOT raw user_id, which is meaningless to a human admin).
    `status` is computed server-side so list filtering is consistent."""
    code: str
    created_at: datetime
    used_at: Optional[datetime]
    used_by_email: Optional[str]
    expires_at: Optional[datetime]
    status: Literal["unused", "used", "expired"]


class InvitationListResponse(BaseModel):
    items: list[InvitationItem]
    total: int
