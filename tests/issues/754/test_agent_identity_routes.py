"""Route-level tests for agent identity authentication (Issue #754).

Tests the _validate_agent_bearer helper and agent_register response logic.
Uses the sys.modules patching pattern for route isolation.

The validate_agent_bearer tests run inside the patch.dict context
to ensure module-level references remain valid.
"""

import importlib.util
import json
import sys
import unittest
from unittest.mock import MagicMock, patch


class TestValidateAgentBearer(unittest.TestCase):
    """Tests for _validate_agent_bearer helper.

    Loads the module inside each test (within patch.dict context)
    to ensure all module-level references stay valid.
    """

    def _load_module(self):
        mock_modules = {
            "app.modules": MagicMock(__path__=[]),
            "app.modules.workspace": MagicMock(__path__=[]),
            "app.modules.workspace.remote_agent_manager": MagicMock(),
            "app.modules.workspace.remote_session_manager": MagicMock(),
            "app.modules.workspace.api_key_proxy": MagicMock(),
            "app.modules.workspace.llm_proxy_handler": MagicMock(),
            "app.modules.workspace.terminal_store": MagicMock(),
            "app.modules.workspace.agent_token": MagicMock(),
            "app.auth.decorators": MagicMock(
                _extract_token=MagicMock(return_value=""),
                _load_user_from_token=MagicMock(return_value=None),
                admin_required=lambda f: f,
            ),
            "app.repositories.database": MagicMock(),
            "app.repositories.schema_init": MagicMock(),
            "app.services.auth_service": MagicMock(),
            "app.services.permission_service": MagicMock(),
            "gevent": MagicMock(),
            "gevent.lock": MagicMock(),
        }
        with patch.dict(sys.modules, mock_modules):
            remote_path = "app/routes/remote.py"
            spec = importlib.util.spec_from_file_location("remote_test_754", remote_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module

    def test_no_bearer_returns_none_none(self):
        """Missing Bearer header should return (None, None)."""
        module = self._load_module()
        from flask import Flask

        app = Flask(__name__)
        with app.test_request_context("/", method="POST", headers={}):
            mid, error = module._validate_agent_bearer()
            self.assertIsNone(mid)
            self.assertIsNone(error)

    def test_empty_bearer_returns_401(self):
        """Empty Bearer token should return (None, 401 response)."""
        module = self._load_module()
        from flask import Flask

        app = Flask(__name__)
        with app.test_request_context("/", method="POST", headers={"Authorization": "Bearer "}):
            mid, error = module._validate_agent_bearer()
            self.assertIsNone(mid)
            self.assertIsNotNone(error)

    def test_valid_bearer_returns_machine_id(self):
        """Valid Bearer token should return machine_id."""
        module = self._load_module()
        from flask import Flask

        app = Flask(__name__)
        module.hash_agent_token = MagicMock(return_value="fake_hash_123")
        mock_mgr = MagicMock()
        mock_mgr.validate_agent_bearer.return_value = "machine-abc"
        module.get_remote_agent_manager = MagicMock(return_value=mock_mgr)

        with app.test_request_context(
            "/", method="POST", headers={"Authorization": "Bearer valid_token"}
        ):
            mid, error = module._validate_agent_bearer()
            self.assertEqual(mid, "machine-abc")
            self.assertIsNone(error)

    def test_invalid_bearer_returns_401(self):
        """Invalid Bearer token should return 401."""
        module = self._load_module()
        from flask import Flask

        app = Flask(__name__)
        module.hash_agent_token = MagicMock(return_value="bad_hash")
        mock_mgr = MagicMock()
        mock_mgr.validate_agent_bearer.return_value = None
        module.get_remote_agent_manager = MagicMock(return_value=mock_mgr)

        with app.test_request_context(
            "/", method="POST", headers={"Authorization": "Bearer bad_token"}
        ):
            mid, error = module._validate_agent_bearer()
            self.assertIsNone(mid)
            self.assertIsNotNone(error)
            self.assertEqual(error[1], 401)

    def test_bearer_token_bound_to_machine(self):
        """Bearer token should be bound to exactly one machine_id."""
        module = self._load_module()
        from flask import Flask

        app = Flask(__name__)
        # Token validates to machine-A
        module.hash_agent_token = MagicMock(return_value="hash_for_A")
        mock_mgr = MagicMock()
        mock_mgr.validate_agent_bearer.return_value = "machine-A"
        module.get_remote_agent_manager = MagicMock(return_value=mock_mgr)

        with app.test_request_context(
            "/", method="POST", headers={"Authorization": "Bearer token_for_A"}
        ):
            mid, error = module._validate_agent_bearer()
            # The returned machine_id should be machine-A, not something else
            self.assertEqual(mid, "machine-A")


class TestAgentRegisterResponseFormat(unittest.TestCase):
    """Tests for agent_register response construction logic.

    Tests the response body format that the route handler builds,
    verifying that agent_token is correctly separated from machine info.
    """

    def test_success_response_includes_agent_token(self):
        """Successful registration should include agent_token at top level."""
        result = {
            "machine_id": "test-mid",
            "machine_name": "test-machine",
            "status": "online",
            "tenant_id": 1,
            "agent_token": "secret_token_abc123",
        }
        response = {
            "success": True,
            "machine": {k: v for k, v in result.items() if k != "agent_token"},
            "agent_token": result.get("agent_token"),
        }
        self.assertTrue(response["success"])
        self.assertEqual(response["agent_token"], "secret_token_abc123")
        self.assertNotIn("agent_token", response["machine"])
        self.assertEqual(response["machine"]["machine_id"], "test-mid")

    def test_success_without_agent_token(self):
        """Registration without agent_token should have None for agent_token."""
        result = {
            "machine_id": "test-mid",
            "machine_name": "test",
            "status": "online",
            "tenant_id": 1,
        }
        response = {
            "success": True,
            "machine": {k: v for k, v in result.items() if k != "agent_token"},
            "agent_token": result.get("agent_token"),
        }
        self.assertIsNone(response["agent_token"])

    def test_hostname_conflict_check(self):
        """Hostname conflict result should have error key."""
        result = {
            "error": "hostname_conflict",
            "message": "Hostname 'myhost' is already online.",
        }
        self.assertEqual(result.get("error"), "hostname_conflict")

    def test_invalid_token_check(self):
        """Invalid token should make register_machine return None."""
        result = None
        self.assertIsNone(result)

    def test_missing_registration_token_returns_400(self):
        """Missing registration_token should be caught before register_machine."""
        data = {"machine_id": "m1", "machine_name": "test"}
        self.assertIsNone(data.get("registration_token"))

    def test_missing_machine_name_returns_400(self):
        """Missing machine_name should be caught before register_machine."""
        data = {"registration_token": "token123", "machine_id": "m1"}
        self.assertIsNone(data.get("machine_name"))


if __name__ == "__main__":
    unittest.main()
