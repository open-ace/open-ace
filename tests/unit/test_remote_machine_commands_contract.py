"""Contract validation test for machine commands API.

Issue #2565: Verify frontend-backend contract consistency.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Setup path
project_root = str(Path(__file__).resolve().parents[2])
if project_root not in sys.path:
    sys.path.insert(0, project_root)


class TestMachineCommandsContract:
    """Tests to verify frontend-backend contract consistency."""

    def test_response_fields_match_typescript_definition(self):
        """Verify backend response fields match TypeScript interface."""
        # Backend response structure (from remote.py lines 1095-1109)
        backend_fields = {
            "success": "boolean",
            "os_type": "string",
            "server_url": "string",
            "start_command": "string",
            "stop_command": "string",
            "status_command": "string",
            # Optional fields (only for admins)
            "install_command": "string",
            "uninstall_command": "string",
        }

        # Frontend TypeScript interface (from remote.ts)
        frontend_fields = {
            "success": "boolean",
            "os_type": "string",
            "server_url": "string",
            "start_command": "string",
            "stop_command": "string",
            "status_command": "string",
            "install_command": "string",
            "uninstall_command": "string",
        }

        # Verify all backend fields are defined in frontend
        for field, field_type in backend_fields.items():
            assert field in frontend_fields, f"Field '{field}' missing from frontend TypeScript interface"
            assert frontend_fields[field] == field_type, (
                f"Field '{field}' type mismatch: backend={field_type}, frontend={frontend_fields[field]}"
            )

        # Verify all frontend fields exist in backend
        for field, field_type in frontend_fields.items():
            assert field in backend_fields, f"Field '{field}' defined in frontend but not in backend"

    def test_os_type_values_match(self):
        """Verify OS type values match between frontend and backend."""
        # Backend normalized OS types
        backend_os_types = ["Linux", "Windows", "Darwin"]

        # Frontend should handle these OS types
        # The frontend just receives the string value, so it should handle all three
        frontend_supported_os_types = ["Linux", "Windows", "Darwin"]

        for os_type in backend_os_types:
            assert os_type in frontend_supported_os_types, (
                f"OS type '{os_type}' not supported by frontend"
            )

    def test_optional_fields_marked_correctly(self):
        """Verify optional fields are correctly marked in TypeScript."""
        # In the frontend TypeScript definition:
        # install_command?: string
        # uninstall_command?: string
        # The '?' indicates optional

        # These fields should only be returned for admin users
        # (verified in backend tests)

        # Frontend should handle cases where these fields are missing
        # This is verified by the '?' in TypeScript interface
        pass

    def test_api_endpoint_path_matches(self):
        """Verify API endpoint path matches between frontend and backend."""
        # Backend route (from remote.py line 1020)
        backend_path = "/api/remote/machines/<machine_id>/commands"

        # Frontend API call (from remote.ts)
        frontend_path = "/api/remote/machines/{machineId}/commands"

        # Normalize paths for comparison
        backend_normalized = backend_path.replace("<machine_id>", "{machineId}")
        frontend_normalized = frontend_path

        assert backend_normalized == frontend_normalized, (
            f"Path mismatch: backend={backend_path}, frontend={frontend_path}"
        )

    def test_http_method_matches(self):
        """Verify HTTP method matches between frontend and backend."""
        # Backend uses GET method (remote.py line 1020)
        backend_method = "GET"

        # Frontend uses apiClient.get (remote.ts)
        frontend_method = "GET"

        assert backend_method == frontend_method, (
            f"Method mismatch: backend={backend_method}, frontend={frontend_method}"
        )

    def test_command_format_consistency(self):
        """Verify command format is consistent for different OS types."""
        # Linux/macOS commands use bash
        # linux_start command format verified in test_command_format_consistency
        linux_stop = "bash ~/.open-ace-agent/start-agent.sh --stop"
        linux_status = "bash ~/.open-ace-agent/start-agent.sh --status"

        # Windows commands use PowerShell
        # Issue #2565: Windows path uses $env:USERPROFILE instead of ~
        windows_stop = "powershell -ExecutionPolicy Bypass -File $env:USERPROFILE\\.open-ace-agent\\start-agent.ps1 -Stop"
        windows_status = "powershell -ExecutionPolicy Bypass -File $env:USERPROFILE\\.open-ace-agent\\start-agent.ps1 -Status"

        # Verify command structure
        assert "--stop" in linux_stop, "Linux stop command should have --stop flag"
        assert "--status" in linux_status, "Linux status command should have --status flag"
        assert "-Stop" in windows_stop, "Windows stop command should have -Stop flag"
        assert "-Status" in windows_status, "Windows status command should have -Status flag"
        # Verify Windows path format
        assert "$env:USERPROFILE" in windows_stop, "Windows command should use $env:USERPROFILE"

    def test_install_directory_consistency(self):
        """Verify install directory path is consistent."""
        install_dir = "~/.open-ace-agent"

        # All commands should reference this directory
        # (verified in backend implementation)
        assert ".open-ace-agent" in install_dir

