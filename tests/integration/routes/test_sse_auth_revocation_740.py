"""Issue #740 — SSE stream auth re-validation on keepalive (R1-migrated).

Migrated from tests/issues/740/test_batch5_medium_backend.py (the
TestSSEAuthRevalidation HTTP half; the smart-truncate/rate-limiter/lazy-repo
unit tests stayed in tests/unit/test_smart_truncate_and_ratelimit_740.py).

R1 repair: the legacy hand-rolled ``_make_client()`` created a temp SQLite DB
but left ``DATABASE_URL`` untouched, so ``create_app``'s ``ensure_all_tables``
dialed the ambient (Postgres) config DB. Replaced with the canonical
``auto_db``/``client`` bootstrap from tests/integration/routes/test_autonomous_api.py
(tmp sqlite + ``DATABASE_URL`` env pin + ``user_repo`` rebind). Assertions
unchanged (the original ``unittest`` asserts were translated 1:1 to plain
``assert`` statements).
"""

import os
import queue as queue_mod
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
    # pytest process would 429 this file's own create requests (they run as
    # user_id=1 too). Clear the per-user hit log so each test starts with a
    # full budget; the limiter itself stays fully in effect within the test.
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


# ── SSE Auth Re-validation ──────────────────────────────────────────


class TestSSEAuthRevalidation:
    """Tests that SSE stream closes on revoked token during keepalive."""

    def test_sse_closes_on_revoked_token(self, client):
        """SSE stream should terminate when token becomes invalid during keepalive."""
        mock_repo = MagicMock()
        mock_repo.get_workflow.return_value = {
            "workflow_id": "wf-1",
            "user_id": 1,
            "status": "developing",
        }

        # Mock event emitter — queue always raises Empty (trigger keepalive)
        mock_emitter = MagicMock()
        mock_q = MagicMock()
        mock_q.get.side_effect = queue_mod.Empty
        mock_emitter.subscribe.return_value = mock_q
        mock_emitter.mark_read.return_value = None
        mock_emitter.unsubscribe.return_value = None

        call_count = 0

        def mock_load_token(token):
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                return None  # Token revoked after first check
            return {
                "id": 1,
                "username": "admin",
                "email": "admin@test.com",
                "role": "admin",
            }

        # Patch auth_required decorator (bypasses initial auth) AND
        # _load_user_from_token in autonomous routes (for keepalive re-check)
        with patch(
            "app.auth.decorators._load_user_from_token",
            side_effect=mock_load_token,
        ):
            with patch("app.routes.autonomous._get_repo", return_value=mock_repo):
                with patch(
                    "app.routes.autonomous._get_event_emitter",
                    return_value=mock_emitter,
                ):
                    resp = client.get("/api/autonomous/workflows/wf-1/events/stream")
                    # Stream should end cleanly (not hang)
                    assert resp.status_code == 200
                    assert "text/event-stream" in resp.content_type
