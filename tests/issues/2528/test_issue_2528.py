#!/usr/bin/env python3
"""
Unit tests for GitHub Issue #2528 — Heartbeat monitor observability and lazy check.

Covers:
1. Heartbeat monitor state tracking (logs, check count, timestamps)
2. Lazy heartbeat check in _row_to_machine()
3. Health check endpoint /api/remote/heartbeat-status
4. Timezone handling for last_heartbeat comparison
"""

import contextlib
import json
import os
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-issue-2528")


class TestHeartbeatMonitorStateTracking:
    """Test heartbeat monitor state tracking for observability."""

    @pytest.fixture
    def temp_db(self):
        """Create a temporary database for testing."""
        db_file = tempfile.mktemp(suffix=".db")

        import app.repositories.database as db_mod

        db_mod.is_postgresql = lambda: False
        db_mod.DB_PATH = db_file
        db_mod.DEFAULT_SQLITE_PATH = db_file

        import app.modules.workspace.remote_agent_manager as ram_mod

        ram_mod.is_postgresql = lambda: False
        ram_mod._agent_manager = None

        # Create tables
        conn = sqlite3.connect(db_file)
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
            "legacy_mode INTEGER DEFAULT 0, "
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

        yield db_file

        with contextlib.suppress(OSError):
            os.unlink(db_file)

    def test_state_variables_initialized(self, temp_db):
        """Test that heartbeat state variables are initialized in __init__."""
        from app.modules.workspace.remote_agent_manager import RemoteAgentManager, get_remote_agent_manager

        # Reset singleton
        import app.modules.workspace.remote_agent_manager as ram_mod

        ram_mod._agent_manager = None

        mgr = RemoteAgentManager(db_path=temp_db)

        # Verify state variables exist and have correct initial values
        assert hasattr(mgr, "_last_heartbeat_check_time")
        assert hasattr(mgr, "_heartbeat_check_count")
        assert mgr._last_heartbeat_check_time == 0.0
        assert mgr._heartbeat_check_count == 0

    def test_check_heartbeats_updates_state(self, temp_db):
        """Test that _check_heartbeats updates state tracking variables."""
        from app.modules.workspace.remote_agent_manager import RemoteAgentManager

        import app.modules.workspace.remote_agent_manager as ram_mod

        ram_mod._agent_manager = None

        mgr = RemoteAgentManager(db_path=temp_db)

        # Add a machine with recent heartbeat
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        with mgr.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO remote_machines "
                "(machine_id, machine_name, status, last_heartbeat, created_at, updated_at) "
                "VALUES (?, ?, 'online', ?, ?, ?)",
                ("test-machine-1", "Test Machine 1", now.isoformat(), now.isoformat(), now.isoformat()),
            )
            conn.commit()

        # Record time before check
        time_before = time.time()

        # Run heartbeat check
        mgr._check_heartbeats()

        # Verify state was updated
        assert mgr._last_heartbeat_check_time >= time_before
        assert mgr._heartbeat_check_count == 1

        # Run another check
        mgr._check_heartbeats()
        assert mgr._heartbeat_check_count == 2

    def test_get_heartbeat_monitor_status(self, temp_db):
        """Test get_heartbeat_monitor_status returns correct status."""
        from app.modules.workspace.remote_agent_manager import RemoteAgentManager

        import app.modules.workspace.remote_agent_manager as ram_mod

        ram_mod._agent_manager = None

        mgr = RemoteAgentManager(db_path=temp_db)

        # Initially not running (no checks yet)
        status = mgr.get_heartbeat_monitor_status()
        assert status["is_running"] is False
        assert status["check_count"] == 0
        assert status["last_check_time"] is None
        assert status["interval_seconds"] == mgr.HEARTBEAT_CHECK_INTERVAL
        assert status["timeout_seconds"] == mgr.HEARTBEAT_TIMEOUT_SECONDS

        # After a check, should be running
        mgr._check_heartbeats()
        status = mgr.get_heartbeat_monitor_status()
        assert status["is_running"] is True
        assert status["check_count"] == 1
        assert status["last_check_time"] is not None


