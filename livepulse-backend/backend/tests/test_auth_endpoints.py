"""A1 Day 2 endpoint integration tests.

Targets the live backend at localhost:8004 (same pattern as
tests/api/test_invariants.py). This avoids the TestClient + asyncpg
multi-event-loop pool corruption that happens when tests run
consecutively against an in-process app.

Fixtures use psycopg2 sync for DB row prep (seeding/cleaning
invitations + users) because admin invitation endpoints aren't built
yet (A4).
"""
import os
import uuid

import httpx
import psycopg2
import pytest

os.environ.setdefault("JWT_SECRET", "0" * 64)

from app.config import get_settings

BACKEND_URL = "http://localhost:8004"


def _pg_conn():
    """Open a fresh psycopg2 connection from DATABASE_URL.

    DATABASE_URL uses asyncpg driver — strip it for sync access.
    """
    raw = get_settings().database_url
    # postgresql+asyncpg://... -> postgresql://...
    sync_url = raw.replace("postgresql+asyncpg://", "postgresql://", 1)
    conn = psycopg2.connect(sync_url)
    conn.autocommit = True
    return conn


def _get_admin_id() -> str:
    with _pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT id FROM users WHERE role='admin' LIMIT 1")
        return cur.fetchone()[0]


def _seed_invitation() -> str:
    code = uuid.uuid4().hex[:16]
    admin_id = _get_admin_id()
    with _pg_conn() as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO invitations (code, created_by, created_at) VALUES (%s, %s, NOW())",
            (code, admin_id),
        )
    return code


def _cleanup_invitation(code: str) -> None:
    """Delete the invitation + any user that consumed it."""
    with _pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT used_by_user_id FROM invitations WHERE code=%s", (code,))
        row = cur.fetchone()
        used_by = row[0] if row else None
        cur.execute("DELETE FROM invitations WHERE code=%s", (code,))
        if used_by:
            cur.execute("DELETE FROM users WHERE id=%s", (used_by,))


def _cleanup_user(email: str) -> None:
    with _pg_conn() as c, c.cursor() as cur:
        cur.execute("DELETE FROM users WHERE LOWER(email)=LOWER(%s)", (email,))


@pytest.fixture(scope="module")
def client():
    c = httpx.Client(base_url=BACKEND_URL, timeout=10)
    try:
        c.get("/api/v1/health").raise_for_status()
    except Exception as e:
        c.close()
        pytest.exit(f"backend not reachable at {BACKEND_URL}: {e}. "
                    f"Run `make backend-restart` first.")
    yield c
    c.close()


@pytest.fixture
def fresh_invitation():
    """Create a fresh invitation code, yield, clean up."""
    code = _seed_invitation()
    yield code
    _cleanup_invitation(code)


# ---- register ----

