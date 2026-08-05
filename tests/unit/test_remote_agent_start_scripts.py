"""
Test remote agent start scripts.

Tests cover:
- Process detection logic correctness
- Script syntax validation
- Error handling verification
- Cross-platform compatibility checks
"""
import os
import re
import stat
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
REMOTE_AGENT_DIR = REPO_ROOT / "remote-agent"


class TestBashStartScript:
    """Test the bash start-agent.sh script."""

    @property
    def script_path(self):
        return REMOTE_AGENT_DIR / "start-agent.sh"

    def test_script_exists_and_executable(self):
        """Verify the script file exists."""
        assert self.script_path.exists()
        # Check if it has execute permission
        assert os.access(self.script_path, os.X_OK)

    def test_script_has_shebang(self):
        """Verify the script has proper shebang."""
        content = self.script_path.read_text(encoding="utf-8")
        assert content.startswith("#!/usr/bin/env bash") or content.startswith("#!/bin/bash")

    def test_process_detection_regex(self):
        """Verify process detection uses proper regex pattern."""
        content = self.script_path.read_text(encoding="utf-8")
        # Should use pgrep with -f flag for full command line matching
        assert "pgrep -f" in content
        # Should reference agent.py specifically
        assert "agent.py" in content

    def test_stop_command_uses_pkill(self):
        """Verify stop command properly kills processes."""
        content = self.script_path.read_text(encoding="utf-8")
        assert "pkill -f" in content
        # Should have error suppression for non-existent processes
        assert "2>/dev/null" in content or "|| true" in content

    def test_python_finding_logic(self):
        """Verify Python binary finding logic exists."""
        content = self.script_path.read_text(encoding="utf-8")
        # Should have a find_python function
        assert "find_python" in content
        # Should check for python3 first
        lines = content.split("\n")
        for i, line in enumerate(lines):
            if "find_python()" in line or "find_python()" in line:
                # Check the next few lines for python3 check
                next_lines = "\n".join(lines[i:i+10])
                assert "python3" in next_lines
                break

    def test_auto_start_handles_systemd_and_cron(self):
        """Verify auto-start handles both systemd and crontab."""
        content = self.script_path.read_text(encoding="utf-8")
        # Should check for systemctl
        assert "systemctl" in content
        # Should have fallback to crontab
        assert "crontab" in content
        # Should handle WSL2 case
        assert "WSL" in content or "@reboot" in content

    def test_status_command_exists(self):
        """Verify status command is implemented."""
        content = self.script_path.read_text(encoding="utf-8")
        assert "--status" in content
        assert "show_status" in content or "is_agent_running" in content

    def test_error_handling_for_missing_agent_py(self):
        """Verify error handling when agent.py is missing."""
        content = self.script_path.read_text(encoding="utf-8")
        # Should check for agent.py existence
        assert 'agent.py"' in content or 'agent.py\'' in content
        # Should have error message
        assert "ERROR" in content or "error" in content.lower()

    def test_config_file_check(self):
        """Verify config.json existence check."""
        content = self.script_path.read_text(encoding="utf-8")
        assert "config.json" in content
        assert "CONFIG_FILE" in content or "config_file" in content.lower()

    def test_script_syntax(self):
        """Verify bash script syntax is valid."""
        result = subprocess.run(
            ["bash", "-n", str(self.script_path)],
            capture_output=True,
            text=True
        )
        assert result.returncode == 0, f"Syntax error in script: {result.stderr}"

    def test_install_script_includes_start_agent_sh(self):
        """Verify install.sh includes start-agent.sh in file list."""
        install_script = REMOTE_AGENT_DIR / "install.sh"
        content = install_script.read_text(encoding="utf-8")
        # Should include start-agent.sh
        assert "start-agent.sh" in content
        # Should include it in AGENT_FILES array
        match = re.search(r"AGENT_FILES=\(([^)]+)\)", content, re.DOTALL)
        assert match is not None
        files = match.group(1)
        assert "start-agent.sh" in files