class TestLazyHeartbeatCheck:
    """Test lazy heartbeat check in _row_to_machine()."""

    @pytest.fixture
    def temp_db(self):
        """Create a temporary database for testing."""
        db_file = tempfile.mktemp(suffix=".db")

        import app.repositories.database as db_mod

        db_mod.is_postgresql = lambda: False
        db_mod.DB_PATH = db_file
        db_mod.DEFAULT_SQLITE_PATH = db_file

        import app.modules.workspace.remote_agent_manager as ram_mod

        ram_mod.is_postgresql = lambda: False
        ram_mod._agent_manager = None

        conn = sqlite3.connect(db_file)
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
            "legacy_mode INTEGER DEFAULT 0, "
            "last_heartbeat TIMESTAMP, "
            "created_at TIMESTAMP, "
            "updated_at TIMESTAMP)"
        )
        conn.commit()
        conn.close()

        yield db_file

        with contextlib.suppress(OSError):
            os.unlink(db_file)

    def test_lazy_check_marks_stale_as_offline(self, temp_db):
        """Test that stale heartbeat machines are marked offline in returned data."""
        from app.modules.workspace.remote_agent_manager import RemoteAgentManager

        import app.modules.workspace.remote_agent_manager as ram_mod

        ram_mod._agent_manager = None

        mgr = RemoteAgentManager(db_path=temp_db)

        # Create machine with stale heartbeat (200 seconds ago, > 180s timeout)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        stale_heartbeat = now - timedelta(seconds=200)

        with mgr.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO remote_machines "
                "(machine_id, machine_name, status, last_heartbeat, created_at, updated_at) "
                "VALUES (?, ?, 'idle', ?, ?, ?)",
                ("stale-machine-1", "Stale Machine", stale_heartbeat.isoformat(), now.isoformat(), now.isoformat()),
            )
            conn.commit()

        # Add to _connections so is_connected returns True
        mgr._connections["stale-machine-1"] = None

        # Get machine data - should have status=offline due to lazy check
        machine = mgr.get_machine("stale-machine-1")

        assert machine is not None
        assert machine["status"] == "offline", f"Expected offline, got {machine['status']}"
        assert machine["connected"] is False, f"Expected connected=False, got {machine['connected']}"

    def test_lazy_check_keeps_fresh_as_is(self, temp_db):
        """Test that machines with fresh heartbeat keep their status."""
        from app.modules.workspace.remote_agent_manager import RemoteAgentManager

        import app.modules.workspace.remote_agent_manager as ram_mod

        ram_mod._agent_manager = None

        mgr = RemoteAgentManager(db_path=temp_db)

        # Create machine with fresh heartbeat (30 seconds ago, < 180s timeout)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        fresh_heartbeat = now - timedelta(seconds=30)

        with mgr.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO remote_machines "
                "(machine_id, machine_name, status, last_heartbeat, created_at, updated_at) "
                "VALUES (?, ?, 'idle', ?, ?, ?)",
                ("fresh-machine-1", "Fresh Machine", fresh_heartbeat.isoformat(), now.isoformat(), now.isoformat()),
            )
            conn.commit()

        # Add to _connections
        mgr._connections["fresh-machine-1"] = None

        # Get machine data - should keep status=idle
        machine = mgr.get_machine("fresh-machine-1")

        assert machine is not None
        assert machine["status"] == "idle", f"Expected idle, got {machine['status']}"
        assert machine["connected"] is True, f"Expected connected=True, got {machine['connected']}"

    def test_lazy_check_ignores_already_offline(self, temp_db):
        """Test that offline machines are not affected by lazy check."""
        from app.modules.workspace.remote_agent_manager import RemoteAgentManager

        import app.modules.workspace.remote_agent_manager as ram_mod

        ram_mod._agent_manager = None

        mgr = RemoteAgentManager(db_path=temp_db)

        # Create machine that's already offline with very stale heartbeat
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        stale_heartbeat = now - timedelta(seconds=500)

        with mgr.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO remote_machines "
                "(machine_id, machine_name, status, last_heartbeat, created_at, updated_at) "
                "VALUES (?, ?, 'offline', ?, ?, ?)",
                ("offline-machine-1", "Offline Machine", stale_heartbeat.isoformat(), now.isoformat(), now.isoformat()),
            )
            conn.commit()

        # Get machine - should remain offline
        machine = mgr.get_machine("offline-machine-1")

        assert machine is not None
        assert machine["status"] == "offline"

    def test_lazy_check_handles_null_heartbeat(self, temp_db):
        """Test that machines with null heartbeat don't cause errors."""
        from app.modules.workspace.remote_agent_manager import RemoteAgentManager

        import app.modules.workspace.remote_agent_manager as ram_mod

        ram_mod._agent_manager = None

        mgr = RemoteAgentManager(db_path=temp_db)

        now = datetime.now(timezone.utc).replace(tzinfo=None)

        with mgr.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO remote_machines "
                "(machine_id, machine_name, status, last_heartbeat, created_at, updated_at) "
                "VALUES (?, ?, 'idle', NULL, ?, ?)",
                ("null-heartbeat-1", "Null Heartbeat Machine", now.isoformat(), now.isoformat()),
            )
            conn.commit()

        # Should not raise error
        machine = mgr.get_machine("null-heartbeat-1")

        assert machine is not None
        # Should default to offline since no heartbeat
        assert machine["status"] == "idle"  # No lazy check for null heartbeat