class TestRegister:
    def test_happy_path(self, client, fresh_invitation):
        r = client.post("/api/v1/auth/register", json={
            "email": "newuser@example.com",
            "password": "validpass123",
            "invitation_code": fresh_invitation,
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert "access_token" in body
        assert body["token_type"] == "bearer"
        assert body["expires_in"] == 7 * 86400

    def test_weak_password_rejected(self, client, fresh_invitation):
        r = client.post("/api/v1/auth/register", json={
            "email": "weak@example.com",
            "password": "short1",
            "invitation_code": fresh_invitation,
        })
        # 422 = our policy (or pydantic length); both surface in detail
        assert r.status_code == 422

    def test_common_password_rejected(self, client, fresh_invitation):
        r = client.post("/api/v1/auth/register", json={
            "email": "common@example.com",
            "password": "password123",
            "invitation_code": fresh_invitation,
        })
        assert r.status_code == 422

    def test_duplicate_email_case_insensitive_rejected(self, client, fresh_invitation):
        client.post("/api/v1/auth/register", json={
            "email": "DupCase@Example.com",
            "password": "validpass123",
            "invitation_code": fresh_invitation,
        })
        code2 = _seed_invitation()
        r = client.post("/api/v1/auth/register", json={
            "email": "dupcase@example.com",  # same as DupCase@... after lower
            "password": "validpass123",
            "invitation_code": code2,
        })
        assert r.status_code == 409
        _cleanup_invitation(code2)
        _cleanup_user("dupcase@example.com")

    def test_invalid_invitation_rejected(self, client):
        r = client.post("/api/v1/auth/register", json={
            "email": "noinv@example.com",
            "password": "validpass123",
            "invitation_code": "nonexistentXYZ",
        })
        assert r.status_code == 422
        assert "邀请码" in r.json()["detail"]

    def test_register_ignores_role_field(self, client, fresh_invitation):
        """A1 contract: register schema doesn't accept `role` — Pydantic
        either ignores unknown fields or rejects them. Either way, the
        resulting user MUST be role='user'."""
        r = client.post("/api/v1/auth/register", json={
            "email": "tryroleadmin@example.com",
            "password": "validpass123",
            "invitation_code": fresh_invitation,
            "role": "admin",  # ignored by schema
        })
        assert r.status_code == 200
        # Verify in DB — must be 'user', NOT 'admin'
        with _pg_conn() as c, c.cursor() as cur:
            cur.execute("SELECT role FROM users WHERE email=%s", ("tryroleadmin@example.com",))
            row = cur.fetchone()
            assert row is not None
            assert row[0] == "user"


# ---- login ----

class TestLogin:
    def test_happy_path(self, client, fresh_invitation):
        client.post("/api/v1/auth/register", json={
            "email": "loginuser@example.com",
            "password": "validpass123",
            "invitation_code": fresh_invitation,
        })
        r = client.post("/api/v1/auth/login", json={
            "email": "loginuser@example.com",
            "password": "validpass123",
        })
        assert r.status_code == 200
        assert "access_token" in r.json()

    def test_wrong_password_rejected(self, client, fresh_invitation):
        client.post("/api/v1/auth/register", json={
            "email": "wpw@example.com",
            "password": "validpass123",
            "invitation_code": fresh_invitation,
        })
        r = client.post("/api/v1/auth/login", json={
            "email": "wpw@example.com",
            "password": "wrongpass1",
        })
        assert r.status_code == 401

    def test_nonexistent_email_rejected(self, client):
        r = client.post("/api/v1/auth/login", json={
            "email": "neverexisted@example.com",
            "password": "anypass123",
        })
        assert r.status_code == 401

    def test_email_case_insensitive_login(self, client, fresh_invitation):
        client.post("/api/v1/auth/register", json={
            "email": "casetest@example.com",
            "password": "validpass123",
            "invitation_code": fresh_invitation,
        })
        r = client.post("/api/v1/auth/login", json={
            "email": "CaseTest@Example.com",   # different case
            "password": "validpass123",
        })
        assert r.status_code == 200


# ---- me ----

class TestMe:
    def test_returns_curated_fields(self, client, fresh_invitation):
        reg = client.post("/api/v1/auth/register", json={
            "email": "meuser@example.com",
            "password": "validpass123",
            "invitation_code": fresh_invitation,
        })
        token = reg.json()["access_token"]
        r = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        body = r.json()
        assert set(body.keys()) == {"id", "email", "role", "is_active"}
        assert body["role"] == "user"

    def test_missing_token_rejected(self, client):
        r = client.get("/api/v1/auth/me")
        assert r.status_code == 401

    def test_garbage_token_rejected(self, client):
        r = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer garbage"})
        assert r.status_code == 401

    def test_wrong_scheme_rejected(self, client, fresh_invitation):
        reg = client.post("/api/v1/auth/register", json={
            "email": "scheme@example.com",
            "password": "validpass123",
            "invitation_code": fresh_invitation,
        })
        token = reg.json()["access_token"]
        # "Basic <token>" — should fail
        r = client.get("/api/v1/auth/me", headers={"Authorization": f"Basic {token}"})
        assert r.status_code == 401
