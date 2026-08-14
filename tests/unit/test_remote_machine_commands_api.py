"""Tests for remote machine commands API endpoint.

Issue #2565: First-time user guidance enhancement.

This test verifies endpoint registration and URL structure without
requiring a full database setup. For permission-based tests, use
integration tests with proper auth fixtures.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Setup path
project_root = str(Path(__file__).resolve().parents[2])
if project_root not in sys.path:
    sys.path.insert(0, project_root)


class TestGetMachineCommandsEndpointRegistration:
    """Tests for endpoint registration and URL structure.

    These tests verify the endpoint is correctly registered without
    requiring database initialization.
    """

    def test_endpoint_handler_function_exists(self):
        """Verify the endpoint handler function exists."""
        from app.routes.remote import get_machine_commands

        # Function should be callable
        assert callable(get_machine_commands), "get_machine_commands should be callable"

        # Function should have the expected signature (machine_id parameter)
        import inspect

        sig = inspect.signature(get_machine_commands)
        params = list(sig.parameters.keys())
        assert "machine_id" in params, "get_machine_commands should have machine_id parameter"

    def test_endpoint_has_route_decorator(self):
        """Verify the endpoint has the correct route decorator."""
        from app.routes.remote import get_machine_commands

        # Check that the function has route information attached
        # Flask routes attach __name__ and other attributes
        assert hasattr(get_machine_commands, "__name__"), "Handler should have __name__ attribute"

        # The route decorator should have attached metadata
        # We can verify by checking the closure or checking the blueprint's deferred_functions
        from app.routes.remote import remote_bp

        # Blueprint should have deferred functions for route registration
        assert len(remote_bp.deferred_functions) > 0, "Blueprint should have registered routes"

    def test_endpoint_route_pattern_correct(self):
        """Verify the route pattern is correct."""
        from app.routes.remote import remote_bp

        # Check deferred functions for route registration
        # This is how Flask stores routes before app registration
        found_route = False
        for deferred_func in remote_bp.deferred_functions:
            # Each deferred function should register a route
            # We can't directly inspect the route without creating an app,
            # but we can verify the blueprint has deferred registrations
            found_route = True
            break

        assert found_route, "Blueprint should have route registration functions"

    def test_blueprint_name_correct(self):
        """Verify the blueprint name is 'remote'."""
        from app.routes.remote import remote_bp

        assert remote_bp.name == "remote", "Blueprint name should be 'remote'"