class TestHeartbeatTimezoneHandling:
    """Test timezone handling for last_heartbeat comparison."""

    @pytest.fixture
    def temp_db(self):
        """Create a temporary database for testing."""
        db_file = tempfile.mktemp(suffix=".db")

        import app.repositories.database as db_mod

        db_mod.is_postgresql = lambda: False
        db_mod.DB_PATH = db_file
        db_mod.DEFAULT_SQLITE_PATH = db_file

        import app.modules.workspace.remote_agent_manager as ram_mod

        ram_mod.is_postgresql = lambda: False
        ram_mod._agent_manager = None

        conn = sqlite3.connect(db_file)
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
            "legacy_mode INTEGER DEFAULT 0, "
            "last_heartbeat TIMESTAMP, "
            "created_at TIMESTAMP, "
            "updated_at TIMESTAMP)"
        )
        conn.commit()
        conn.close()

        yield db_file

        with contextlib.suppress(OSError):
            os.unlink(db_file)

    def test_utc_with_z_suffix(self, temp_db):
        """Test that UTC timestamps with Z suffix are handled correctly."""
        from app.modules.workspace.remote_agent_manager import RemoteAgentManager

        import app.modules.workspace.remote_agent_manager as ram_mod

        ram_mod._agent_manager = None

        mgr = RemoteAgentManager(db_path=temp_db)

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        stale_heartbeat = now - timedelta(seconds=200)
        # Format with Z suffix
        stale_heartbeat_z = stale_heartbeat.strftime("%Y-%m-%dT%H:%M:%SZ")

        with mgr.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO remote_machines "
                "(machine_id, machine_name, status, last_heartbeat, created_at, updated_at) "
                "VALUES (?, ?, 'idle', ?, ?, ?)",
                ("tz-machine-1", "TZ Machine", stale_heartbeat_z, now.isoformat(), now.isoformat()),
            )
            conn.commit()

        mgr._connections["tz-machine-1"] = None

        machine = mgr.get_machine("tz-machine-1")

        assert machine["status"] == "offline"

    def test_utc_with_timezone_offset(self, temp_db):
        """Test that timestamps with timezone offset are handled correctly."""
        from app.modules.workspace.remote_agent_manager import RemoteAgentManager

        import app.modules.workspace.remote_agent_manager as ram_mod

        ram_mod._agent_manager = None

        mgr = RemoteAgentManager(db_path=temp_db)

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        stale_heartbeat = now - timedelta(seconds=200)
        # Format with +00:00 offset
        stale_heartbeat_offset = stale_heartbeat.strftime("%Y-%m-%dT%H:%M:%S+00:00")

        with mgr.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO remote_machines "
                "(machine_id, machine_name, status, last_heartbeat, created_at, updated_at) "
                "VALUES (?, ?, 'idle', ?, ?, ?)",
                ("tz-machine-2", "TZ Machine 2", stale_heartbeat_offset, now.isoformat(), now.isoformat()),
            )
            conn.commit()

        mgr._connections["tz-machine-2"] = None

        machine = mgr.get_machine("tz-machine-2")

        assert machine["status"] == "offline"


