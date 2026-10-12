"""Tests for app.services.ownership — A2 Day 2 (2026-05-31).

Strategy split (following test_auth_endpoints.py precedent):
  * `apply_owner_scope` is a PURE function over a SQLAlchemy Select —
    unit-testable here by inspecting the compiled SQL.
  * `get_owned_session` / `get_owned_anchor` / `assert_owned_anchor`
    are async + DB-bound. The project's asyncpg pool gets corrupted
    when fixtures create multiple loops (asyncio_default_test_loop_scope
    defaults to function but the engine binds connections to the first
    loop that uses them — same issue test_auth_endpoints.py works around
    by running against the live backend via httpx).
    THESE HELPERS ARE EXERCISED END-TO-END BY THE DAY 3 CROSS-TENANT
    INVARIANTS in tests/api/test_invariants.py (Group 13 — pending).
    Coverage check: if every Day 3 invariant fails after removing
    helper calls from a refactored endpoint, the helper logic IS
    being exercised.

Why we still keep this file (vs deleting + relying purely on Day 3):
  * apply_owner_scope unit tests catch SELECT-shape regressions cheaply
  * Smoke import asserts the helper symbols exist (catches accidental
    renames before integration tests reveal them)
"""
import pytest
from sqlalchemy import select

from app.models.anchor import Anchor
from app.models.session import LiveSession
from app.services.auth.current_user import UserContext
from app.services.ownership import (
    apply_owner_scope,
    assert_owned_anchor,
    get_owned_anchor,
    get_owned_session,
)


# ---------------------------------------------------------------------
# apply_owner_scope — pure function, full coverage here
# ---------------------------------------------------------------------


class TestApplyOwnerScope:
    def test_admin_pass_through(self):
        """Admin sees no extra WHERE clause."""
        admin = UserContext(id="admin-1", role="admin")
        stmt = select(LiveSession)
        scoped = apply_owner_scope(stmt, LiveSession, admin)
        sql = str(scoped.compile(compile_kwargs={"literal_binds": True}))
        # owner_user_id appears as a SELECT column (it's on the model) —
        # what we forbid is a WHERE filter on it being silently added.
        assert "WHERE" not in sql.upper(), \
            f"admin scope should not add WHERE: {sql!r}"

    def test_user_gets_owner_filter(self):
        user = UserContext(id="user-uuid-1", role="user")
        stmt = select(LiveSession)
        scoped = apply_owner_scope(stmt, LiveSession, user)
        sql = str(scoped.compile(compile_kwargs={"literal_binds": True}))
        assert "WHERE" in sql.upper()
        assert "owner_user_id" in sql
        assert "user-uuid-1" in sql

    def test_works_on_anchor_too(self):
        """Helper isn't LiveSession-specific."""
        user = UserContext(id="user-uuid-1", role="user")
        stmt = select(Anchor)
        scoped = apply_owner_scope(stmt, Anchor, user)
        sql = str(scoped.compile(compile_kwargs={"literal_binds": True}))
        assert "owner_user_id" in sql
        assert "user-uuid-1" in sql

    def test_preserves_existing_where_clauses(self):
        """Layering rule: existing filters MUST be preserved, not replaced."""
        user = UserContext(id="user-uuid-1", role="user")
        stmt = select(LiveSession).where(LiveSession.status == "analyzed")
        scoped = apply_owner_scope(stmt, LiveSession, user)
        sql = str(scoped.compile(compile_kwargs={"literal_binds": True}))
        assert "analyzed" in sql, "existing WHERE clause was wiped"
        assert "user-uuid-1" in sql, "owner filter not appended"


# ---------------------------------------------------------------------
# Smoke: helper symbols exist + are callable (cheap typo check)
# ---------------------------------------------------------------------


class TestHelperSymbols:
    def test_get_owned_session_is_async_callable(self):
        import inspect
        assert inspect.iscoroutinefunction(get_owned_session)

    def test_get_owned_anchor_is_async_callable(self):
        import inspect
        assert inspect.iscoroutinefunction(get_owned_anchor)

    def test_assert_owned_anchor_is_async_callable(self):
        import inspect
        assert inspect.iscoroutinefunction(assert_owned_anchor)

    def test_helpers_have_distinct_implementations_but_same_contract(self):
        """assert_owned_anchor's docstring promises 'identical to
        get_owned_anchor' — verify both signatures match so future
        readers can trust the comment."""
        import inspect
        get_sig = inspect.signature(get_owned_anchor)
        assert_sig = inspect.signature(assert_owned_anchor)
        assert list(get_sig.parameters.keys()) == list(assert_sig.parameters.keys())
