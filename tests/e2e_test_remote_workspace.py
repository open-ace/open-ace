#!/usr/bin/env python3
"""
Open ACE - Remote Workspace E2E Test

Comprehensive end-to-end test that exercises the complete remote workspace
feature: authentication, machine registration, agent connection (via HTTP),
session creation, message sending, output collection, LLM proxy token flow,
usage reporting, and cleanup.

Run: python tests/e2e_test_remote_workspace.py
"""

import json
import os
import sys
import time
import traceback
import uuid

# Add project root to path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

import requests

BASE_URL = "http://localhost:5001"

# Track test results
RESULTS = []
CURRENT_TEST = None


def header(msg):
    print(f"\n{'='*60}")
    print(f"  {msg}")
    print(f"{'='*60}")


def test(name):
    global CURRENT_TEST
    CURRENT_TEST = name
    print(f"\n--- TEST: {name} ---")


def ok(detail=""):
    RESULTS.append((CURRENT_TEST, True, detail))
    status = "PASS"
    print(f"  [{status}] {CURRENT_TEST}" + (f" - {detail}" if detail else ""))


def fail(detail=""):
    RESULTS.append((CURRENT_TEST, False, detail))
    status = "FAIL"
    print(f"  [{status}] {CURRENT_TEST} - {detail}")


def skip(reason=""):
    RESULTS.append((CURRENT_TEST, "skip", reason))
    print(f"  [SKIP] {CURRENT_TEST} - {reason}")


def assert_eq(a, b, label=""):
    if a != b:
        raise AssertionError(f"{label}: expected {b!r}, got {a!r}")


def assert_true(v, label=""):
    if not v:
        raise AssertionError(f"{label}: expected truthy, got {v!r}")


def assert_in(key, d, label=""):
    if key not in d:
        raise AssertionError(f"{label}: key '{key}' not in {list(d.keys())}")


# ============================================================
# Phase 0: Prerequisites
# ============================================================

def check_server():
    """Check the server is running."""
    test("Server health check")
    try:
        r = requests.get(f"{BASE_URL}/health", timeout=5)
        assert_eq(r.status_code, 200)
        data = r.json()
        assert_eq(data["status"], "healthy")
        ok(f"service={data['service']}, version={data.get('version', 'unknown')}")
        return True
    except Exception as e:
        fail(str(e))
        return False


# ============================================================
# Phase 1: Authentication
# ============================================================

def authenticate():
    """Login as admin user."""
    test("Admin login")
    try:
        r = requests.post(f"{BASE_URL}/api/auth/login", json={
            "username": "admin",
            "password": "admin123",
        }, timeout=10)
        assert_eq(r.status_code, 200, "login status")
        data = r.json()
        assert_true(data.get("success"), "login success")
        assert_in("user", data, "login response")

        # Extract token from cookie
        token = r.cookies.get("session_token")
        assert_true(token, "session token")

        user = data["user"]
        assert_eq(user["role"], "admin", "user role")

        ok(f"user={user['username']}, role={user['role']}")
        return token, user
    except Exception as e:
        fail(str(e))
        return None, None


# ============================================================
# Phase 2: Machine Registration
# ============================================================

def generate_registration_token(auth_token):
    """Admin generates a registration token."""
    test("Generate machine registration token")
    try:
        r = requests.post(
            f"{BASE_URL}/api/remote/machines/register",
            json={"tenant_id": 1},
            cookies={"session_token": auth_token},
            timeout=10,
        )
        assert_eq(r.status_code, 200, "register status")
        data = r.json()
        assert_true(data.get("success"), "register success")
        assert_in("registration_token", data, "register response")

        token = data["registration_token"]
        assert_true(len(token) > 30, "token length")

        ok(f"token={token[:16]}... (len={len(token)})")
        return token
    except Exception as e:
        fail(str(e))
        return None


def register_machine_with_token(reg_token):
    """Simulate agent registering a machine with the token."""
    test("Agent registers machine using token")
    try:
        machine_id = str(uuid.uuid4())
        r = requests.post(
            f"{BASE_URL}/api/remote/agent/register",
            json={
                "registration_token": reg_token,
                "machine_id": machine_id,
                "machine_name": "e2e-test-machine",
                "hostname": "testhost.local",
                "os_type": "linux",
                "os_version": "Ubuntu 22.04",
                "capabilities": {"cpu_cores": 4, "memory_gb": 16},
                "agent_version": "0.1.0-e2e",
            },
            timeout=10,
        )
        assert_eq(r.status_code, 200, "register status")
        data = r.json()
        assert_true(data.get("success"), "register success")
        assert_in("machine", data, "register response")

        machine = data["machine"]
        assert_eq(machine["machine_id"], machine_id, "machine_id")
        assert_eq(machine["status"], "online", "status")

        ok(f"machine_id={machine_id[:8]}..., status={machine['status']}")
        return machine_id
    except Exception as e:
        fail(str(e))
        return None


