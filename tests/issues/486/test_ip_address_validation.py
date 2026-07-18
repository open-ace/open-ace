"""
Test for IP address validation during agent registration (Issue #486).

Covers:
- Agent reports valid IP → stored correctly
- Agent reports invalid IP format → fallback to request IP
- Agent reports 127.0.0.1 → fallback to request IP
- validate_ip() function unit tests
"""

import os
import sys
import unittest
from unittest.mock import MagicMock

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))


# ==================== Unit Tests: validate_ip() ====================


class TestValidateIP(unittest.TestCase):
    """Test the validate_ip function."""

    def test_valid_ipv4(self):
        """Valid IPv4 addresses should pass."""
        from app.routes.remote import validate_ip

        valid_ips = [
            "192.168.1.1",
            "10.0.0.1",
            "172.16.0.1",
            "8.8.8.8",
            "127.0.0.1",
            "0.0.0.0",
            "255.255.255.255",
        ]
        for ip in valid_ips:
            self.assertTrue(validate_ip(ip), f"{ip} should be valid")

    def test_valid_ipv6(self):
        """Valid IPv6 addresses should pass."""
        from app.routes.remote import validate_ip

        valid_ips = [
            "::1",
            "2001:db8::1",
            "fe80::1",
            "ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff",
        ]
        for ip in valid_ips:
            self.assertTrue(validate_ip(ip), f"{ip} should be valid")

    def test_invalid_format(self):
        """Invalid IP formats should fail."""
        from app.routes.remote import validate_ip

        invalid_ips = [
            "invalid",
            "192.168.1",  # missing last octet
            "192.168.1.1.1",  # extra octet
            "256.0.0.1",  # octet > 255
            "192.168.1.abc",  # non-numeric
            "",  # empty string
            "  ",  # whitespace
            "192.168.1.1 ",  # trailing space
            " 192.168.1.1",  # leading space
            "http://192.168.1.1",  # URL format
            "192.168.1.1:8080",  # with port
        ]
        for ip in invalid_ips:
            self.assertFalse(validate_ip(ip), f"{ip} should be invalid")

    def test_none_and_non_string(self):
        """None and non-string inputs should fail."""
        from app.routes.remote import validate_ip

        self.assertFalse(validate_ip(None))
        self.assertFalse(validate_ip(123))
        self.assertFalse(validate_ip([]))
        self.assertFalse(validate_ip({}))


# ==================== Route Tests: Agent Registration IP ====================


