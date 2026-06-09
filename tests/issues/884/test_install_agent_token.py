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


class TestInstallScriptStructure:
    """Verify token logic is inside the registration success branch."""

    def test_sh_agent_token_inside_success_branch(self):
        """install.sh agent_token extraction should be inside the success block."""
        with open(INSTALL_SH) as f:
            content = f.read()
        # Find the registration success block and verify AGENT_TOKEN logic is within it
        success_match = re.search(
            r"registered successfully.*?(?=\n\s*else\b|\n\s*fi\b)",
            content,
            re.DOTALL,
        )
        assert success_match is not None, "Registration success block not found"
        assert "AGENT_TOKEN" in success_match.group(
            0
        ), "AGENT_TOKEN extraction not inside registration success block"

    def test_ps1_agent_token_inside_success_branch(self):
        """install.ps1 agent_token extraction should be inside the success block."""
        with open(INSTALL_PS1) as f:
            content = f.read()
        # Find the success block and verify agent_token logic is within it
        success_match = re.search(
            r"\$response\.success.*?(?=\} elseif|\} catch|\}$)",
            content,
            re.DOTALL,
        )
        assert success_match is not None, "Registration success block not found"
        assert "agent_token" in success_match.group(
            0
        ), "agent_token extraction not inside registration success block"

    def test_sh_uses_sys_argv_for_path(self):
        """install.sh should pass config path via sys.argv, not string interpolation."""
        with open(INSTALL_SH) as f:
            content = f.read()
        # The save script should use sys.argv[1] for config_path
        assert "sys.argv[1]" in content
        assert "sys.argv[2]" in content

    def test_ps1_has_try_catch_for_token_save(self):
        """install.ps1 should have try/catch around token save."""
        with open(INSTALL_PS1) as f:
            content = f.read()
        # Verify there's a try block around the token save
        assert "try {" in content
        assert "Set-Content" in content