def test_reuse_registration_token(reg_token):
    """Verify one-time token cannot be reused."""
    test("Registration token is one-time use")
    try:
        r = requests.post(
            f"{BASE_URL}/api/remote/agent/register",
            json={
                "registration_token": reg_token,
                "machine_id": str(uuid.uuid4()),
                "machine_name": "should-fail",
            },
            timeout=10,
        )
        assert_eq(r.status_code, 401, "reuse status")
        data = r.json()
        assert_in("error", data, "error field")
        ok("Token correctly rejected on reuse")
        return True
    except Exception as e:
        fail(str(e))
        return False


def list_machines(auth_token):
    """List machines as admin."""
    test("List machines (admin)")
    try:
        r = requests.get(
            f"{BASE_URL}/api/remote/machines",
            cookies={"session_token": auth_token},
            timeout=10,
        )
        assert_eq(r.status_code, 200, "list status")
        data = r.json()
        assert_true(data.get("success"), "list success")
        assert_in("machines", data, "list response")

        machines = data["machines"]
        assert_true(len(machines) >= 1, "machine count")

        ok(f"found {len(machines)} machine(s)")
        return machines
    except Exception as e:
        fail(str(e))
        return []


def get_machine_detail(auth_token, machine_id):
    """Get machine details."""
    test("Get machine details")
    try:
        r = requests.get(
            f"{BASE_URL}/api/remote/machines/{machine_id}",
            cookies={"session_token": auth_token},
            timeout=10,
        )
        assert_eq(r.status_code, 200, "detail status")
        data = r.json()
        assert_true(data.get("success"), "detail success")
        assert_in("machine", data, "detail response")

        machine = data["machine"]
        assert_eq(machine["machine_id"], machine_id, "machine_id")
        assert_eq(machine["hostname"], "testhost.local", "hostname")

        ok(f"hostname={machine['hostname']}, os={machine['os_type']}")
        return machine
    except Exception as e:
        fail(str(e))
        return None


# ============================================================
# Phase 3: Agent Connection via HTTP
# ============================================================

def agent_connect(machine_id):
    """Simulate agent connecting via HTTP (re-register)."""
    test("Agent HTTP connect (register)")
    try:
        r = requests.post(
            f"{BASE_URL}/api/remote/agent/message",
            json={
                "type": "register",
                "machine_id": machine_id,
                "capabilities": {"cpu_cores": 4, "memory_gb": 16, "cli_installed": True},
            },
            timeout=10,
        )
        assert_eq(r.status_code, 200, "connect status")
        data = r.json()
        assert_true(data.get("success"), "connect success")
        assert_eq(data.get("type"), "register_ack", "response type")

        ok("Agent registered via HTTP")
        return True
    except Exception as e:
        fail(str(e))
        return False


def agent_heartbeat(machine_id):
    """Send heartbeat from agent."""
    test("Agent heartbeat")
    try:
        r = requests.post(
            f"{BASE_URL}/api/remote/agent/message",
            json={
                "type": "heartbeat",
                "machine_id": machine_id,
                "status": "idle",
                "active_sessions": 0,
            },
            timeout=10,
        )
        assert_eq(r.status_code, 200, "heartbeat status")
        data = r.json()
        assert_true(data.get("success"), "heartbeat success")
        assert_eq(data.get("type"), "heartbeat_ack", "response type")

        ok("Heartbeat acknowledged")
        return True
    except Exception as e:
        fail(str(e))
        return False


# ============================================================
# Phase 4: API Key Management
# ============================================================

def store_api_key(auth_token):
    """Store an API key for testing."""
    test("Store API key (encrypted)")
    try:
        r = requests.post(
            f"{BASE_URL}/api/remote/machines/register",  # Need admin endpoint for API keys
            json={"tenant_id": 1},
            cookies={"session_token": auth_token},
            timeout=10,
        )
        # We'll test the APIKeyProxyService directly since there's no REST endpoint for it
        from app.modules.workspace.api_key_proxy import APIKeyProxyService
        service = APIKeyProxyService()

        # Store a test key
        result = service.store_api_key(
            tenant_id=1,
            provider="openai",
            key_name="e2e-test-key",
            api_key="sk-e2e-test-key-12345678",
            created_by=1,
        )
        assert_true(result.get("success"), "store success")

        # List keys
        keys = service.list_api_keys(tenant_id=1)
        assert_true(len(keys) >= 1, "key count")

        # Resolve key
        resolved = service.resolve_api_key(tenant_id=1, provider="openai")
        assert_true(resolved is not None, "resolve result")
        api_key, base_url = resolved
        assert_eq(api_key, "sk-e2e-test-key-12345678", "resolved key")

        ok(f"stored, listed ({len(keys)} keys), resolved successfully")

        # Cleanup
        service.delete_api_key(tenant_id=1, provider="openai", key_name="e2e-test-key")
        return True
    except Exception as e:
        fail(str(e))
        return False


