"""Regression tests for package installer non-systemd auto-start (Issue #2583)."""

from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
INSTALL_SH = PROJECT_ROOT / "scripts" / "install-central" / "package-method" / "install.sh"

pytestmark = [pytest.mark.issue(2583), pytest.mark.regression]


def _installer_text() -> str:
    return INSTALL_SH.read_text(encoding="utf-8")


def test_local_manual_start_groups_pid_write_in_target_directory() -> None:
    """The local PID write must share the target-directory command group."""
    text = _installer_text()

    expected = (
        'su - "$DEPLOY_USER" -c "cd \'$target_path\' && { '
        "AI_TOKEN_WEB_PORT=$port AI_TOKEN_WEB_HOST=$host nohup python3 server.py "
        "> '$target_path/logs/server.log' 2>&1 & "
        "echo \\$! > '$pid_file'; }\""
    )
    assert expected in text


def test_remote_manual_start_groups_pid_write_in_target_directory() -> None:
    """The remote PID write must use the same grouped, absolute-path behavior."""
    text = _installer_text()

    expected = (
        "ssh \"$remote\" \"su - '$DEPLOY_USER' -c \\\"cd '$target_path' && { "
        "AI_TOKEN_WEB_PORT=$port AI_TOKEN_WEB_HOST=$host nohup python3 server.py "
        "> '$target_path/logs/server.log' 2>&1 & "
        "echo \\\\\\$! > '$pid_file'; }\\\"\""
    )
    assert expected in text


def test_manual_start_does_not_write_relative_pid_or_log_paths() -> None:
    """A login shell's working directory must not control log or PID placement."""
    text = _installer_text()

    assert "> logs/server.log" not in text
    assert "> logs/server.pid" not in text
