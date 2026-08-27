"""Issue #740 — Remote machine admin permission validation on create (R1-migrated).

Migrated from tests/issues/740/test_batch6_distributed_lock.py (the
TestRemoteMachineAdminValidation HTTP half; the distributed-lock unit tests
stayed in tests/unit/test_autonomous_distributed_lock.py).

R1 repair: the legacy ``_make_client()`` seeded a temp SQLite DB and pinned
``DATABASE_URL`` via a manual ``patch.dict`` but left the ``user_repo`` module
global bound to the import-time DB (per-test mocks papered over it). Replaced
with the canonical ``auto_db``/``client`` bootstrap from
tests/integration/routes/test_autonomous_api.py (tmp sqlite + ``DATABASE_URL``
env pin via monkeypatch + ``user_repo`` rebind). Assertions unchanged (the
original ``unittest`` asserts were translated 1:1 to plain ``assert``
statements).
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
                cursor.execute(
                    "INSERT INTO users (username, email, password_hash, role) VALUES (?, ?, ?, ?)",
                    ("testuser", "user@test.com", "hash123", "user"),
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


# ── Remote Machine Admin Validation ─────────────────────────────────


class TestRemoteMachineAdminValidation:
    """Tests for remote machine admin permission check in create_workflow."""

    def _app_repos(self, user_id=1, role="admin"):
        """Patch the route-level user_repo singleton (bound at import time to
        whatever DATABASE_URL was then active — cannot be pointed at the temp
        DB via env) with a stub returning the test user."""
        user = {
            "id": user_id,
            "username": "admin" if role == "admin" else "testuser",
            "email": f"{role}@test.com",
            "role": role,
            "tenant_id": 1,
            "is_active": 1,
            # The local-workspace permission check requires a system account.
            "system_account": "testacct",
        }
        repo = MagicMock()
        repo.get_user_by_id.return_value = user
        return patch("app.routes.autonomous.user_repo", repo)

    def _mock_auth(self, user_id=1, role="admin"):
        return patch(
            "app.auth.decorators._load_user_from_token",
            return_value={
                "id": user_id,
                "username": "admin" if role == "admin" else "testuser",
                "email": f"{role}@test.com",
                "role": role,
            },
        )

    def test_rejects_non_admin_remote_workflow(self, client):
        """Non-admin user without machine admin permission should be rejected."""
        mock_repo = MagicMock()
        mock_repo.count_active_workflows_by_user.return_value = 0
        mock_repo.create_workflow.return_value = {"workflow_id": "wf-1"}

        with self._mock_auth(user_id=2, role="user"), self._app_repos(user_id=2, role="user"):
            with patch("app.routes.autonomous._get_repo", return_value=mock_repo):
                with patch(
                    "app.routes.autonomous.check_machine_admin_permission", return_value=False
                ):
                    resp = client.post(
                        "/api/autonomous/workflows",
                        json={
                            "requirements_text": "test",
                            "cli_tool": "claude-code",
                            "project_path": "/tmp/project",
                            "workspace_type": "remote",
                            "remote_machine_id": "machine-123",
                            "branch_strategy": "worktree",
                        },
                    )

        assert resp.status_code == 403
        data = resp.get_json()
        assert "machine admin" in data["error"].lower()

    def test_allows_admin_remote_workflow(self, client):
        """System admin should be able to create remote workflows."""
        mock_repo = MagicMock()
        mock_repo.count_active_workflows_by_user.return_value = 0
        mock_repo.create_workflow.return_value = {
            "workflow_id": "wf-1",
            "title": "",
        }

        with self._mock_auth(user_id=1, role="admin"), self._app_repos(user_id=1, role="admin"):
            with patch("app.routes.autonomous._get_repo", return_value=mock_repo):
                with patch("app.routes.autonomous._get_event_emitter"):
                    resp = client.post(
                        "/api/autonomous/workflows",
                        json={
                            "requirements_text": "test",
                            "cli_tool": "claude-code",
                            "project_path": "/tmp/project",
                            "workspace_type": "remote",
                            "remote_machine_id": "machine-123",
                            "branch_strategy": "worktree",
                        },
                    )

        assert resp.status_code == 201

    def test_allows_local_workflow_without_check(self, client):
        """Local workflows should not require machine admin check."""
        mock_repo = MagicMock()
        mock_repo.count_active_workflows_by_user.return_value = 0
        mock_repo.create_workflow.return_value = {
            "workflow_id": "wf-1",
            "title": "",
        }

        with self._mock_auth(user_id=2, role="user"), self._app_repos(user_id=2, role="user"):
            with patch("app.routes.autonomous._get_repo", return_value=mock_repo):
                with patch("app.routes.autonomous._get_event_emitter"):
                    # Path-access probing (sudo test -e/-r/-w) is not what
                    # this test exercises; stub it accessible.
                    with patch(
                        "app.routes.autonomous._run_as_user",
                        return_value=MagicMock(returncode=0),
                    ):
                        resp = client.post(
                            "/api/autonomous/workflows",
                            json={
                                "requirements_text": "test",
                                "cli_tool": "claude-code",
                                "project_path": "/tmp/project",
                                "workspace_type": "local",
                                "branch_strategy": "worktree",
                            },
                        )

        assert resp.status_code == 201

    def test_allows_machine_admin_remote_workflow(self, client):
        """Machine admin (non-system admin) should be able to create remote workflows."""
        mock_repo = MagicMock()
        mock_repo.count_active_workflows_by_user.return_value = 0
        mock_repo.create_workflow.return_value = {
            "workflow_id": "wf-1",
            "title": "",
        }

        with self._mock_auth(user_id=2, role="user"), self._app_repos(user_id=2, role="user"):
            with patch("app.routes.autonomous._get_repo", return_value=mock_repo):
                with patch("app.auth.decorators._check_machine_admin", return_value=True):
                    with patch("app.routes.autonomous._get_event_emitter"):
                        resp = client.post(
                            "/api/autonomous/workflows",
                            json={
                                "requirements_text": "test",
                                "cli_tool": "claude-code",
                                "project_path": "/tmp/project",
                                "workspace_type": "remote",
                                "remote_machine_id": "machine-123",
                                "branch_strategy": "worktree",
                            },
                        )

        assert resp.status_code == 201