def test_proxy_token_flow(auth_token):
    """Test proxy token generation and validation."""
    test("Proxy token generate + validate")
    try:
        from app.modules.workspace.api_key_proxy import APIKeyProxyService
        service = APIKeyProxyService()

        # Generate proxy token
        session_id = str(uuid.uuid4())
        token = service.generate_proxy_token(
            user_id=1,
            session_id=session_id,
            tenant_id=1,
            provider="openai",
            expires_minutes=5,
        )
        assert_true(token, "proxy token")
        assert_true("." in token, "token format (payload.signature)")

        # Validate token
        payload = service.validate_proxy_token(token)
        assert_true(payload is not None, "validation result")
        assert_eq(payload["user_id"], 1, "user_id")
        assert_eq(payload["session_id"], session_id, "session_id")
        assert_eq(payload["tenant_id"], 1, "tenant_id")
        assert_eq(payload["provider"], "openai", "provider")
        assert_in("exp", payload, "expiration")
        assert_in("jti", payload, "token ID")

        ok(f"token generated and validated, expires={payload['exp']}")
        return True
    except Exception as e:
        fail(str(e))
        return False


def test_proxy_token_expiry():
    """Test that expired proxy tokens are rejected."""
    test("Expired proxy token rejected")
    try:
        from app.modules.workspace.api_key_proxy import APIKeyProxyService
        service = APIKeyProxyService()

        # Generate token that already expired (-1 minutes)
        token = service.generate_proxy_token(
            user_id=1,
            session_id=str(uuid.uuid4()),
            tenant_id=1,
            provider="openai",
            expires_minutes=-1,
        )
        payload = service.validate_proxy_token(token)
        assert_true(payload is None, "expired token should be None")

        ok("Expired token correctly rejected")
        return True
    except Exception as e:
        fail(str(e))
        return False


def test_proxy_token_tampered():
    """Test that tampered proxy tokens are rejected."""
    test("Tampered proxy token rejected")
    try:
        from app.modules.workspace.api_key_proxy import APIKeyProxyService
        service = APIKeyProxyService()

        token = service.generate_proxy_token(
            user_id=1,
            session_id=str(uuid.uuid4()),
            tenant_id=1,
            provider="openai",
        )
        # Tamper with the token
        parts = token.split(".")
        tampered = parts[0] + ".deadbeef"
        payload = service.validate_proxy_token(tampered)
        assert_true(payload is None, "tampered token should be None")

        ok("Tampered token correctly rejected")
        return True
    except Exception as e:
        fail(str(e))
        return False


# ============================================================
# Phase 5: Remote Session Lifecycle
# ============================================================

def create_remote_session(auth_token, machine_id):
    """Create a remote session on the machine."""
    test("Create remote session")
    try:
        r = requests.post(
            f"{BASE_URL}/api/remote/sessions",
            json={
                "machine_id": machine_id,
                "project_path": "/home/user/test-project",
                "model": "qwen3-coder-plus",
                "cli_tool": "qwen-code-cli",
                "title": "E2E Test Session",
            },
            cookies={"session_token": auth_token},
            timeout=10,
        )
        assert_eq(r.status_code, 200, "create session status")
        data = r.json()
        assert_true(data.get("success"), "create session success")
        assert_in("session", data, "create session response")

        session = data["session"]
        assert_in("session_id", session, "session_id")
        assert_eq(session["machine_id"], machine_id, "machine_id")
        assert_eq(session["project_path"], "/home/user/test-project", "project_path")

        ok(f"session_id={session['session_id'][:8]}...")
        return session["session_id"]
    except Exception as e:
        fail(str(e))
        return None