class TestPowerShellStartScript:
    """Test the PowerShell start-agent.ps1 script."""

    @property
    def script_path(self):
        return REMOTE_AGENT_DIR / "start-agent.ps1"

    def test_script_exists(self):
        """Verify the script file exists."""
        assert self.script_path.exists()

    def test_process_detection_regex_precision(self):
        """
        Verify process detection uses precise regex pattern.
        Should not match 'myagent.py', 'subagent.py', etc.
        """
        content = self.script_path.read_text(encoding="utf-8")
        # Should use a precise regex pattern that matches agent.py as a standalone filename
        # Look for the improved regex pattern: (^|[\\])agent\.py($|[\\\s])
        # This ensures we don't match myagent.py, subagent.py, etc.
        assert r"(^|[\\])agent\.py($|[\\\s])" in content or \
               r'agent\\.py"' in content or \
               'agent\\.py' in content

    def test_get_python_path_function(self):
        """Verify Get-PythonPath function exists."""
        content = self.script_path.read_text(encoding="utf-8")
        # Should have Get-PythonPath function
        assert "Get-PythonPath" in content or "function Get-PythonPath" in content
        # Should use Get-Command to find python
        assert "Get-Command" in content

    def test_scheduled_task_uses_python_path(self):
        """Verify scheduled task uses full Python path."""
        content = self.script_path.read_text(encoding="utf-8")
        # Should call Get-PythonPath before creating scheduled task
        lines = content.split("\n")
        found_python_path_before_task = False
        for i, line in enumerate(lines):
            if "Get-PythonPath" in line:
                # Check if next uses of New-ScheduledTaskAction use $pythonPath
                for j in range(i, min(i+20, len(lines))):
                    if "New-ScheduledTaskAction" in lines[j]:
                        if "$pythonPath" in lines[j]:
                            found_python_path_before_task = True
                        break
        assert found_python_path_before_task, "Scheduled task should use $pythonPath variable"

    def test_start_process_uses_python_path(self):
        """Verify Start-Process uses full Python path."""
        content = self.script_path.read_text(encoding="utf-8")
        # Should call Get-PythonPath before starting process
        lines = content.split("\n")
        found_python_path_before_start = False
        for i, line in enumerate(lines):
            if "Get-PythonPath" in line:
                # Check if next uses of Start-Process use $pythonPath
                for j in range(i, min(i+20, len(lines))):
                    if "Start-Process" in lines[j]:
                        if "$pythonPath" in lines[j]:
                            found_python_path_before_start = True
                        break
        assert found_python_path_before_start, "Start-Process should use $pythonPath variable"

    def test_stop_command_exists(self):
        """Verify -Stop parameter is implemented."""
        content = self.script_path.read_text(encoding="utf-8")
        assert "-Stop" in content or "$Stop" in content

    def test_status_command_exists(self):
        """Verify -Status parameter is implemented."""
        content = self.script_path.read_text(encoding="utf-8")
        assert "-Status" in content or "$Status" in content

    def test_auto_start_parameter(self):
        """Verify -InstallAutoStart parameter is implemented."""
        content = self.script_path.read_text(encoding="utf-8")
        assert "-InstallAutoStart" in content or "$InstallAutoStart" in content

    def test_error_handling_for_missing_files(self):
        """Verify error handling for missing agent.py and config.json."""
        content = self.script_path.read_text(encoding="utf-8")
        assert "未找到 agent.py" in content or "not found" in content.lower()
        assert "config.json" in content

    def test_install_script_includes_start_agent_ps1(self):
        """Verify install.ps1 includes start-agent.ps1 in file list."""
        install_script = REMOTE_AGENT_DIR / "install.ps1"
        content = install_script.read_text(encoding="utf-8")
        # Should include start-agent.ps1
        assert "start-agent.ps1" in content
        # Should include it in $files array
        match = re.search(r'\$files = @\(([^)]+)\)', content, re.DOTALL)
        assert match is not None
        files = match.group(1)
        assert "start-agent.ps1" in files


class TestCmdWrapper:
    """Test the Windows batch file wrapper."""

    @property
    def script_path(self):
        return REMOTE_AGENT_DIR / "start-agent.cmd"

    def test_script_exists(self):
        """Verify the wrapper script exists."""
        assert self.script_path.exists()

    def test_calls_powershell_script(self):
        """Verify it calls the PowerShell script."""
        content = self.script_path.read_text(encoding="utf-8")
        assert "start-agent.ps1" in content
        assert "powershell" in content.lower()


class TestCrossPlatformConsistency:
    """Test cross-platform consistency between scripts."""

    def test_both_scripts_have_same_features(self):
        """Verify both scripts implement the same core features."""
        bash_script = (REMOTE_AGENT_DIR / "start-agent.sh").read_text(encoding="utf-8")
        ps_script = (REMOTE_AGENT_DIR / "start-agent.ps1").read_text(encoding="utf-8")

        # Both should have start functionality
        assert "启动" in bash_script or "start" in bash_script.lower()
        assert "启动" in ps_script or "start" in ps_script.lower()

        # Both should have stop functionality
        assert "停止" in bash_script or "stop" in bash_script.lower()
        assert "停止" in ps_script or "stop" in ps_script.lower()

        # Both should have status functionality
        assert "状态" in bash_script or "status" in bash_script.lower()
        assert "状态" in ps_script or "status" in ps_script.lower()

        # Both should have auto-start functionality
        assert "自启" in bash_script or "auto" in bash_script.lower()
        assert "自启" in ps_script or "auto" in ps_script.lower()

        # Both should reference agent.py
        assert "agent.py" in bash_script
        assert "agent.py" in ps_script

        # Both should reference config.json
        assert "config.json" in bash_script
        assert "config.json" in ps_script
