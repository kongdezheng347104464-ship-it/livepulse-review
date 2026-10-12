"""A1 auth request/response schemas (2026-05-31)."""
from pydantic import BaseModel, EmailStr, Field
from typing import Optional


# ---- Requests ----

class RegisterRequest(BaseModel):
    """POST /auth/register payload.

    Note: any `role` field in the request body is IGNORED — register
    endpoint hardcodes role='user'. Admin is created only via env-driven
    seed. This is intentionally not modeled as a field here so attackers
    can't even try to include it (though server-side defense is the real
    barrier — see test_register_cannot_create_admin invariant).
    """
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    invitation_code: str = Field(min_length=8, max_length=32)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


# ---- Responses ----

class TokenResponse(BaseModel):
    """JWT response shared by register + login.

    `token_type: bearer` is the standard label clients use to build the
    Authorization header (`Authorization: Bearer <access_token>`).
    `expires_in` is in seconds — Pydantic int, not delta — to keep the
    contract universally consumable by web/mobile clients.
    """
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds until exp


class UserMeResponse(BaseModel):
    """GET /auth/me payload — curated 4 fields, no created_at/updated_at.

    Per A1 design: only fields the UI actually needs (navbar avatar /
    admin badge / workspace owner). Adding more here is a one-way door —
    once a client depends on a field we can't drop it.
    """
    id: str
    email: str
    role: str
    is_active: bool