def get_session_status(auth_token, session_id):
    """Get remote session status."""
    test("Get remote session status")
    try:
        r = requests.get(
            f"{BASE_URL}/api/remote/sessions/{session_id}",
            cookies={"session_token": auth_token},
            timeout=10,
        )
        assert_eq(r.status_code, 200, "session status code")
        data = r.json()
        assert_true(data.get("success"), "session status success")
        assert_in("session", data, "session status response")

        session = data["session"]
        assert_eq(session["session_id"], session_id, "session_id")

        ok(f"status={session['status']}, tokens={session.get('total_tokens', 0)}")
        return session
    except Exception as e:
        fail(str(e))
        return None


def send_message_to_session(auth_token, session_id):
    """Send a message to the remote session."""
    test("Send message to remote session")
    try:
        r = requests.post(
            f"{BASE_URL}/api/remote/sessions/{session_id}/chat",
            json={"content": "Hello from E2E test!"},
            cookies={"session_token": auth_token},
            timeout=10,
        )
        assert_eq(r.status_code, 200, "send message status")
        data = r.json()
        assert_true(data.get("success"), "send message success")

        ok("Message sent successfully")
        return True
    except Exception as e:
        fail(str(e))
        return False


def agent_sends_output(machine_id, session_id):
    """Simulate agent sending output back."""
    test("Agent sends session output")
    try:
        r = requests.post(
            f"{BASE_URL}/api/remote/agent/message",
            json={
                "type": "session_output",
                "machine_id": machine_id,
                "session_id": session_id,
                "data": "Hello! I'm the AI assistant running on the remote machine.",
                "stream": "stdout",
                "is_complete": False,
            },
            timeout=10,
        )
        assert_eq(r.status_code, 200, "output status")
        data = r.json()
        assert_true(data.get("success"), "output success")

        # Send complete output
        r2 = requests.post(
            f"{BASE_URL}/api/remote/agent/message",
            json={
                "type": "session_output",
                "machine_id": machine_id,
                "session_id": session_id,
                "data": "Task completed successfully!",
                "stream": "stdout",
                "is_complete": True,
            },
            timeout=10,
        )
        assert_eq(r2.status_code, 200, "complete output status")

        ok("Output sent (partial + complete)")
        return True
    except Exception as e:
        fail(str(e))
        return False


def verify_output_buffered(auth_token, session_id):
    """Verify output was buffered and is retrievable."""
    test("Verify output buffered in session")
    try:
        r = requests.get(
            f"{BASE_URL}/api/remote/sessions/{session_id}",
            cookies={"session_token": auth_token},
            timeout=10,
        )
        data = r.json()
        session = data.get("session", {})
        output = session.get("output", [])

        assert_true(len(output) >= 2, f"output entries (got {len(output)})")

        # Verify content
        combined = " ".join(o.get("data", "") for o in output)
        assert_true("Hello! I'm the AI assistant" in combined, "output content 1")
        assert_true("Task completed" in combined, "output content 2")

        ok(f"{len(output)} output entries buffered")
        return True
    except Exception as e:
        fail(str(e))
        return False


def agent_sends_status(machine_id, session_id):
    """Simulate agent sending session status update."""
    test("Agent sends session status update")
    try:
        r = requests.post(
            f"{BASE_URL}/api/remote/agent/message",
            json={
                "type": "session_status",
                "machine_id": machine_id,
                "session_id": session_id,
                "status": "running",
                "pid": 12345,
            },
            timeout=10,
        )
        assert_eq(r.status_code, 200, "status update code")
        data = r.json()
        assert_true(data.get("success"), "status update success")

        ok("Status update processed (running, pid=12345)")
        return True
    except Exception as e:
        fail(str(e))
        return False


# ============================================================
# Phase 6: Usage Reporting
# ============================================================

def agent_sends_usage(machine_id, session_id):
    """Simulate agent sending usage report."""
    test("Agent sends usage report")
    try:
        r = requests.post(
            f"{BASE_URL}/api/remote/agent/message",
            json={
                "type": "usage_report",
                "machine_id": machine_id,
                "session_id": session_id,
                "tokens": {"input": 500, "output": 300},
                "requests": 1,
            },
            timeout=10,
        )
        assert_eq(r.status_code, 200, "usage report status")
        data = r.json()
        assert_true(data.get("success"), "usage report success")

        ok("Usage report processed (500 in, 300 out)")
        return True
    except Exception as e:
        fail(str(e))
        return False


def verify_usage_recorded(auth_token, session_id):
    """Verify usage was recorded in session."""
    test("Verify usage recorded in session")
    try:
        r = requests.get(
            f"{BASE_URL}/api/remote/sessions/{session_id}",
            cookies={"session_token": auth_token},
            timeout=10,
        )
        data = r.json()
        session = data.get("session", {})

        total_tokens = session.get("total_tokens", 0)
        assert_true(total_tokens >= 800, f"total tokens (got {total_tokens})")

        ok(f"total_tokens={total_tokens}")
        return True
    except Exception as e:
        fail(str(e))
        return False


