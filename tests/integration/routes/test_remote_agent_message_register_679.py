"""Register-message trust boundary tests for the remote agent HTTP API (Issue #679).

Fix 2 of #679: the ``register`` message type on POST /api/remote/agent/message
validates ``machine_id`` against the database before tracking the connection,
so an unknown machine cannot inject itself into ``_connections``.

Migrated from tests/issues/679/test_issue_679.py (the three route tests),
rewritten from the print-based ok()/fail() harness to real pytest asserts —
every former ok()/fail() predicate is now an assert. Changes vs. the legacy
harness, all scoped via monkeypatch/fixtures (auto-restored):
- ``tempfile.mktemp`` -> ``tmp_path``;
- destructive never-restored global mutations (``db_mod.DB_PATH``,
  ``DEFAULT_SQLITE_PATH``, ``is_postgresql``, ``ram_compat.is_postgresql``,
  ``sm_compat.DB_PATH``, ``ram_mod._agent_manager = None``) -> ``monkeypatch``;
- dropped the ``_load_user_from_token`` swap: /api/remote/agent/message is in
  the blueprint's auth-exempt list (it uses agent-side Bearer auth instead),
  so no user auth runs for these requests;
- dropped ``os.environ.setdefault("SECRET_KEY", ...)`` (no session auth used);
- machine_ids are now UUID strings: #2540 added a UUID format gate in front
  of the DB check, and the legacy non-UUID ids ("mid-valid", ...) tripped
  that gate with 400 before reaching the #679 DB validation. UUID-shaped ids
  keep asserting the SAME #679 predicates (unknown -> 404, known -> 200
  register_ack) against the code path #679 actually guards.
"""

import sqlite3
import uuid
from datetime import datetime, timezone

import pytest
from flask import Flask

import app.modules.workspace.remote_agent_manager as ram_mod
import app.modules.workspace.session_manager as sm_compat
import app.repositories.database as db_mod

pytestmark = [pytest.mark.regression, pytest.mark.issue(679)]


@pytest.fixture
def manager(tmp_path, monkeypatch):
    """Create a RemoteAgentManager with a fresh temp SQLite DB (Issue #679)."""
    db_path = str(tmp_path / "remote-agent-register-679.db")

    # Pin the env-resolved URL too: any dynamic get_database_url() call
    # (Priority 1 = env) then resolves to this fixture's SQLite file instead
    # of the ambient ~/.open-ace/config.json Postgres URL.
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setattr(db_mod, "DB_PATH", db_path)
    monkeypatch.setattr(db_mod, "DEFAULT_SQLITE_PATH", db_path)
    monkeypatch.setattr(db_mod, "is_postgresql", lambda: False)
    monkeypatch.setattr(ram_mod, "is_postgresql", lambda: False)
    monkeypatch.setattr(sm_compat, "DB_PATH", db_path)
    # The route resolves the manager through the module-level singleton.
    from app.modules.workspace.remote_agent_manager import RemoteAgentManager

    mgr = RemoteAgentManager(db_path=db_path)
    monkeypatch.setattr(ram_mod, "_agent_manager", mgr)
    # Keep ensure_db_dir()'s makedirs inside tmp_path (never ~/.open-ace).
    monkeypatch.setattr(db_mod, "CONFIG_DIR", str(tmp_path))

    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS remote_machines ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "machine_id TEXT NOT NULL UNIQUE, "
        "machine_name TEXT, "
        "hostname TEXT, "
        "os_type TEXT, "
        "os_version TEXT, "
        "ip_address TEXT, "
        "status TEXT DEFAULT 'offline', "
        "agent_version TEXT, "
        "capabilities TEXT, "
        "cli_path TEXT, "
        "work_dir TEXT, "
        "tenant_id INTEGER, "
        "created_by INTEGER, "
        "last_heartbeat TIMESTAMP, "
        "created_at TIMESTAMP, "
        "updated_at TIMESTAMP)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS agent_sessions ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "session_id TEXT NOT NULL UNIQUE, "
        "session_type TEXT DEFAULT 'chat', "
        "title TEXT, "
        "tool_name TEXT, "
        "host_name TEXT, "
        "user_id INTEGER, "
        "status TEXT DEFAULT 'active', "
        "context TEXT, "
        "settings TEXT, "
        "project_id TEXT, "
        "project_path TEXT, "
        "total_tokens INTEGER DEFAULT 0, "
        "total_input_tokens INTEGER DEFAULT 0, "
        "total_output_tokens INTEGER DEFAULT 0, "
        "message_count INTEGER DEFAULT 0, "
        "request_count INTEGER DEFAULT 0, "
        "model TEXT, "
        "tags TEXT, "
        "workspace_type TEXT DEFAULT 'local', "
        "remote_machine_id TEXT, "
        "paused_at TIMESTAMP, "
        "created_at TIMESTAMP, "
        "updated_at TIMESTAMP, "
        "completed_at TIMESTAMP, "
        "expires_at TIMESTAMP)"
    )
    conn.commit()
    conn.close()
    return mgr


@pytest.fixture
def client(manager):
    """Minimal Flask app with the remote blueprint at /api/remote."""
    from app.routes import remote as remote_mod

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret"
    app.register_blueprint(remote_mod.remote_bp, url_prefix="/api/remote")
    return app.test_client()


# ── Fix 2 Tests: register message validates machine_id ──


def test_register_unknown_machine_returns_404(client, manager):
    """POST /agent/message with unknown machine_id returns 404."""
    unknown_mid = str(uuid.uuid4())

    resp = client.post(
        "/api/remote/agent/message",
        json={"type": "register", "machine_id": unknown_mid},
    )

    assert resp.status_code == 404, f"expected 404, got {resp.status_code}: {resp.get_json()}"
    body = resp.get_json()
    assert body is not None
    assert "error" in body


def test_register_known_machine_returns_200(client, manager):
    """POST /agent/message with valid machine_id returns 200."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    valid_mid = str(uuid.uuid4())

    with manager.db.connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO remote_machines "
            "(machine_id, machine_name, status, last_heartbeat, created_at, updated_at) "
            "VALUES (?, ?, 'offline', ?, ?, ?)",
            (valid_mid, "valid-machine", now.isoformat(), now.isoformat(), now.isoformat()),
        )
        conn.commit()

    resp = client.post(
        "/api/remote/agent/message",
        json={"type": "register", "machine_id": valid_mid},
    )

    assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.get_json()}"
    body = resp.get_json()
    assert body is not None
    assert body.get("success") is True, f"status=200 but unexpected body: {body}"
    # Strengthened: pin the ack type the agent protocol expects.
    assert body.get("type") == "register_ack"
    # Strengthened: known machines ARE tracked — the flip side of the
    # trust boundary asserted in test_register_unknown_machine_not_in_connections.
    assert valid_mid in manager._connections


def test_register_unknown_machine_not_in_connections(client, manager):
    """Unknown machine_id is NOT added to _connections after rejected register."""
    fake_mid = str(uuid.uuid4())

    resp = client.post(
        "/api/remote/agent/message",
        json={"type": "register", "machine_id": fake_mid},
    )

    # The register must be rejected outright (unknown machine).
    assert resp.status_code == 404
    # Even after the request, _connections should not have it.
    assert fake_mid not in manager._connections, "fake machine_id was added to _connections!"
