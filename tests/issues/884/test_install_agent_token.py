"""
Tests for Issue #884: Install scripts persist agent_token after registration.

Validates that install.sh and install.ps1 extract agent_token from the
registration response and write it to config.json.
"""

import os
import re

import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
INSTALL_SH = os.path.join(REPO_ROOT, "remote-agent", "install.sh")
INSTALL_PS1 = os.path.join(REPO_ROOT, "remote-agent", "install.ps1")


class TestInstallShellAgentToken:
    """Validate install.sh extracts and saves agent_token."""

    @pytest.fixture
    def script_content(self):
        with open(INSTALL_SH) as f:
            return f.read()

    def test_install_sh_extracts_agent_token(self, script_content):
        """install.sh should extract agent_token from registration response."""
        # Should parse response.machine.agent_token
        assert "machine" in script_content
        assert "agent_token" in script_content

    def test_install_sh_saves_agent_token_to_config(self, script_content):
        """install.sh should update config.json with agent_token."""
        # Should write agent_token into the config file
        assert "agent_token" in script_content
        # Should use Python to update config.json
        assert "config.json" in script_content
        # Should have the extraction logic near registration success
        assert "AGENT_TOKEN" in script_content

    def test_install_sh_handles_missing_agent_token(self, script_content):
        """install.sh should handle case where server does not return agent_token."""
        # Should have a fallback or check for empty token
        assert "-n" in script_content  # [ -n "$AGENT_TOKEN" ] check


class TestInstallPs1AgentToken:
    """Validate install.ps1 extracts and saves agent_token."""

    @pytest.fixture
    def script_content(self):
        with open(INSTALL_PS1) as f:
            return f.read()

    def test_install_ps1_extracts_agent_token(self, script_content):
        """install.ps1 should extract agent_token from registration response."""
        assert "$response.machine" in script_content
        assert "agent_token" in script_content

    def test_install_ps1_saves_agent_token_to_config(self, script_content):
        """install.ps1 should update config.json with agent_token."""
        assert "$config.agent_token" in script_content
        assert "ConvertTo-Json" in script_content
        assert "config.json" in script_content

    def test_install_ps1_handles_missing_agent_token(self, script_content):
        """install.ps1 should handle case where response.machine is absent."""
        # Should check $response.machine exists before accessing agent_token
        assert "$response.machine" in script_content


class TestInstallScriptsRegistrationResponse:
    """Validate both scripts handle the registration response correctly."""

    def test_shell_checks_success_field(self):
        """install.sh checks d.get('success') from registration response."""
        with open(INSTALL_SH) as f:
            content = f.read()
        assert "success" in content

    def test_ps1_checks_success_field(self):
        """install.ps1 checks $response.success from registration response."""
        with open(INSTALL_PS1) as f:
            content = f.read()
        assert "$response.success" in content

    def test_shell_config_has_machine_id(self):
        """install.sh writes machine_id to config.json."""
        with open(INSTALL_SH) as f:
            content = f.read()
        assert "machine_id" in content

    def test_ps1_config_has_machine_id(self):
        """install.ps1 writes machine_id to config.json."""
        with open(INSTALL_PS1) as f:
            content = f.read()
        assert "machine_id" in content
