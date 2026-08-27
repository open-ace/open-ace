"""Issue #740 — Path validation and retry limit API routes (R1-migrated).

Migrated from tests/issues/740/test_batch2_idempotency_validation.py (HTTP
halves of TestPathValidation and TestRetryLimit; the orchestrator-level unit
tests stayed in tests/unit/test_autonomous_workflow_idempotency.py).

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


# ── Test: Path Validation ────────────────────────────────────────────


class TestPathValidation:
    """Verify project_path validation in create_workflow."""

    @pytest.fixture(autouse=True)
    def _allow_quota(self):
        """These tests exercise path validation, not the quota/rate gate. Stub
        QuotaManager to allow-by-default so the (real, DB-backed) quota check
        doesn't reach the test's schema-less DB and spuriously 429 before path
        validation runs, reset the module-global workflow rate limiter
        (``_workflow_rate_limiter`` accumulates per-user hits across the whole
        pytest session, so without a reset these create-workflow requests are
        429'd once earlier tests in the shard exhaust the 10/hour budget), and
        stub the module-level ``user_repo.get_user_by_id`` (its db binds at
        import to a CI db with no users table, so the route's user lookup past
        path validation 500s once the reset lets valid-path requests through).
        The #2457 assert-429 / assert-500 cluster."""
        from app.routes.autonomous import _workflow_rate_limiter

        _workflow_rate_limiter._hits.clear()
        qmock = MagicMock()
        qmock.return_value.check_quota.return_value = {"allowed": True, "reason": None}
        with (
            patch("app.modules.governance.quota_manager.QuotaManager", qmock),
            patch(
                "app.routes.autonomous.user_repo.get_user_by_id",
                return_value={
                    "id": 1,
                    "username": "admin",
                    "system_account": "",
                    "role": "admin",
                    "tenant_id": None,
                },
            ),
        ):
            yield

    def test_rejects_path_traversal(self, client):
        """Should reject paths with '..' traversal."""
        repo = MagicMock()
        repo.create_workflow.return_value = {"workflow_id": "wf-1"}

        with _mock_auth():
            with patch("app.routes.autonomous.auto_repo", repo):
                resp = client.post(
                    "/api/autonomous/workflows",
                    json={
                        "requirements_text": "test",
                        "cli_tool": "claude-code",
                        "project_path": "/tmp/../../etc/passwd",
                    },
                )

        assert resp.status_code == 400
        data = resp.get_json()
        assert "path traversal" in data["error"].lower()

    def test_rejects_relative_path(self, client):
        """Should reject non-absolute paths."""
        repo = MagicMock()
        repo.create_workflow.return_value = {"workflow_id": "wf-1"}

        with _mock_auth():
            with patch("app.routes.autonomous.auto_repo", repo):
                resp = client.post(
                    "/api/autonomous/workflows",
                    json={
                        "requirements_text": "test",
                        "cli_tool": "claude-code",
                        "project_path": "relative/path/project",
                    },
                )

        assert resp.status_code == 400
        data = resp.get_json()
        assert "absolute" in data["error"].lower()

    def test_accepts_valid_absolute_path(self, client):
        """Should accept valid absolute paths."""
        repo = MagicMock()
        repo.create_workflow.return_value = {
            "workflow_id": "wf-ok",
            "title": "test",
            "status": "pending",
            "cli_tool": "claude-code",
        }

        with _mock_auth():
            with patch("app.routes.autonomous.auto_repo", repo):
                resp = client.post(
                    "/api/autonomous/workflows",
                    json={
                        "requirements_text": "test",
                        "cli_tool": "claude-code",
                        "project_path": "/home/user/projects/my-app",
                    },
                )

        assert resp.status_code == 201

    def test_accepts_new_project_without_path(self, client):
        """Should accept when is_new_project=true (no path needed)."""
        repo = MagicMock()
        repo.create_workflow.return_value = {
            "workflow_id": "wf-new",
            "title": "new proj",
            "status": "pending",
            "cli_tool": "claude-code",
        }

        with _mock_auth():
            with patch("app.routes.autonomous.auto_repo", repo):
                resp = client.post(
                    "/api/autonomous/workflows",
                    json={
                        "requirements_text": "test",
                        "cli_tool": "claude-code",
                        "is_new_project": True,
                    },
                )

        assert resp.status_code == 201


# ── Test: Retry Limit ────────────────────────────────────────────────


class TestRetryLimit:
    """Verify retry count limit for failed workflows."""

    def test_retry_increments_count(self, client):
        """Retry should increment retry_count."""
        repo = MagicMock()
        repo.get_workflow.return_value = {
            "workflow_id": "wf-retry",
            "user_id": 1,
            "status": "failed",
            "retry_count": 2,
        }
        repo.update_workflow.return_value = None

        with _mock_auth():
            with patch("app.routes.autonomous.auto_repo", repo):
                resp = client.post("/api/autonomous/workflows/wf-retry/retry")

        assert resp.status_code == 200
        call_args = repo.update_workflow.call_args[0]
        assert call_args[1]["retry_count"] == 3

    def test_rejects_retry_over_limit(self, client):
        """Should reject retry when retry_count >= MAX_RETRY_COUNT."""
        repo = MagicMock()
        repo.get_workflow.return_value = {
            "workflow_id": "wf-max-retry",
            "user_id": 1,
            "status": "failed",
            "retry_count": 5,
        }

        with _mock_auth():
            with patch("app.routes.autonomous.auto_repo", repo):
                resp = client.post("/api/autonomous/workflows/wf-max-retry/retry")

        assert resp.status_code == 400
        data = resp.get_json()
        assert "retry count" in data["error"].lower()

    def test_allows_retry_under_limit(self, client):
        """Should allow retry when retry_count < MAX_RETRY_COUNT."""
        repo = MagicMock()
        repo.get_workflow.return_value = {
            "workflow_id": "wf-under-limit",
            "user_id": 1,
            "status": "failed",
            "retry_count": 4,
        }
        repo.update_workflow.return_value = None

        with _mock_auth():
            with patch("app.routes.autonomous.auto_repo", repo):
                resp = client.post("/api/autonomous/workflows/wf-under-limit/retry")

        assert resp.status_code == 200

    def test_handles_none_retry_count(self, client):
        """Should treat None retry_count as 0."""
        repo = MagicMock()
        repo.get_workflow.return_value = {
            "workflow_id": "wf-no-retry",
            "user_id": 1,
            "status": "failed",
            "retry_count": None,
        }
        repo.update_workflow.return_value = None

        with _mock_auth():
            with patch("app.routes.autonomous.auto_repo", repo):
                resp = client.post("/api/autonomous/workflows/wf-no-retry/retry")

        assert resp.status_code == 200
        call_args = repo.update_workflow.call_args[0]
        assert call_args[1]["retry_count"] == 1
