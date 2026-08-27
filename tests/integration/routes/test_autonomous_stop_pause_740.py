"""Issue #740 — Stop/pause API routes cancel running agent tasks (R1-migrated).

Migrated from tests/issues/740/test_batch1_session_wiring.py (HTTP half of
TestStopPauseCancelsTask; the direct ``_cancel_running_task`` calls stayed in
tests/unit/test_autonomous_session_wiring.py).

R1 repair: the legacy hand-rolled ``_make_client()`` created a temp SQLite DB
but left ``DATABASE_URL`` untouched, so ``create_app``'s ``ensure_all_tables``
dialed the ambient (Postgres) config DB. Replaced with the canonical
``auto_db``/``client`` bootstrap from tests/integration/routes/test_autonomous_api.py
(tmp sqlite + ``DATABASE_URL`` env pin + ``user_repo`` rebind). Assertions
unchanged.
"""

import os
from unittest.mock import MagicMock, patch

import pytest

import app.repositories.database as db_mod
from app.repositories.database import Database

pytestmark = [pytest.mark.regression, pytest.mark.issue(740)]

# ── Canonical fixtures (tests/integration/routes/test_autonomous_api.py) ──


@pytest.fixture
def auto_db(tmp_path):
    """Create a temporary SQLite database with autonomous tables."""
    with patch.object(db_mod, "is_postgresql", return_value=False):
        orig = db_mod.adapt_sql
        db_mod.adapt_sql = lambda q: q
        try:
            db_path = str(tmp_path / "test_api.db")
            db = Database(db_url=f"sqlite:///{db_path}")
            conn = db.get_connection()
            try:
                from app.repositories.schema_init import load_schema_from_file

                # Create the FULL authoritative schema (incl. users.deleted_at) on the
                # empty DB FIRST, then seed. Do NOT hand-CREATE an old users table —
                # load_schema's CREATE TABLE IF NOT EXISTS will not add the missing
                # column to an already-existing table (legacy tests/issues escape hatch).
                load_schema_from_file(db_url=db.db_url, dialect="sqlite")
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO users (username, email, password_hash, role) VALUES (?, ?, ?, ?)",
                    ("admin", "admin@test.com", "hash123", "platform_admin"),
                )
                conn.commit()
            finally:
                conn.close()
            yield db
        finally:
            db_mod.adapt_sql = orig
            try:
                os.unlink(db_path)
            except OSError:
                pass


@pytest.fixture
def client(auto_db, monkeypatch):
    """Create a Flask test client with session cookie set."""
    from app import create_app
    from app.repositories.user_repo import UserRepository

    # create_app()'s ensure_all_tables() picks its DB from get_database_url()
    # (DATABASE_URL), which defaults to the dev Postgres DB locally; and the
    # module-global user_repo (app.routes.autonomous) binds Database() once at
    # import. Point both at auto_db's seeded SQLite DB so the app and the route
    # share the fixture's database. monkeypatch auto-restores both.
    monkeypatch.setenv("DATABASE_URL", auto_db.db_url)
    # The create endpoint's module-level rate limiter (10/user/hour) is
    # process-global state: hits accumulated by earlier test files in the same
    # pytest process would 429 this file's own requests (they run as user_id=1
    # too). Clear the per-user hit log so each test starts with a full budget;
    # the limiter itself stays fully in effect within the test.
    monkeypatch.setattr("app.routes.autonomous._workflow_rate_limiter._hits", {})
    # These endpoint tests were written for single-tenant semantics: pin the
    # deployment mode so the default branch_strategy ("new-branch") is not
    # rejected by the multi-user _shared_checkout_rejection gate (#2021;
    # that rejection logic is separately unit-covered by
    # tests/unit/test_git_path_hardening.py).
    monkeypatch.setenv("OPENACE_ALLOW_SHARED_CHECKOUT", "1")
    app = create_app({"TESTING": True})
    monkeypatch.setattr("app.routes.autonomous.user_repo", UserRepository(db=auto_db))
    with app.app_context():
        c = app.test_client()
        c.set_cookie("session_token", "test-token")
        yield c


def _mock_auth(user_id=1, role="admin"):
    """Patch auth to bypass authentication."""
    return patch(
        "app.auth.decorators._load_user_from_token",
        return_value={
            "id": user_id,
            "username": "admin" if role == "admin" else "testuser",
            "email": f"{role}@test.com",
            "role": role,
        },
    )


# ── Test: Stop/Pause API cancellation ────────────────────────────────


class TestStopPauseCancelsTask:
    """Verify stop/pause API routes cancel running agent tasks."""

    def test_stop_calls_cancel_running_task(self, client):
        """stop_workflow should call _cancel_running_task."""
        repo = MagicMock()
        repo.get_workflow.return_value = {
            "workflow_id": "wf-stop-test",
            "user_id": 1,
            "status": "developing",
        }
        repo.update_workflow.return_value = None

        with _mock_auth():
            with patch("app.routes.autonomous.auto_repo", repo):
                with patch("app.routes.autonomous._stop_running_task") as mock_cancel:
                    resp = client.post("/api/autonomous/workflows/wf-stop-test/stop")

        assert resp.status_code == 200
        mock_cancel.assert_called_once_with("wf-stop-test")

    def test_pause_calls_cancel_running_task(self, client):
        """pause_workflow should call _cancel_running_task."""
        repo = MagicMock()
        repo.get_workflow.return_value = {
            "workflow_id": "wf-pause-test",
            "user_id": 1,
            "status": "developing",
        }
        repo.update_workflow.return_value = None

        with _mock_auth():
            with patch("app.routes.autonomous.auto_repo", repo):
                with patch("app.routes.autonomous._pause_running_task") as mock_cancel:
                    resp = client.post("/api/autonomous/workflows/wf-pause-test/pause")

        assert resp.status_code == 200
        mock_cancel.assert_called_once_with("wf-pause-test")
