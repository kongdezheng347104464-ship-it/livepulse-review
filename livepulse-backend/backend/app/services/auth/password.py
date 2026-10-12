"""Password hashing + validation (A1 2026-05-31).

Hashing: argon2 (id variant) via argon2-cffi. Default params are fine
for 2026 hardware; tunable via `hasher = PasswordHasher(...)` if dev
machines feel slow.

Complexity policy: NIST SP 800-63B-aligned simplification:
  * length 8 ≤ len ≤ 128
  * MUST contain at least one letter
  * MUST contain at least one digit
  * MUST NOT be in TOP_100_COMMON_PASSWORDS

No mandatory special-char / uppercase rules — those lead to predictable
patterns ("Password1!") without raising real entropy. Modern guidance:
let length + breach-check do the work.
"""
from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError


_hasher = PasswordHasher()

MIN_PASSWORD_LEN = 8
MAX_PASSWORD_LEN = 128

# Curated subset of the SecLists rockyou top-100. Add more as we hit
# them in M1 internal testing. The point isn't completeness — it's
# "block the embarrassingly bad ones".
_COMMON_PASSWORDS = frozenset({
    "password", "password1", "password123", "passw0rd",
    "12345678", "123456789", "1234567890", "qwerty123",
    "abc12345", "abcd1234", "letmein1", "iloveyou1",
    "admin123", "admin1234", "welcome1", "welcome123",
    "monkey123", "dragon123", "master123", "sunshine1",
    "princess1", "qwertyui", "football1", "baseball1",
})


class PasswordPolicyError(ValueError):
    """Raised by validate_password for any rule failure. The message is
    safe to surface to end users (no internal detail leaks)."""


def hash_password(plain: str) -> str:
    """Returns the argon2 encoded hash. argon2-cffi's default profile
    encodes the version, time/memory costs, and salt inline, so we can
    upgrade params later without breaking old hashes."""
    return _hasher.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Constant-time verify. Returns False for any mismatch or for
    structurally invalid stored hash (which would only happen if the
    DB was tampered with — still better to return False than raise)."""
    try:
        return _hasher.verify(hashed, plain)
    except (VerifyMismatchError, InvalidHashError):
        return False


def validate_password(plain: str) -> None:
    """Raises PasswordPolicyError on rule violation. Otherwise returns
    None silently — callers should treat that as "policy passes"."""
    if not isinstance(plain, str):
        raise PasswordPolicyError("密码格式不正确")
    if len(plain) < MIN_PASSWORD_LEN:
        raise PasswordPolicyError(f"密码至少需要 {MIN_PASSWORD_LEN} 个字符")
    if len(plain) > MAX_PASSWORD_LEN:
        raise PasswordPolicyError(f"密码不能超过 {MAX_PASSWORD_LEN} 个字符")
    if not any(c.isalpha() for c in plain):
        raise PasswordPolicyError("密码必须至少包含一个字母")
    if not any(c.isdigit() for c in plain):
        raise PasswordPolicyError("密码必须至少包含一个数字")
    if plain.lower() in _COMMON_PASSWORDS:
        raise PasswordPolicyError("这个密码太常见，请换一个更独特的")