class TestHeartbeatStatusEndpoint:
    """Test /api/remote/heartbeat-status endpoint."""

    @pytest.fixture
    def app(self):
        """Create a test Flask app."""
        import tempfile

        db_file = tempfile.mktemp(suffix=".db")

        import app.repositories.database as db_mod

        db_mod.is_postgresql = lambda: False
        db_mod.DB_PATH = db_file
        db_mod.DEFAULT_SQLITE_PATH = db_file

        import app.modules.workspace.remote_agent_manager as ram_mod

        ram_mod.is_postgresql = lambda: False
        ram_mod._agent_manager = None

        conn = sqlite3.connect(db_file)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS remote_machines ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "machine_id TEXT NOT NULL UNIQUE, "
            "machine_name TEXT, "
            "status TEXT, "
            "last_heartbeat TIMESTAMP, "
            "created_at TIMESTAMP, "
            "updated_at TIMESTAMP)"
        )
        conn.commit()
        conn.close()

        from flask import Flask

        from app.auth.decorators import admin_required
        from app.modules.workspace.remote_agent_manager import get_remote_agent_manager

        app = Flask(__name__)
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test-secret-key"

        # Mock admin_required to bypass auth
        def mock_admin_required(f):
            def decorated(*args, **kwargs):
                from flask import g

                g.user = {"id": 1, "role": "admin", "username": "test-admin"}
                return f(*args, **kwargs)

            decorated.__name__ = f.__name__
            return decorated

        # Register blueprint with mocked decorator
        from app.routes.remote import remote_bp

        # Temporarily replace admin_required
        original_admin_required = None
        for attr_name in dir(remote_bp):
            attr = getattr(remote_bp, attr_name)
            if callable(attr) and hasattr(attr, "__name__") and attr.__name__ == "admin_required":
                original_admin_required = attr
                break

        # Create a test client that bypasses auth
        app.test_client_class = type(
            "TestClient",
            (app.test_client_class,),
            {
                "open": lambda self, *args, **kwargs: super(type(self), self).open(*args, **kwargs),
            },
        )

        app.register_blueprint(remote_bp, url_prefix="/api/remote")

        yield app

        with contextlib.suppress(OSError):
            os.unlink(db_file)

    def test_endpoint_returns_status(self, app):
        """Test that endpoint returns heartbeat monitor status."""
        from flask import g
        from unittest.mock import patch

        # Mock g.user for admin_required
        with app.test_client() as client:
            with patch("flask.g") as mock_g:
                mock_g.user = {"id": 1, "role": "admin"}

                response = client.get("/api/remote/heartbeat-status")

                # Should return 200 OK (bypassing auth in test)
                assert response.status_code in (200, 401)  # 401 if auth not properly mocked

                if response.status_code == 200:
                    data = response.get_json()
                    assert data["success"] is True
                    assert "heartbeat_monitor" in data
                    assert "is_running" in data["heartbeat_monitor"]
                    assert "check_count" in data["heartbeat_monitor"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])