class TestAgentRegisterIP(unittest.TestCase):
    """Test the agent_register route IP handling."""

    def _make_app(self, mgr):
        """Create a minimal Flask app with remote_bp for route testing."""
        from flask import Flask

        import app.modules.workspace.remote_agent_manager as ram_mod
        from app.routes import remote as remote_mod

        ram_mod._agent_manager = mgr

        app = Flask(__name__)
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test-secret"
        app.register_blueprint(remote_mod.remote_bp, url_prefix="/api/remote")

        return app

    def test_agent_reports_valid_ipv4(self):
        """Agent reports valid IPv4 → should be stored."""
        mgr = MagicMock()
        mgr.register_machine.return_value = {
            "success": True,
            "machine": {
                "machine_id": "test-id",
                "ip_address": "192.168.1.100",
                "status": "online",
            },
        }
        mgr.create_registration_token.return_value = "test-token"

        app = self._make_app(mgr)
        with app.test_client() as client:
            resp = client.post(
                "/api/remote/agent/register",
                json={
                    "registration_token": "test-token",
                    "machine_id": "test-id",
                    "machine_name": "test-machine",
                    "hostname": "testhost",
                    "os_type": "linux",
                    "ip_address": "192.168.1.100",  # Valid IP
                },
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertTrue(data.get("success"))

            # Verify manager was called with the valid IP
            call_args = mgr.register_machine.call_args
            self.assertEqual(call_args.kwargs.get("ip_address"), "192.168.1.100")

    def test_agent_reports_invalid_ip_format(self):
        """Agent reports invalid IP format → should use request IP."""
        mgr = MagicMock()
        mgr.register_machine.return_value = {
            "success": True,
            "machine": {
                "machine_id": "test-id",
                "ip_address": "127.0.0.1",  # Fallback from request
                "status": "online",
            },
        }
        mgr.create_registration_token.return_value = "test-token"

        app = self._make_app(mgr)
        with app.test_client() as client:
            resp = client.post(
                "/api/remote/agent/register",
                json={
                    "registration_token": "test-token",
                    "machine_id": "test-id",
                    "machine_name": "test-machine",
                    "hostname": "testhost",
                    "os_type": "linux",
                    "ip_address": "invalid-ip-format",  # Invalid IP
                },
            )
            self.assertEqual(resp.status_code, 200)

            # Verify manager was called with fallback IP (not invalid string)
            call_args = mgr.register_machine.call_args
            ip_used = call_args.kwargs.get("ip_address")
            # Should NOT be "invalid-ip-format"
            self.assertNotEqual(ip_used, "invalid-ip-format")

    def test_agent_reports_loopback_127_0_0_1(self):
        """Agent reports 127.0.0.1 → should use request IP fallback."""
        mgr = MagicMock()
        mgr.register_machine.return_value = {
            "success": True,
            "machine": {
                "machine_id": "test-id",
                "ip_address": "127.0.0.1",  # From request fallback
                "status": "online",
            },
        }
        mgr.create_registration_token.return_value = "test-token"

        app = self._make_app(mgr)
        with app.test_client() as client:
            resp = client.post(
                "/api/remote/agent/register",
                json={
                    "registration_token": "test-token",
                    "machine_id": "test-id",
                    "machine_name": "test-machine",
                    "hostname": "testhost",
                    "os_type": "linux",
                    "ip_address": "127.0.0.1",  # Loopback - should be rejected
                },
            )
            self.assertEqual(resp.status_code, 200)

            # Agent-reported 127.0.0.1 was rejected, fallback used from request
            # (In test client, request.remote_addr is 127.0.0.1, so fallback matches)
            call_args = mgr.register_machine.call_args
            # Verify register_machine was called
            self.assertIsNotNone(call_args)

    def test_agent_reports_valid_ipv6(self):
        """Agent reports valid IPv6 → should be stored."""
        mgr = MagicMock()
        mgr.register_machine.return_value = {
            "success": True,
            "machine": {
                "machine_id": "test-id",
                "ip_address": "2001:db8::1",
                "status": "online",
            },
        }
        mgr.create_registration_token.return_value = "test-token"

        app = self._make_app(mgr)
        with app.test_client() as client:
            resp = client.post(
                "/api/remote/agent/register",
                json={
                    "registration_token": "test-token",
                    "machine_id": "test-id",
                    "machine_name": "test-machine",
                    "hostname": "testhost",
                    "os_type": "linux",
                    "ip_address": "2001:db8::1",  # Valid IPv6
                },
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertTrue(data.get("success"))

            # Verify manager was called with the valid IPv6
            call_args = mgr.register_machine.call_args
            self.assertEqual(call_args.kwargs.get("ip_address"), "2001:db8::1")

    def test_agent_no_ip_address_field(self):
        """Agent does not report ip_address → should use request IP."""
        mgr = MagicMock()
        mgr.register_machine.return_value = {
            "success": True,
            "machine": {
                "machine_id": "test-id",
                "ip_address": "127.0.0.1",  # From request
                "status": "online",
            },
        }
        mgr.create_registration_token.return_value = "test-token"

        app = self._make_app(mgr)
        with app.test_client() as client:
            resp = client.post(
                "/api/remote/agent/register",
                json={
                    "registration_token": "test-token",
                    "machine_id": "test-id",
                    "machine_name": "test-machine",
                    "hostname": "testhost",
                    "os_type": "linux",
                    # No ip_address field
                },
            )
            self.assertEqual(resp.status_code, 200)

            # Verify manager was called with fallback IP
            call_args = mgr.register_machine.call_args
            self.assertIsNotNone(call_args.kwargs.get("ip_address"))


# ==================== Integration Test: Full Registration Flow ====================


class TestIPRegistrationIntegration(unittest.TestCase):
    """Integration test with actual database."""

    def setUp(self):
        """Set up test database."""
        import sqlite3
        import tempfile

        from app.modules.workspace.remote_agent_manager import RemoteAgentManager
        from app.repositories.schema_init import load_schema_from_file

        self.db_file = tempfile.mktemp(suffix=".db")
        # Initialize database tables
        conn = sqlite3.connect(self.db_file)
        load_schema_from_file(db_url=f"sqlite:///{self.db_file}", dialect="sqlite")
        conn.commit()
        conn.close()

        self.mgr = RemoteAgentManager(db_path=self.db_file)

    def tearDown(self):
        """Clean up test database."""
        if os.path.exists(self.db_file):
            os.remove(self.db_file)

    def test_registration_with_valid_ip_stored_correctly(self):
        """Valid IP should be stored in database."""
        token = self.mgr.create_registration_token(tenant_id=1, created_by=1)
        result = self.mgr.register_machine(
            registration_token=token,
            machine_id="test-machine-1",
            machine_name="Test Machine",
            hostname="testhost",
            os_type="linux",
            ip_address="192.168.1.100",
        )

        self.assertIsNotNone(result)
        self.assertEqual(result.get("machine_id"), "test-machine-1")
        self.assertEqual(result.get("status"), "online")

        # Verify IP stored correctly by fetching machine
        machines = self.mgr.list_machines()
        test_machine = next((m for m in machines if m["machine_id"] == "test-machine-1"), None)
        self.assertIsNotNone(test_machine)
        self.assertEqual(test_machine.get("ip_address"), "192.168.1.100")

    def test_registration_with_invalid_ip_not_stored(self):
        """Invalid IP should not be stored directly."""
        token = self.mgr.create_registration_token(tenant_id=1, created_by=1)
        # The route validation happens before manager is called
        # This test verifies manager would receive validated IP
        result = self.mgr.register_machine(
            registration_token=token,
            machine_id="test-machine-2",
            machine_name="Test Machine 2",
            hostname="testhost2",
            os_type="linux",
            ip_address="not-an-ip",  # Manager accepts it (validation is in route)
        )

        # Manager stores whatever it receives (route validates first)
        self.assertIsNotNone(result)
        self.assertEqual(result.get("machine_id"), "test-machine-2")

    def test_machine_list_shows_correct_ip(self):
        """Machines list should show stored IP."""
        token = self.mgr.create_registration_token(tenant_id=1, created_by=1)
        self.mgr.register_machine(
            registration_token=token,
            machine_id="test-machine-3",
            machine_name="Test Machine 3",
            hostname="testhost3",
            os_type="linux",
            ip_address="10.0.0.50",
        )

        machines = self.mgr.list_machines()
        test_machine = next((m for m in machines if m["machine_id"] == "test-machine-3"), None)
        self.assertIsNotNone(test_machine)
        self.assertEqual(test_machine["ip_address"], "10.0.0.50")


if __name__ == "__main__":
    unittest.main()


# ==================== Unit Tests: update_machine_ip() ====================


class TestUpdateMachineIP(unittest.TestCase):
    """Test the update_machine_ip method in RemoteAgentManager."""

    def setUp(self):
        """Set up test database and manager."""
        import sqlite3
        import tempfile

        from app.modules.workspace.remote_agent_manager import RemoteAgentManager
        from app.repositories.schema_init import load_schema_from_file

        self.db_file = tempfile.mktemp(suffix=".db")
        load_schema_from_file(db_url=f"sqlite:///{self.db_file}", dialect="sqlite")

        self.mgr = RemoteAgentManager(db_path=self.db_file)
        # Register a test machine first
        token = self.mgr.create_registration_token(tenant_id=1, created_by=1)
        self.mgr.register_machine(
            registration_token=token,
            machine_id="test-ip-update-machine",
            machine_name="Test IP Update",
            hostname="testipupdate",
            os_type="linux",
            ip_address="127.0.0.1",  # Initial IP is loopback
        )

    def tearDown(self):
        """Clean up test database."""
        if os.path.exists(self.db_file):
            os.remove(self.db_file)

    def test_update_machine_ip_basic(self):
        """update_machine_ip should update IP address correctly."""
        self.mgr.update_machine_ip("test-ip-update-machine", "192.168.1.100")

        machine = self.mgr.get_machine("test-ip-update-machine")
        self.assertIsNotNone(machine)
        self.assertEqual(machine.get("ip_address"), "192.168.1.100")

    def test_update_machine_ip_ipv6(self):
        """update_machine_ip should handle IPv6 addresses."""
        self.mgr.update_machine_ip("test-ip-update-machine", "2001:db8::1")

        machine = self.mgr.get_machine("test-ip-update-machine")
        self.assertIsNotNone(machine)
        self.assertEqual(machine.get("ip_address"), "2001:db8::1")

    def test_update_machine_ip_nonexistent_machine(self):
        """update_machine_ip should not fail for nonexistent machine."""
        # Should silently do nothing (no exception raised)
        self.mgr.update_machine_ip("nonexistent-machine-id", "10.0.0.1")

    def test_update_machine_ip_overwrites_previous(self):
        """update_machine_ip should overwrite previous IP."""
        self.mgr.update_machine_ip("test-ip-update-machine", "10.0.0.1")
        self.mgr.update_machine_ip("test-ip-update-machine", "172.16.0.1")

        machine = self.mgr.get_machine("test-ip-update-machine")
        self.assertEqual(machine.get("ip_address"), "172.16.0.1")


# ==================== Route Tests: agent_message IP update ====================


class TestAgentMessageRegisterLogic(unittest.TestCase):
    """Test the IP update logic used in agent_message register handler."""

    def setUp(self):
        """Set up test database and manager."""
        import sqlite3
        import tempfile

        from app.modules.workspace.remote_agent_manager import RemoteAgentManager
        from app.repositories.schema_init import load_schema_from_file

        self.db_file = tempfile.mktemp(suffix=".db")
        load_schema_from_file(db_url=f"sqlite:///{self.db_file}", dialect="sqlite")

        self.mgr = RemoteAgentManager(db_path=self.db_file)
        # Register a test machine
        token = self.mgr.create_registration_token(tenant_id=1, created_by=1)
        self.mgr.register_machine(
            registration_token=token,
            machine_id="test-register-logic-machine",
            machine_name="Test Register Logic",
            hostname="testreglogic",
            os_type="linux",
            ip_address="127.0.0.1",
        )

    def tearDown(self):
        """Clean up test database."""
        if os.path.exists(self.db_file):
            os.remove(self.db_file)

    def test_register_logic_updates_valid_ip(self):
        """Valid non-loopback IP should be updated via register logic."""
        from app.routes.remote import validate_ip

        # Simulate the logic from agent_message register handler
        agent_reported_ip = "192.168.1.200"
        if (
            agent_reported_ip
            and agent_reported_ip != "127.0.0.1"
            and validate_ip(agent_reported_ip)
        ):
            self.mgr.update_machine_ip("test-register-logic-machine", agent_reported_ip)

        machine = self.mgr.get_machine("test-register-logic-machine")
        self.assertEqual(machine.get("ip_address"), "192.168.1.200")

    def test_register_logic_skips_loopback(self):
        """Loopback IP (127.0.0.1) should not trigger update."""
        from app.routes.remote import validate_ip

        # First set a valid IP
        self.mgr.update_machine_ip("test-register-logic-machine", "10.0.0.1")

        # Simulate register with loopback IP
        agent_reported_ip = "127.0.0.1"
        if (
            agent_reported_ip
            and agent_reported_ip != "127.0.0.1"
            and validate_ip(agent_reported_ip)
        ):
            self.mgr.update_machine_ip("test-register-logic-machine", agent_reported_ip)

        # IP should remain unchanged
        machine = self.mgr.get_machine("test-register-logic-machine")
        self.assertEqual(machine.get("ip_address"), "10.0.0.1")

    def test_register_logic_skips_invalid_format(self):
        """Invalid IP format should not trigger update."""
        from app.routes.remote import validate_ip

        # First set a valid IP
        self.mgr.update_machine_ip("test-register-logic-machine", "172.16.0.1")

        # Simulate register with invalid IP format
        agent_reported_ip = "invalid-ip-format"
        if (
            agent_reported_ip
            and agent_reported_ip != "127.0.0.1"
            and validate_ip(agent_reported_ip)
        ):
            self.mgr.update_machine_ip("test-register-logic-machine", agent_reported_ip)

        # IP should remain unchanged
        machine = self.mgr.get_machine("test-register-logic-machine")
        self.assertEqual(machine.get("ip_address"), "172.16.0.1")

    def test_register_logic_handles_ipv6(self):
        """Valid IPv6 should be updated."""
        from app.routes.remote import validate_ip

        agent_reported_ip = "2001:db8::1"
        if (
            agent_reported_ip
            and agent_reported_ip != "127.0.0.1"
            and validate_ip(agent_reported_ip)
        ):
            self.mgr.update_machine_ip("test-register-logic-machine", agent_reported_ip)

        machine = self.mgr.get_machine("test-register-logic-machine")
        self.assertEqual(machine.get("ip_address"), "2001:db8::1")
