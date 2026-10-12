"""Tests for app.services.auth.jwt_service — issue + decode roundtrip."""

import os
import time
from datetime import datetime, timedelta, timezone

import pytest

# Ensure env has a valid JWT secret BEFORE importing the module (settings
# reads env at import). The .env auto-load via pydantic-settings handles
# this in dev; in CI we set it here.
os.environ.setdefault("JWT_SECRET", "0" * 64)

from app.services.auth.jwt_service import (
    issue_token, decode_token, JWT_TTL_DAYS,
    JWTConfigError, JWTInvalidError,
)


class TestTokenRoundtrip:
    def test_issue_then_decode_returns_sub(self):
        token = issue_token("user-uuid-123")
        payload = decode_token(token)
        assert payload["sub"] == "user-uuid-123"
        assert "iat" in payload
        assert "exp" in payload
        assert "jti" in payload

    def test_token_has_no_role_or_email(self):
        # Critical: A1 design contract — JWT must be sub-only. Putting
        # role / email in JWT would create stale-state windows.
        token = issue_token("user-uuid-123")
        payload = decode_token(token)
        assert "role" not in payload
        assert "email" not in payload

    def test_exp_is_7_days_out(self):
        token = issue_token("user-uuid-123")
        payload = decode_token(token)
        delta = payload["exp"] - payload["iat"]
        assert delta == JWT_TTL_DAYS * 86400

    def test_jti_unique_per_call(self):
        t1 = issue_token("user-uuid-123")
        t2 = issue_token("user-uuid-123")
        p1 = decode_token(t1)
        p2 = decode_token(t2)
        assert p1["jti"] != p2["jti"]


class TestTokenTampering:
    def test_modified_token_rejected(self):
        token = issue_token("user-uuid-123")
        # Flip a character in the middle (signature region)
        tampered = token[:-5] + ("X" if token[-5] != "X" else "Y") + token[-4:]
        with pytest.raises(JWTInvalidError):
            decode_token(tampered)

    def test_garbage_token_rejected(self):
        with pytest.raises(JWTInvalidError):
            decode_token("not.a.token")

    def test_expired_token_rejected(self, monkeypatch):
        # Issue with manipulated time so exp is in the past.
        import jwt as pyjwt
        from app.services.auth import jwt_service
        past = datetime.now(timezone.utc) - timedelta(days=8)
        payload = {
            "sub": "user-uuid-123",
            "iat": int(past.timestamp()),
            "exp": int((past + timedelta(days=7)).timestamp()),  # exp = 1 day ago
            "jti": "test",
        }
        token = pyjwt.encode(payload, jwt_service._get_secret(),
                             algorithm=jwt_service.JWT_ALG)
        with pytest.raises(JWTInvalidError):
            decode_token(token)


class TestSecretValidation:
    def test_short_secret_refuses_to_issue(self, monkeypatch):
        from app.config import get_settings
        # Force a fresh settings instance with short secret
        get_settings.cache_clear()
        monkeypatch.setenv("JWT_SECRET", "tooshort")
        with pytest.raises(JWTConfigError, match="32 chars"):
            issue_token("user-uuid-123")
        get_settings.cache_clear()
