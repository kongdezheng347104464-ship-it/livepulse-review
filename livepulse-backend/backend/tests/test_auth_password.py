"""Tests for app.services.auth.password — hash + verify + policy."""

import pytest

from app.services.auth.password import (
    hash_password, verify_password, validate_password,
    PasswordPolicyError, MIN_PASSWORD_LEN, MAX_PASSWORD_LEN,
)


class TestHashVerify:
    def test_hash_then_verify_succeeds(self):
        hashed = hash_password("hunter2hunter")
        assert verify_password("hunter2hunter", hashed) is True

    def test_wrong_password_returns_false(self):
        hashed = hash_password("hunter2hunter")
        assert verify_password("wrongpass1", hashed) is False

    def test_garbage_hash_returns_false(self):
        # InvalidHashError path
        assert verify_password("anything1", "not-a-hash") is False

    def test_two_hashes_of_same_password_differ(self):
        # argon2 includes random salt → encoded strings should differ.
        h1 = hash_password("samesame1")
        h2 = hash_password("samesame1")
        assert h1 != h2
        assert verify_password("samesame1", h1)
        assert verify_password("samesame1", h2)


class TestPasswordPolicy:
    def test_valid_password_passes(self):
        validate_password("hunter22")   # 8 chars, letters+digits

    def test_too_short_rejected(self):
        with pytest.raises(PasswordPolicyError, match="至少"):
            validate_password("ab12")

    def test_too_long_rejected(self):
        with pytest.raises(PasswordPolicyError, match="超过"):
            validate_password("a1" * 70)

    def test_no_letter_rejected(self):
        with pytest.raises(PasswordPolicyError, match="字母"):
            validate_password("123456789")

    def test_no_digit_rejected(self):
        with pytest.raises(PasswordPolicyError, match="数字"):
            validate_password("abcdefghij")

    def test_common_password_rejected(self):
        with pytest.raises(PasswordPolicyError, match="常见"):
            validate_password("password123")

    def test_common_password_case_insensitive(self):
        with pytest.raises(PasswordPolicyError):
            validate_password("PASSWORD123")

    def test_non_string_rejected(self):
        with pytest.raises(PasswordPolicyError):
            validate_password(12345678)
