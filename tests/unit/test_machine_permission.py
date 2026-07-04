#!/usr/bin/env python3
"""
Unit tests for machine-level admin permission control.

Tests the permission model:
  - System admin: all operations
  - Machine admin: assign/revoke users, view/stop others' sessions
  - Regular user: own sessions only
"""

import contextlib
import os
import sys
import tempfile

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

# Ensure encryption key is available for session tests
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-machine-permission-tests")

# Use a temp DB for isolation
TMP_DB = tempfile.mktemp(suffix=".db")

RESULTS = []


def test(name):
    RESULTS.append({"name": name, "passed": False, "detail": ""})
    print(f"\n--- TEST: {name} ---")
    return RESULTS[-1]


def ok(detail=""):
    RESULTS[-1]["passed"] = True
    RESULTS[-1]["detail"] = detail
    print(f"  [PASS] {RESULTS[-1]['name']}" + (f" - {detail}" if detail else ""))


def fail(detail=""):
    RESULTS[-1]["detail"] = detail
    print(f"  [FAIL] {RESULTS[-1]['name']} - {detail}")


def print_summary():
    passed = sum(1 for r in RESULTS if r["passed"])
    failed = sum(1 for r in RESULTS if not r["passed"])
    print(f"\n{'='*60}")
    print(f"  Results: {passed} passed, {failed} failed out of {len(RESULTS)}")
    print(f"{'='*60}")
    for r in RESULTS:
        status = "PASS" if r["passed"] else "FAIL"
        print(f"  [{status}] {r['name']}" + (f" - {r['detail']}" if r["detail"] else ""))
    return failed == 0


# ── Setup ──

_test_counter = 0


def make_manager():
    """Create a RemoteAgentManager with a fresh temp SQLite DB per test."""
    global _test_counter, TMP_DB
    _test_counter += 1
    # Clean previous temp DB
    with contextlib.suppress(OSError):
        os.unlink(TMP_DB)
    TMP_DB = tempfile.mktemp(suffix=".db")

    # Patch is_postgresql to return False for testing
    import app.repositories.database as db_mod

    original_is_pg = db_mod.is_postgresql
    original_db_path = db_mod.DB_PATH
    original_default_sqlite_path = db_mod.DEFAULT_SQLITE_PATH
    db_mod.is_postgresql = lambda: False
    db_mod.DB_PATH = TMP_DB
    db_mod.DEFAULT_SQLITE_PATH = TMP_DB  # get_sqlite_connection() uses this

    # Also patch the imported reference in remote_agent_manager
    import app.modules.workspace.remote_agent_manager as ram_compat

    ram_compat.is_postgresql = lambda: False

    # Patch session_manager's DB_PATH to use the test database
    import app.modules.workspace.session_manager as sm_compat

    sm_compat.DB_PATH = TMP_DB

    # Reset singleton so each test gets a fresh manager
    import app.modules.workspace.remote_agent_manager as ram_mod

    ram_mod._agent_manager = None

    from app.modules.workspace.remote_agent_manager import RemoteAgentManager

    mgr = RemoteAgentManager(db_path=TMP_DB)

    # Create required tables (SQLite-compatible DDL)
    import sqlite3

    conn = sqlite3.connect(TMP_DB)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS remote_machines ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "machine_id TEXT NOT NULL UNIQUE, "
        "machine_name TEXT, "
        "status TEXT DEFAULT 'offline', "
        "tenant_id INTEGER, "
        "last_heartbeat TIMESTAMP, "
        "created_at TIMESTAMP, "
        "updated_at TIMESTAMP)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS machine_assignments ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "machine_id TEXT NOT NULL, "
        "user_id INTEGER NOT NULL, "
        "permission TEXT NOT NULL DEFAULT 'user', "
        "granted_by INTEGER, "
        "granted_at TIMESTAMP)"
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
    # Agent identity tables (PR #754)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS registration_tokens ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "token_hash TEXT NOT NULL UNIQUE, "
        "tenant_id INTEGER NOT NULL, "
        "created_by INTEGER NOT NULL, "
        "created_at TIMESTAMP, "
        "expires_at TIMESTAMP, "
        "is_consumed INTEGER DEFAULT 0, "
        "consumed_at TIMESTAMP)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS agent_tokens ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "token_hash TEXT NOT NULL UNIQUE, "
        "machine_id TEXT NOT NULL, "
        "created_at TIMESTAMP, "
        "is_revoked INTEGER DEFAULT 0, "
        "revoked_at TIMESTAMP, "
        "revoked_by INTEGER, "
        "rotated_at TIMESTAMP)"
    )
    conn.commit()
    conn.close()

    db_mod.is_postgresql = original_is_pg
    db_mod.DB_PATH = original_db_path
    db_mod.DEFAULT_SQLITE_PATH = original_default_sqlite_path
    return mgr


