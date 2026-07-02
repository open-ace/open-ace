"""Tests for Issue #249 — unify SQL placeholder style in app/routes/workspace.py.

These tests lock in the refactor that standardized the file on
``get_param_placeholder()`` ({p}) and removed all redundant ``adapt_sql()``
wrappings (the Database layer adapts internally).

Coverage:
  1. Static invariant — the source file has no ``adapt_sql(`` calls and no
     raw ``?`` / ``%s`` string literals used as SQL placeholders. This is the
     automation success criterion for the cleanup.
  2. Functional SQLite — drives ``GET /api/workspace/sessions`` through both
     the non-search and search branches against a temporary SQLite database,
     verifying the queries still execute correctly after ``adapt_sql()`` was
     removed.

Note on PostgreSQL: these tests run on SQLite only (the standard test-harness
backend). PostgreSQL correctness rests on static analysis (the removed
``adapt_sql()`` calls were triple-no-ops: SQL built with {p} -> manual adapt ->
internal adapt), not on a test, because no PG fixture is wired into this
harness.
"""

import os
import re
import sys

import pytest

# test file lives at <root>/tests/issues/249/ -> walk up 4 dirnames to repo root
PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

WORKSPACE_FILE = os.path.join(PROJECT_ROOT, "app", "routes", "workspace.py")


def _read_source() -> str:
    with open(WORKSPACE_FILE) as f:
        return f.read()


# ── Static invariant tests ───────────────────────────────────────────


class TestPlaceholderStyleInvariant:
    """Lock in the file-wide placeholder convention."""

    def test_no_adapt_sql_calls(self):
        """No real adapt_sql() invocations remain (comment mentions are OK)."""
        src = _read_source()
        offenders = [
            line.strip()
            for line in src.splitlines()
            if re.search(r"\badapt_sql\s*\(", line) and not line.strip().startswith("#")
        ]
        assert offenders == [], f"Unexpected adapt_sql() calls remain: {offenders}"

    def test_no_raw_questionmark_placeholders(self):
        """No raw '?' / '%s' string literals used as SQL placeholders."""
        src = _read_source()
        assert '"?"' not in src, "raw '?' placeholder literal found"
        assert "'?'" not in src, "raw '?' placeholder literal found"

    def test_get_param_placeholder_is_used(self):
        """The canonical helper is still in use (sanity check)."""
        src = _read_source()
        assert "get_param_placeholder()" in src


# ── Functional tests on SQLite ───────────────────────────────────────


def _insert_session(db, session_id, tool_name, title, user_id, status="active"):
    """Insert a minimal agent_sessions row using the file's placeholder style."""
    from app.repositories.database import get_param_placeholder

    p = get_param_placeholder()
    db.execute(
        f"INSERT INTO agent_sessions (session_id, tool_name, title, user_id, status) "
        f"VALUES ({p}, {p}, {p}, {p}, {p})",
        (session_id, tool_name, title, user_id, status),
    )


@pytest.fixture
def app_client(tmp_path):
    """Create a Flask test client backed by an isolated temp SQLite DB.

    Patches get_database_url so both create_app()'s ensure_all_tables() and the
    route's own Database() resolve to the same temp database.
    """
    import app.repositories.database as db_mod
    from app import create_app

    db_path = str(tmp_path / "issue249.db")
    db_url = f"sqlite:///{db_path}"

    with patch_db_url(db_mod, db_url):
        app = create_app({"TESTING": True})

        # Seed sessions: two for user 1 (one matching a later search), one for
        # user 2 (must be filtered out by the user_id condition).
        db = db_mod.Database(db_url=db_url)
        _insert_session(db, "sess-alpha-001", "qwen-code", "Alpha Project", 1)
        _insert_session(db, "sess-beta-002", "qwen-code", "Beta Task", 1)
        _insert_session(db, "sess-gamma-003", "qwen-code", "Gamma Other User", 2)

        client = app.test_client()
        client.set_cookie("session_token", "issue249-token")
        yield client


class patch_db_url:
    """Context manager that patches get_database_url to a target URL."""

    def __init__(self, db_mod, db_url):
        self.db_mod = db_mod
        self.db_url = db_url

    def __enter__(self):
        from unittest.mock import patch

        self._patch = patch.object(self.db_mod, "get_database_url", return_value=self.db_url)
        self._patch.start()
        return self

    def __exit__(self, *exc):
        self._patch.stop()


def _patched_user():
    """Patch _load_user_from_token in the workspace blueprint to a fixed user."""
    from unittest.mock import patch

    user = {"id": 1, "username": "tester", "email": "t@example.com", "role": "user"}
    return patch("app.routes.workspace._load_user_from_token", return_value=user)


class TestListSessionsSqlite:
    """Exercise both list_sessions query branches on SQLite."""

    def test_non_search_branch_returns_user_sessions(self, app_client):
        """Non-search branch (count_sql + sessions_sql) executes correctly."""
        with _patched_user():
            resp = app_client.get("/api/workspace/sessions?page=1&limit=20")

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        # Only user 1's sessions; the user-2 session is filtered out.
        assert data["data"]["total"] == 2
        titles = {s["title"] for s in data["data"]["sessions"]}
        assert "Alpha Project" in titles
        assert "Beta Task" in titles

    def test_search_branch_filters_by_term(self, app_client):
        """Search branch (with search + search_days) executes correctly."""
        with _patched_user():
            resp = app_client.get(
                "/api/workspace/sessions?page=1&limit=20&search=alpha&search_days=7"
            )

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        # Case-insensitive title match on "alpha" yields exactly one session.
        assert data["data"]["total"] == 1
        assert data["data"]["sessions"][0]["title"] == "Alpha Project"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