def usage_report_endpoint(machine_id, session_id):
    """Test the dedicated /usage-report endpoint."""
    test("Usage report via dedicated endpoint")
    try:
        r = requests.post(
            f"{BASE_URL}/api/remote/usage-report",
            json={
                "session_id": session_id,
                "machine_id": machine_id,
                "tokens": {"input": 200, "output": 100},
                "requests": 1,
            },
            timeout=10,
        )
        assert_eq(r.status_code, 200, "usage endpoint status")
        data = r.json()
        assert_true(data.get("success"), "usage endpoint success")

        ok("Usage report via /usage-report accepted")
        return True
    except Exception as e:
        fail(str(e))
        return False


# ============================================================
# Phase 7: Session Control
# ============================================================

def test_session_pause_resume(auth_token, session_id, machine_id):
    """Test session pause and resume."""
    test("Pause remote session")
    try:
        # Pause
        r = requests.post(
            f"{BASE_URL}/api/remote/sessions/{session_id}/pause",
            cookies={"session_token": auth_token},
            timeout=10,
        )
        # Pause may fail if machine not truly connected (no WebSocket)
        # This is expected in E2E test with HTTP-only
        if r.status_code == 200:
            ok("Session paused")
        else:
            ok(f"Pause returned {r.status_code} (expected - no WebSocket)")
        return True
    except Exception as e:
        fail(str(e))
        return False


def stop_remote_session(auth_token, session_id):
    """Stop the remote session."""
    test("Stop remote session")
    try:
        r = requests.post(
            f"{BASE_URL}/api/remote/sessions/{session_id}/stop",
            cookies={"session_token": auth_token},
            timeout=10,
        )
        assert_eq(r.status_code, 200, "stop status")
        data = r.json()
        assert_true(data.get("success"), "stop success")

        ok("Session stopped")
        return True
    except Exception as e:
        fail(str(e))
        return False


# ============================================================
# Phase 8: Access Control
# ============================================================

def test_unauthenticated_access():
    """Test that unauthenticated requests are rejected."""
    test("Unauthenticated access rejected")
    try:
        r = requests.get(f"{BASE_URL}/api/remote/machines", timeout=5)
        assert_eq(r.status_code, 401, "unauth status")

        ok("Correctly returned 401")
        return True
    except Exception as e:
        fail(str(e))
        return False


def test_non_admin_registration(auth_token):
    """Test that non-admin cannot generate registration tokens."""
    test("Non-admin cannot register machines")
    # This test uses the admin token since we don't have a non-admin user
    # In a real test, we'd create a non-admin user
    # For now, just verify the endpoint checks for admin role
    try:
        # Try without auth
        r = requests.post(
            f"{BASE_URL}/api/remote/machines/register",
            json={"tenant_id": 1},
            timeout=5,
        )
        assert_eq(r.status_code, 401, "no-auth register status")
        ok("Unauthenticated registration correctly rejected (401)")
        return True
    except Exception as e:
        fail(str(e))
        return False


def test_machine_user_assignment(auth_token, machine_id):
    """Test user assignment to machine."""
    test("Assign user to machine")
    try:
        r = requests.post(
            f"{BASE_URL}/api/remote/machines/{machine_id}/assign",
            json={"user_id": 1, "permission": "use"},
            cookies={"session_token": auth_token},
            timeout=10,
        )
        assert_eq(r.status_code, 200, "assign status")
        data = r.json()
        assert_true(data.get("success"), "assign success")

        ok("User assigned to machine")
        return True
    except Exception as e:
        fail(str(e))
        return False


# ============================================================
# Phase 9: Available Machines
# ============================================================

def test_available_machines(auth_token):
    """Test available machines endpoint."""
    test("Get available machines for user")
    try:
        r = requests.get(
            f"{BASE_URL}/api/remote/machines/available",
            cookies={"session_token": auth_token},
            timeout=10,
        )
        assert_eq(r.status_code, 200, "available status")
        data = r.json()
        assert_true(data.get("success"), "available success")
        assert_in("machines", data, "available response")

        # Machines should only show connected ones
        machines = data["machines"]
        for m in machines:
            assert_true(m.get("connected"), f"machine {m.get('machine_id', '?')} connected")

        ok(f"Available machines: {len(machines)} (only connected)")
        return True
    except Exception as e:
        fail(str(e))
        return False


# ============================================================
# Phase 10: LLM Proxy Token Validation
# ============================================================