def setup_test_data(mgr):
    """Create test machines and user assignments."""
    with mgr.db.connection() as conn:
        cursor = conn.cursor()

        now = "2026-01-01T00:00:00"

        # 3 machines
        for _i, name in enumerate(["machine-a", "machine-b", "machine-c"]):
            mid = f"mid-{name}"
            cursor.execute(
                "INSERT INTO remote_machines (machine_id, machine_name, status, tenant_id, created_at, updated_at) "
                "VALUES (?, ?, 'online', 1, ?, ?)",
                (mid, name, now, now),
            )

        # Create a users table for the LEFT JOIN in get_machine_assignments
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                username TEXT NOT NULL
            )
        """
        )
        for uid in [1, 2, 3, 4, 5, 99]:
            cursor.execute(
                "INSERT OR IGNORE INTO users (id, username) VALUES (?, ?)",
                (uid, f"user{uid}"),
            )

        # User 1: system admin (not tracked in machine_assignments for admin check)
        # User 2: machine admin on machine-a
        cursor.execute(
            "INSERT INTO machine_assignments (machine_id, user_id, permission, granted_by, granted_at) "
            "VALUES (?, ?, ?, 1, ?)",
            ("mid-machine-a", 2, "admin", now),
        )
        # User 2: regular user on machine-b
        cursor.execute(
            "INSERT INTO machine_assignments (machine_id, user_id, permission, granted_by, granted_at) "
            "VALUES (?, ?, ?, 1, ?)",
            ("mid-machine-b", 2, "user", now),
        )
        # User 3: regular user on machine-a
        cursor.execute(
            "INSERT INTO machine_assignments (machine_id, user_id, permission, granted_by, granted_at) "
            "VALUES (?, ?, ?, 1, ?)",
            ("mid-machine-a", 3, "user", now),
        )
        # User 4: machine admin on machine-b
        cursor.execute(
            "INSERT INTO machine_assignments (machine_id, user_id, permission, granted_by, granted_at) "
            "VALUES (?, ?, ?, 1, ?)",
            ("mid-machine-b", 4, "admin", now),
        )

        conn.commit()


# ════════════════════════════════════════════
#  Tests
# ════════════════════════════════════════════


def test_check_user_access_returns_permission():
    test("check_user_access returns permission string")
    mgr = make_manager()
    setup_test_data(mgr)

    # User 2 is admin on machine-a
    perm = mgr.check_user_access("mid-machine-a", 2)
    if perm == "admin":
        ok(f"permission={perm}")
    else:
        fail(f"expected 'admin', got {perm!r}")


def test_check_user_access_returns_none_for_unassigned():
    test("check_user_access returns None for unassigned user")
    mgr = make_manager()
    setup_test_data(mgr)

    perm = mgr.check_user_access("mid-machine-a", 99)
    if perm is None:
        ok()
    else:
        fail(f"expected None, got {perm!r}")


def test_check_user_access_returns_user_permission():
    test("check_user_access returns 'user' for regular user")
    mgr = make_manager()
    setup_test_data(mgr)

    perm = mgr.check_user_access("mid-machine-a", 3)
    if perm == "user":
        ok(f"permission={perm}")
    else:
        fail(f"expected 'user', got {perm!r}")


def test_get_user_permission():
    test("get_user_permission delegates to check_user_access")
    mgr = make_manager()
    setup_test_data(mgr)

    perm = mgr.get_user_permission("mid-machine-b", 2)
    if perm == "user":
        ok(f"permission={perm}")
    else:
        fail(f"expected 'user', got {perm!r}")


def test_list_machines_with_user_id_has_permission():
    test("list_machines with user_id attaches current_user_permission")
    mgr = make_manager()
    setup_test_data(mgr)

    machines = mgr.list_machines(user_id=2)
    # User 2 is assigned to machine-a (admin) and machine-b (user)
    perms = {m["machine_id"]: m.get("current_user_permission") for m in machines}

    if (
        perms.get("mid-machine-a") == "admin"
        and perms.get("mid-machine-b") == "user"
        and perms.get("mid-machine-c") is None
    ):
        ok(f"permissions={perms}")
    else:
        fail(f"unexpected permissions: {perms}")


def test_list_machines_without_user_id_no_permission():
    test("list_machines without user_id has no current_user_permission")
    mgr = make_manager()
    setup_test_data(mgr)

    machines = mgr.list_machines()
    for m in machines:
        if "current_user_permission" in m:
            fail(f"unexpected current_user_permission in {m['machine_id']}")
            return
    ok("no machine has current_user_permission")


def test_assign_user_as_machine_admin():
    test("assign_user works for machine admin")
    mgr = make_manager()
    setup_test_data(mgr)

    # User 2 (machine admin on machine-a) assigns user 5
    success = mgr.assign_user("mid-machine-a", 5, granted_by=2, permission="user")
    if success:
        perm = mgr.check_user_access("mid-machine-a", 5)
        if perm == "user":
            ok("user 5 assigned with 'user' permission")
        else:
            fail(f"expected 'user', got {perm!r}")
    else:
        fail("assign_user returned False")


def test_revoke_user_as_machine_admin():
    test("revoke_user works for regular user")
    mgr = make_manager()
    setup_test_data(mgr)

    # Revoke user 3 from machine-a
    success = mgr.revoke_user("mid-machine-a", 3)
    perm = mgr.check_user_access("mid-machine-a", 3)
    if success and perm is None:
        ok("user 3 revoked successfully")
    else:
        fail(f"success={success}, perm={perm!r}")


def test_revoke_admin_by_machine_admin():
    """Verify that the route-level logic prevents machine admin from revoking admin.
    We test the data layer here; route logic is tested separately."""
    test("revoke_user data layer allows revoking admin (route enforces)")
    mgr = make_manager()
    setup_test_data(mgr)

    # Data layer doesn't enforce permission check; the route does
    success = mgr.revoke_user("mid-machine-a", 2)  # user 2 is admin
    perm = mgr.check_user_access("mid-machine-a", 2)
    # Data layer should succeed - route enforces the restriction
    if success and perm is None:
        ok("data layer revokes admin (route-level enforcement prevents this)")
    else:
        fail(f"success={success}, perm={perm!r}")


def test_backwards_compat_truthiness():
    test("check_user_access result is truthy for assigned, falsy for unassigned")
    mgr = make_manager()
    setup_test_data(mgr)

    assigned = mgr.check_user_access("mid-machine-a", 2)
    unassigned = mgr.check_user_access("mid-machine-a", 99)

    if assigned and not unassigned:
        ok(f"assigned={assigned!r} (truthy), unassigned={unassigned!r} (falsy)")
    else:
        fail(f"assigned={assigned!r}, unassigned={unassigned!r}")


def test_permission_isolation_across_machines():
    test("user permissions are isolated per machine")
    mgr = make_manager()
    setup_test_data(mgr)

    # User 2 is admin on machine-a, user on machine-b, not on machine-c
    perm_a = mgr.check_user_access("mid-machine-a", 2)
    perm_b = mgr.check_user_access("mid-machine-b", 2)
    perm_c = mgr.check_user_access("mid-machine-c", 2)

    if perm_a == "admin" and perm_b == "user" and perm_c is None:
        ok(f"a={perm_a}, b={perm_b}, c={perm_c}")
    else:
        fail(f"a={perm_a}, b={perm_b}, c={perm_c}")


def test_assign_user_with_admin_permission():
    test("assign_user can assign admin permission (data layer)")
    mgr = make_manager()
    setup_test_data(mgr)

    success = mgr.assign_user("mid-machine-a", 5, granted_by=1, permission="admin")
    perm = mgr.check_user_access("mid-machine-a", 5)
    if success and perm == "admin":
        ok("user 5 assigned with 'admin' permission")
    else:
        fail(f"success={success}, perm={perm!r}")


# ════════════════════════════════════════════
#  Route-level permission tests (using Flask test client)
# ════════════════════════════════════════════


def _make_app(mgr):
    """Create a minimal Flask app with remote_bp for route testing."""
    import app.repositories.database as db_mod

    db_mod.is_postgresql = lambda: False
    db_mod.DB_PATH = TMP_DB

    from flask import Flask

    import app.modules.workspace.remote_agent_manager as ram_mod
    from app.routes import remote as remote_mod

    # Set the global singleton to our test manager
    ram_mod._agent_manager = mgr

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret"
    app.register_blueprint(remote_mod.remote_bp, url_prefix="/api/remote")

    # Mock _load_user_from_token to return user based on token prefix
    from app.auth import decorators as auth_dec

    _original_load_user = auth_dec._load_user_from_token

    def _mock_load_user(token):
        if not token:
            return None
        if token.startswith("test-token-"):
            parts = token.split("-")
            if len(parts) >= 4:
                return {
                    "id": int(parts[2]),
                    "username": f"user{parts[2]}",
                    "email": f"user{parts[2]}@test.com",
                    "role": parts[3],
                }
        return None

    auth_dec._load_user_from_token = _mock_load_user
    # Also patch the imported reference in remote module
    remote_mod._load_user_from_token = _mock_load_user
    app._auth_dec = auth_dec
    app._remote_mod = remote_mod
    app._original_load_user = _original_load_user

    return app


def _auth_get(client, url, token):
    client.set_cookie("session_token", token)
    return client.get(url)


def _auth_post(client, url, token, **kwargs):
    client.set_cookie("session_token", token)
    return client.post(url, **kwargs)


def _auth_delete(client, url, token, **kwargs):
    client.set_cookie("session_token", token)
    return client.delete(url, **kwargs)


def test_route_assign_by_system_admin():
    test("Route: system admin can assign user with admin permission")
    mgr = make_manager()
    setup_test_data(mgr)
    app = _make_app(mgr)

    with app.test_client() as client:
        resp = _auth_post(
            client,
            "/api/remote/machines/mid-machine-a/assign",
            "test-token-1-admin",
            json={"user_id": 5, "permission": "admin"},
        )
        if resp.status_code == 200:
            perm = mgr.check_user_access("mid-machine-a", 5)
            if perm == "admin":
                ok(f"status=200, permission={perm}")
            else:
                fail(f"permission={perm!r}, expected 'admin'")
        else:
            fail(f"status={resp.status_code}, body={resp.get_json()}")


def test_route_assign_by_machine_admin():
    test("Route: machine admin can assign user (forced to 'user')")
    mgr = make_manager()
    setup_test_data(mgr)
    app = _make_app(mgr)

    with app.test_client() as client:
        resp = _auth_post(
            client,
            "/api/remote/machines/mid-machine-a/assign",
            "test-token-2-user",
            json={"user_id": 5, "permission": "admin"},
        )
        if resp.status_code == 200:
            perm = mgr.check_user_access("mid-machine-a", 5)
            if perm == "user":
                ok(f"status=200, permission forced to '{perm}'")
            else:
                fail(f"permission={perm!r}, expected 'user' (forced)")
        else:
            fail(f"status={resp.status_code}, body={resp.get_json()}")


def test_route_assign_by_regular_user():
    test("Route: regular user cannot assign users")
    mgr = make_manager()
    setup_test_data(mgr)
    app = _make_app(mgr)

    with app.test_client() as client:
        resp = _auth_post(
            client,
            "/api/remote/machines/mid-machine-a/assign",
            "test-token-3-user",
            json={"user_id": 5, "permission": "user"},
        )
        if resp.status_code == 403:
            ok(f"status=403, body={resp.get_json()}")
        else:
            fail(f"expected 403, got {resp.status_code}")


def test_route_assign_by_unassigned_user():
    test("Route: unassigned user cannot assign users")
    mgr = make_manager()
    setup_test_data(mgr)
    app = _make_app(mgr)

    with app.test_client() as client:
        resp = _auth_post(
            client,
            "/api/remote/machines/mid-machine-a/assign",
            "test-token-99-user",
            json={"user_id": 5, "permission": "user"},
        )
        if resp.status_code == 403:
            ok("status=403")
        else:
            fail(f"expected 403, got {resp.status_code}")


def test_route_revoke_by_machine_admin():
    test("Route: machine admin can revoke regular user")
    mgr = make_manager()
    setup_test_data(mgr)
    app = _make_app(mgr)

    with app.test_client() as client:
        resp = _auth_delete(
            client, "/api/remote/machines/mid-machine-a/assign/3", "test-token-2-user"
        )
        if resp.status_code == 200:
            perm = mgr.check_user_access("mid-machine-a", 3)
            if perm is None:
                ok("user 3 revoked successfully")
            else:
                fail(f"user 3 still has permission: {perm!r}")
        else:
            fail(f"status={resp.status_code}, body={resp.get_json()}")


def test_route_revoke_admin_by_machine_admin():
    test("Route: machine admin cannot revoke admin user")
    mgr = make_manager()
    setup_test_data(mgr)
    app = _make_app(mgr)

    with app.test_client() as client:
        mgr.assign_user("mid-machine-a", 4, granted_by=1, permission="admin")
        resp = _auth_delete(
            client, "/api/remote/machines/mid-machine-a/assign/4", "test-token-2-user"
        )
        if resp.status_code == 403:
            ok(f"status=403, body={resp.get_json()}")
        else:
            fail(f"expected 403, got {resp.status_code}")


def test_route_revoke_admin_by_system_admin():
    test("Route: system admin can revoke admin user")
    mgr = make_manager()
    setup_test_data(mgr)
    app = _make_app(mgr)

    with app.test_client() as client:
        resp = _auth_delete(
            client, "/api/remote/machines/mid-machine-a/assign/2", "test-token-1-admin"
        )
        if resp.status_code == 200:
            perm = mgr.check_user_access("mid-machine-a", 2)
            if perm is None:
                ok("admin user revoked by system admin")
            else:
                fail(f"admin still has permission: {perm!r}")
        else:
            fail(f"status={resp.status_code}, body={resp.get_json()}")


def test_route_get_users_by_machine_admin():
    test("Route: machine admin can get machine users")
    mgr = make_manager()
    setup_test_data(mgr)
    app = _make_app(mgr)

    with app.test_client() as client:
        resp = _auth_get(client, "/api/remote/machines/mid-machine-a/users", "test-token-2-user")
        data = resp.get_json()
        if resp.status_code == 200 and len(data.get("users", [])) >= 1:
            ok(f"status=200, users={len(data['users'])}")
        else:
            fail(f"status={resp.status_code}, body={data}")


def test_route_get_users_by_regular_user():
    test("Route: regular user cannot get machine users")
    mgr = make_manager()
    setup_test_data(mgr)
    app = _make_app(mgr)

    with app.test_client() as client:
        resp = _auth_get(client, "/api/remote/machines/mid-machine-a/users", "test-token-3-user")
        if resp.status_code == 403:
            ok("status=403")
        else:
            fail(f"expected 403, got {resp.status_code}")


def test_route_list_machines_includes_permission():
    test("Route: list machines for non-admin includes current_user_permission")
    mgr = make_manager()
    setup_test_data(mgr)
    app = _make_app(mgr)

    with app.test_client() as client:
        resp = _auth_get(client, "/api/remote/machines", "test-token-2-user")
        data = resp.get_json()
        machines = data.get("machines", [])
        perms = {m["machine_id"]: m.get("current_user_permission") for m in machines}
        if perms.get("mid-machine-a") == "admin" and perms.get("mid-machine-b") == "user":
            ok(f"permissions={perms}")
        else:
            fail(f"unexpected permissions: {perms}")


def test_route_deregister_system_admin_only():
    test("Route: deregister machine is system admin only")
    mgr = make_manager()
    setup_test_data(mgr)
    app = _make_app(mgr)

    with app.test_client() as client:
        resp = _auth_delete(client, "/api/remote/machines/mid-machine-a", "test-token-2-user")
        if resp.status_code == 403:
            ok("machine admin gets 403")
        else:
            fail(f"expected 403, got {resp.status_code}")

    with app.test_client() as client:
        resp2 = _auth_delete(client, "/api/remote/machines/mid-machine-c", "test-token-1-admin")
        if resp2.status_code == 200:
            ok("system admin can deregister")
        else:
            fail(f"system admin got {resp2.status_code}")


def test_route_generate_token_system_admin_only():
    test("Route: generate token is system admin only")
    mgr = make_manager()
    setup_test_data(mgr)
    app = _make_app(mgr)

    with app.test_client() as client:
        resp = _auth_post(
            client, "/api/remote/machines/register", "test-token-2-user", json={"tenant_id": 1}
        )
        if resp.status_code == 403:
            ok("machine admin gets 403 for token generation")
        else:
            fail(f"expected 403, got {resp.status_code}")


def _create_session_for_test(mgr, user_id, machine_id):
    """Helper to create a remote session with patched singleton.

    Returns (result, session_mgr, patches) where patches is a dict of original
    values that must be restored after the test.
    """
    import app.modules.workspace.remote_agent_manager as ram_mod
    import app.modules.workspace.remote_session_manager as rsm_mod

    # Ensure singleton points to our test manager
    ram_mod._agent_manager = mgr

    # Simulate machine connection
    mgr._connections[machine_id] = True

    original_get = rsm_mod.get_remote_agent_manager
    original_rsm_get = rsm_mod.get_remote_session_manager
    rsm_mod.get_remote_agent_manager = lambda: mgr

    from unittest.mock import MagicMock

    from app.modules.workspace.remote_session_manager import RemoteSessionManager

    session_mgr = RemoteSessionManager()

    # Mock APIKeyProxyService to avoid needing api_key_store table
    mock_proxy = MagicMock()
    mock_proxy.generate_proxy_token.return_value = "test-proxy-token"
    mock_proxy.get_cli_settings_for_tool.return_value = None
    session_mgr._api_key_proxy = mock_proxy
    # Mock send_command to avoid needing actual remote connection
    mgr.send_command = MagicMock(return_value=True)

    # Patch route module to use our session manager (do NOT restore here!)
    import app.routes.remote as remote_mod

    original_remote_rsm = remote_mod.get_remote_session_manager
    remote_mod.get_remote_session_manager = lambda: session_mgr

    result = session_mgr.create_remote_session(
        user_id=user_id,
        machine_id=machine_id,
        project_path="/home/test",
        title="Test Session",
    )

    # Return patches to be restored by caller
    patches = {
        "rsm_mod.get_remote_agent_manager": (
            rsm_mod,
            "get_remote_agent_manager",
            original_get,
        ),
        "rsm_mod.get_remote_session_manager": (
            rsm_mod,
            "get_remote_session_manager",
            original_rsm_get,
        ),
        "remote_mod.get_remote_session_manager": (
            remote_mod,
            "get_remote_session_manager",
            original_remote_rsm,
        ),
    }

    return result, session_mgr, patches


def _restore_patches(patches):
    """Restore all patched values."""
    for name, (module, attr, original) in patches.items():
        setattr(module, attr, original)


def test_route_session_access_owner():
    test("Route: session owner can access own session")
    mgr = make_manager()
    setup_test_data(mgr)
    app = _make_app(mgr)

    with app.test_client() as client:
        result, session_mgr, patches = _create_session_for_test(mgr, 3, "mid-machine-a")
        if not result:
            fail("session creation failed")
            _restore_patches(patches)
            return

        sid = result["session_id"]
        resp = _auth_get(client, f"/api/remote/sessions/{sid}", "test-token-3-user")
        _restore_patches(patches)
        if resp.status_code == 200:
            ok("session owner can access session")
        else:
            fail(f"expected 200, got {resp.status_code}")


def test_route_session_access_machine_admin():
    test("Route: machine admin can access others' session")
    mgr = make_manager()
    setup_test_data(mgr)
    app = _make_app(mgr)

    with app.test_client() as client:
        result, session_mgr, patches = _create_session_for_test(mgr, 3, "mid-machine-a")
        if not result:
            fail("session creation failed")
            _restore_patches(patches)
            return

        sid = result["session_id"]
        resp = _auth_get(client, f"/api/remote/sessions/{sid}", "test-token-2-user")
        _restore_patches(patches)
        if resp.status_code == 200:
            ok("machine admin can access others' session")
        else:
            fail(f"expected 200, got {resp.status_code}, body={resp.get_json()}")


def test_route_session_access_denied_other_user():
    test("Route: regular user cannot access others' session")
    mgr = make_manager()
    setup_test_data(mgr)
    app = _make_app(mgr)

    with app.test_client() as client:
        result, session_mgr, patches = _create_session_for_test(mgr, 2, "mid-machine-a")
        if not result:
            fail("session creation failed")
            _restore_patches(patches)
            return

        sid = result["session_id"]
        resp = _auth_get(client, f"/api/remote/sessions/{sid}", "test-token-3-user")
        _restore_patches(patches)
        if resp.status_code == 403:
            ok("regular user gets 403 for others' session")
        else:
            fail(f"expected 403, got {resp.status_code}")


def test_route_session_access_unassigned_user():
    test("Route: unassigned user cannot access session")
    mgr = make_manager()
    setup_test_data(mgr)
    app = _make_app(mgr)

    with app.test_client() as client:
        result, session_mgr, patches = _create_session_for_test(mgr, 2, "mid-machine-a")
        if not result:
            fail("session creation failed")
            _restore_patches(patches)
            return

        sid = result["session_id"]
        resp = _auth_get(client, f"/api/remote/sessions/{sid}", "test-token-99-user")
        _restore_patches(patches)
        if resp.status_code == 403:
            ok("unassigned user gets 403")
        else:
            fail(f"expected 403, got {resp.status_code}")


# ════════════════════════════════════════════
#  P3-2: New tests for create_session and browse permissions
# ════════════════════════════════════════════


def test_route_create_session_by_unassigned_user():
    """P1-1: Unassigned users cannot create sessions (security fix)."""
    test("Route: unassigned user cannot create session")
    mgr = make_manager()
    setup_test_data(mgr)
    app = _make_app(mgr)

    # Simulate machine connection
    mgr._connections["mid-machine-a"] = True

    with app.test_client() as client:
        resp = _auth_post(
            client,
            "/api/remote/sessions",
            "test-token-99-user",  # Unassigned user
            json={"machine_id": "mid-machine-a", "project_path": "/home/test"},
        )
        if resp.status_code == 403:
            ok("unassigned user gets 403 for create_session")
        else:
            fail(f"expected 403, got {resp.status_code}, body={resp.get_json()}")


def test_route_create_session_by_assigned_user():
    """P1-1: Assigned users can create sessions."""
    test("Route: assigned user can create session")
    mgr = make_manager()
    setup_test_data(mgr)
    app = _make_app(mgr)

    # Simulate machine connection
    mgr._connections["mid-machine-a"] = True

    with app.test_client() as client:
        result, session_mgr, patches = _create_session_for_test(mgr, 3, "mid-machine-a")
        _restore_patches(patches)
        if result and result.get("session_id"):
            ok(f"assigned user created session: {result['session_id'][:8]}...")
        else:
            fail(f"session creation failed: {result}")


def test_route_create_session_by_machine_admin():
    """P1-1: Machine admins can create sessions."""
    test("Route: machine admin can create session")
    mgr = make_manager()
    setup_test_data(mgr)
    app = _make_app(mgr)

    # Simulate machine connection
    mgr._connections["mid-machine-a"] = True

    with app.test_client() as client:
        result, session_mgr, patches = _create_session_for_test(mgr, 2, "mid-machine-a")
        _restore_patches(patches)
        if result and result.get("session_id"):
            ok(f"machine admin created session: {result['session_id'][:8]}...")
        else:
            fail(f"session creation failed: {result}")


def test_route_browse_by_unassigned_user():
    """P1-1: Unassigned users cannot browse machine files."""
    test("Route: unassigned user cannot browse files")
    mgr = make_manager()
    setup_test_data(mgr)
    app = _make_app(mgr)

    with app.test_client() as client:
        resp = _auth_get(
            client,
            "/api/remote/machines/mid-machine-a/browse",
            "test-token-99-user",  # Unassigned user
        )
        if resp.status_code == 403:
            ok("unassigned user gets 403 for browse")
        else:
            fail(f"expected 403, got {resp.status_code}, body={resp.get_json()}")


def test_route_list_machines_returns_user_role():
    """P1-2: API returns explicit user_role field."""
    test("Route: list_machines returns user_role")
    mgr = make_manager()
    setup_test_data(mgr)
    app = _make_app(mgr)

    with app.test_client() as client:
        # System admin
        resp = _auth_get(client, "/api/remote/machines", "test-token-1-admin")
        data = resp.get_json()
        if data.get("user_role") == "admin":
            ok(f"system admin gets user_role='admin'")
        else:
            fail(f"expected user_role='admin', got {data.get('user_role')}")

        # Regular user
        resp2 = _auth_get(client, "/api/remote/machines", "test-token-2-user")
        data2 = resp2.get_json()
        if data2.get("user_role") == "user":
            ok(f"regular user gets user_role='user'")
        else:
            fail(f"expected user_role='user', got {data2.get('user_role')}")


# ════════════════════════════════════════════


def main():
    print("=" * 60)
    print("  Machine-Level Admin Permission Tests")
    print("=" * 60)

    # Data layer tests
    test_check_user_access_returns_permission()
    test_check_user_access_returns_none_for_unassigned()
    test_check_user_access_returns_user_permission()
    test_get_user_permission()
    test_list_machines_with_user_id_has_permission()
    test_list_machines_without_user_id_no_permission()
    test_assign_user_as_machine_admin()
    test_revoke_user_as_machine_admin()
    test_revoke_admin_by_machine_admin()
    test_backwards_compat_truthiness()
    test_permission_isolation_across_machines()
    test_assign_user_with_admin_permission()

    # Route-level tests
    test_route_assign_by_system_admin()
    test_route_assign_by_machine_admin()
    test_route_assign_by_regular_user()
    test_route_assign_by_unassigned_user()
    test_route_revoke_by_machine_admin()
    test_route_revoke_admin_by_machine_admin()
    test_route_revoke_admin_by_system_admin()
    test_route_get_users_by_machine_admin()
    test_route_get_users_by_regular_user()
    test_route_list_machines_includes_permission()
    test_route_deregister_system_admin_only()
    test_route_generate_token_system_admin_only()
    test_route_session_access_owner()
    test_route_session_access_machine_admin()
    test_route_session_access_denied_other_user()
    test_route_session_access_unassigned_user()

    # P3-2: New tests for create_session and browse permissions
    test_route_create_session_by_unassigned_user()
    test_route_create_session_by_assigned_user()
    test_route_create_session_by_machine_admin()
    test_route_browse_by_unassigned_user()
    test_route_list_machines_returns_user_role()

    all_passed = print_summary()

    # Cleanup
    with contextlib.suppress(OSError):
        os.unlink(TMP_DB)

    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