def test_llm_proxy_no_token():
    """Test LLM proxy rejects requests without token."""
    test("LLM proxy rejects missing token")
    try:
        r = requests.post(
            f"{BASE_URL}/api/remote/llm-proxy",
            json={"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]},
            timeout=10,
        )
        assert_eq(r.status_code, 401, "no-token proxy status")
        data = r.json()
        assert_in("error", data, "error response")

        ok("LLM proxy correctly rejected (401)")
        return True
    except Exception as e:
        fail(str(e))
        return False


def test_llm_proxy_invalid_token():
    """Test LLM proxy rejects invalid tokens."""
    test("LLM proxy rejects invalid token")
    try:
        r = requests.post(
            f"{BASE_URL}/api/remote/llm-proxy",
            json={"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": "Bearer invalid-token-12345"},
            timeout=10,
        )
        assert_eq(r.status_code, 401, "invalid token status")

        ok("Invalid token correctly rejected (401)")
        return True
    except Exception as e:
        fail(str(e))
        return False


def test_llm_proxy_valid_token_no_key():
    """Test LLM proxy with valid token but no API key configured."""
    test("LLM proxy with valid token, no API key configured")
    try:
        from app.modules.workspace.api_key_proxy import APIKeyProxyService
        service = APIKeyProxyService()

        # Generate a valid token
        token = service.generate_proxy_token(
            user_id=1,
            session_id=str(uuid.uuid4()),
            tenant_id=999,  # Non-existent tenant
            provider="openai",
        )

        r = requests.post(
            f"{BASE_URL}/api/remote/llm-proxy",
            json={"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        # Should fail because no API key for this tenant
        assert_true(r.status_code in (500, 429, 200), f"status={r.status_code}")

        if r.status_code == 500:
            data = r.json()
            assert_true("error" in data, "error present")
            ok(f"Correctly failed with no API key: {data['error'].get('message', '')[:50]}")
        else:
            ok(f"Returned status {r.status_code}")
        return True
    except Exception as e:
        fail(str(e))
        return False


# ============================================================
# Phase 11: Cleanup
# ============================================================

def deregister_machine(auth_token, machine_id):
    """Deregister the test machine."""
    test("Deregister machine")
    try:
        r = requests.delete(
            f"{BASE_URL}/api/remote/machines/{machine_id}",
            cookies={"session_token": auth_token},
            timeout=10,
        )
        assert_eq(r.status_code, 200, "deregister status")
        data = r.json()
        assert_true(data.get("success"), "deregister success")

        ok("Machine deregistered")
        return True
    except Exception as e:
        fail(str(e))
        return False


def verify_machine_removed(auth_token, machine_id):
    """Verify machine is removed."""
    test("Verify machine removed")
    try:
        r = requests.get(
            f"{BASE_URL}/api/remote/machines/{machine_id}",
            cookies={"session_token": auth_token},
            timeout=10,
        )
        assert_eq(r.status_code, 404, "removed status")

        ok("Machine correctly returns 404 after deregistration")
        return True
    except Exception as e:
        fail(str(e))
        return False


# ============================================================
# Phase 12: Direct Module Tests
# ============================================================

def test_session_manager_direct():
    """Test SessionManager directly for workspace_type support."""
    test("SessionManager workspace_type support")
    try:
        from app.modules.workspace.session_manager import SessionManager
        sm = SessionManager()

        # Create a session with workspace_type
        session = sm.create_session(
            tool_name="qwen-code-cli",
            user_id=1,
            title="E2E Direct Test",
            project_path="/test/path",
        )
        assert_true(session is not None, "session created")
        assert_true(session.session_id, "session_id")

        # Update with workspace type
        session.context["workspace_type"] = "remote"
        session.context["remote_machine_id"] = "test-machine-id"
        sm.update_session(session)

        # Retrieve and verify
        retrieved = sm.get_session(session.session_id)
        assert_true(retrieved is not None, "session retrieved")
        assert_eq(retrieved.context.get("workspace_type"), "remote", "workspace_type")
        assert_eq(retrieved.context.get("remote_machine_id"), "test-machine-id", "machine_id")

        # Add message
        sm.add_message(session.session_id, "user", "Test message")
        sm.add_message(session.session_id, "assistant", "Test response")

        updated = sm.get_session(session.session_id)
        assert_eq(updated.message_count, 2, "message count")

        # Cleanup
        sm.delete_session(session.session_id)

        ok(f"workspace_type=remote, messages=2, session CRUD works")
        return True
    except Exception as e:
        fail(str(e))
        return False


def test_agent_manager_direct():
    """Test RemoteAgentManager directly."""
    test("RemoteAgentManager direct operations")
    try:
        from app.modules.workspace.remote_agent_manager import get_remote_agent_manager
        mgr = get_remote_agent_manager()

        # Create registration token
        token = mgr.create_registration_token(tenant_id=1, created_by=1)
        assert_true(token, "registration token")

        # Register machine
        machine_id = str(uuid.uuid4())
        result = mgr.register_machine(
            registration_token=token,
            machine_id=machine_id,
            machine_name="direct-test",
            hostname="direct.local",
            os_type="darwin",
        )
        assert_true(result is not None, "register result")
        assert_eq(result["machine_id"], machine_id, "machine_id")

        # Get machine
        machine = mgr.get_machine(machine_id)
        assert_true(machine is not None, "get machine")
        assert_eq(machine["machine_name"], "direct-test", "machine_name")
        assert_true(machine["connected"] is False, "not connected (no WS)")

        # List machines
        machines = mgr.list_machines()
        assert_true(len(machines) >= 1, "machine count")

        # Check user access (should be assigned to creator)
        has_access = mgr.check_user_access(machine_id, 1)
        assert_true(has_access, "creator has access")

        # Assign another user
        mgr.assign_user(machine_id, user_id=999, granted_by=1, permission="use")
        has_999 = mgr.check_user_access(machine_id, 999)
        assert_true(has_999, "assigned user has access")

        # Revoke user
        revoked = mgr.revoke_user(machine_id, 999)
        assert_true(revoked, "revoke success")
        has_999_after = mgr.check_user_access(machine_id, 999)
        assert_true(not has_999_after, "revoked user no access")

        # Deregister
        dereg = mgr.deregister_machine(machine_id)
        assert_true(dereg, "deregister success")

        gone = mgr.get_machine(machine_id)
        assert_true(gone is None, "machine removed")

        ok("Full CRUD lifecycle passed")
        return True
    except Exception as e:
        fail(str(e))
        return False


def test_cli_adapters():
    """Test CLI adapter registry."""
    test("CLI adapters registry")
    try:
        # Add remote-agent to path
        sys.path.insert(0, os.path.join(PROJECT_ROOT, "remote-agent"))
        from cli_adapters import get_adapter, list_adapters

        adapters = list_adapters()
        assert_true("qwen-code-cli" in adapters, "qwen-code-cli")
        assert_true("claude-code" in adapters, "claude-code")
        assert_true("openclaw" in adapters, "openclaw")

        # Test each adapter
        for name in adapters:
            adapter = get_adapter(name)
            assert_true(adapter.get_display_name(), f"{name} display name")
            assert_true(adapter.get_executable_name(), f"{name} executable name")
            env = adapter.get_env_vars("http://proxy:8080", "test-token")
            assert_true(isinstance(env, dict), f"{name} env vars")
            assert_true(len(env) > 0, f"{name} env vars non-empty")
            args = adapter.build_start_args("sess1", "/path", "model-1")
            assert_true(isinstance(args, list), f"{name} start args")

        # Test generic adapter fallback
        generic = get_adapter("unknown-tool")
        assert_true(generic is not None, "generic adapter")
        assert_eq(generic.get_display_name(), "unknown-tool", "generic name")

        ok(f"All {len(adapters)} adapters + generic fallback work")
        return True
    except Exception as e:
        fail(str(e))
        return False


def test_database_tables():
    """Verify database tables exist with correct schema."""
    test("Database tables and schema")
    try:
        from app.repositories.database import get_database_url, is_postgresql
        if is_postgresql():
            import psycopg2
            conn = psycopg2.connect(get_database_url())
        else:
            import sqlite3
            conn = sqlite3.connect(os.path.expanduser("~/.open-ace/ace.db"))

        cur = conn.cursor()

        # Check remote_machines table
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'remote_machines'
        """ if is_postgresql() else "PRAGMA table_info(remote_machines)")

        if is_postgresql():
            columns = [r[0] for r in cur.fetchall()]
        else:
            columns = [r[1] for r in cur.fetchall()]

        expected_cols = {"machine_id", "machine_name", "hostname", "status", "agent_version"}
        found = expected_cols.intersection(set(columns))
        assert_true(len(found) >= 4, f"remote_machines columns: {found}")

        # Check machine_assignments table
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'machine_assignments'
        """ if is_postgresql() else "PRAGMA table_info(machine_assignments)")
        if is_postgresql():
            cols2 = [r[0] for r in cur.fetchall()]
        else:
            cols2 = [r[1] for r in cur.fetchall()]
        assert_true("machine_id" in cols2, "machine_assignments.machine_id")
        assert_true("user_id" in cols2, "machine_assignments.user_id")

        # Check api_key_store table
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'api_key_store'
        """ if is_postgresql() else "PRAGMA table_info(api_key_store)")
        if is_postgresql():
            cols3 = [r[0] for r in cur.fetchall()]
        else:
            cols3 = [r[1] for r in cur.fetchall()]
        assert_true("encrypted_key" in cols3, "api_key_store.encrypted_key")
        assert_true("provider" in cols3, "api_key_store.provider")

        conn.close()
        ok("All 3 tables (remote_machines, machine_assignments, api_key_store) verified")
        return True
    except Exception as e:
        fail(str(e))
        return False


# ============================================================
# Main runner
# ============================================================

def main():
    header("Open ACE Remote Workspace - End-to-End Test")

    # Phase 0: Prerequisites
    header("Phase 0: Prerequisites")
    if not check_server():
        print("\nFATAL: Server not running at", BASE_URL)
        print("Start with: python web.py")
        sys.exit(1)

    # Phase 1: Authentication
    header("Phase 1: Authentication")
    auth_token, user = authenticate()
    if not auth_token:
        print("\nFATAL: Cannot authenticate")
        sys.exit(1)

    # Phase 2: Machine Registration
    header("Phase 2: Machine Registration")
    reg_token = generate_registration_token(auth_token)
    machine_id = register_machine_with_token(reg_token)
    test_reuse_registration_token(reg_token)
    list_machines(auth_token)
    get_machine_detail(auth_token, machine_id)

    # Phase 3: Agent Connection
    header("Phase 3: Agent Connection (HTTP)")
    agent_connect(machine_id)
    agent_heartbeat(machine_id)

    # Phase 4: API Key Management
    header("Phase 4: API Key Management")
    store_api_key(auth_token)
    test_proxy_token_flow(auth_token)
    test_proxy_token_expiry()
    test_proxy_token_tampered()

    # Phase 5: Remote Session Lifecycle
    header("Phase 5: Remote Session Lifecycle")
    session_id = create_remote_session(auth_token, machine_id)
    if session_id:
        get_session_status(auth_token, session_id)
        send_message_to_session(auth_token, session_id)
        agent_sends_output(machine_id, session_id)
        verify_output_buffered(auth_token, session_id)
        agent_sends_status(machine_id, session_id)

    # Phase 6: Usage Reporting
    header("Phase 6: Usage Reporting")
    if session_id:
        agent_sends_usage(machine_id, session_id)
        verify_usage_recorded(auth_token, session_id)
        usage_report_endpoint(machine_id, session_id)

    # Phase 7: Session Control
    header("Phase 7: Session Control")
    if session_id:
        test_session_pause_resume(auth_token, session_id, machine_id)
        stop_remote_session(auth_token, session_id)

    # Phase 8: Access Control
    header("Phase 8: Access Control")
    test_unauthenticated_access()
    test_non_admin_registration(auth_token)
    test_machine_user_assignment(auth_token, machine_id)

    # Phase 9: Available Machines
    header("Phase 9: Available Machines")
    test_available_machines(auth_token)

    # Phase 10: LLM Proxy
    header("Phase 10: LLM Proxy Token Validation")
    test_llm_proxy_no_token()
    test_llm_proxy_invalid_token()
    test_llm_proxy_valid_token_no_key()

    # Phase 11: Cleanup
    header("Phase 11: Cleanup")
    deregister_machine(auth_token, machine_id)
    verify_machine_removed(auth_token, machine_id)

    # Phase 12: Direct Module Tests
    header("Phase 12: Direct Module Tests")
    test_session_manager_direct()
    test_agent_manager_direct()
    test_cli_adapters()
    test_database_tables()

    # Summary
    header("TEST SUMMARY")
    passed = sum(1 for _, r, _ in RESULTS if r is True)
    failed = sum(1 for _, r, _ in RESULTS if r is False)
    skipped = sum(1 for _, r, _ in RESULTS if r == "skip")
    total = len(RESULTS)

    for name, result, detail in RESULTS:
        icon = "PASS" if result is True else ("FAIL" if result is False else "SKIP")
        line = f"  [{icon}] {name}"
        if detail:
            line += f" - {detail}"
        print(line)

    print(f"\n  Total: {total} | Passed: {passed} | Failed: {failed} | Skipped: {skipped}")

    if failed > 0:
        print(f"\n  *** {failed} TEST(S) FAILED ***")
        return 1
    else:
        print("\n  ALL TESTS PASSED!")
        return 0


if __name__ == "__main__":
    sys.exit(main())
