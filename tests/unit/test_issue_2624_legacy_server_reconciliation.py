"""Regression coverage for package upgrade legacy-server reconciliation."""

from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
INSTALL_SH = PROJECT_ROOT / "scripts" / "install-central" / "package-method" / "install.sh"

pytestmark = [pytest.mark.issue(2624), pytest.mark.regression]


def _installer_text() -> str:
    return INSTALL_SH.read_text(encoding="utf-8")


def test_upgrade_reconciles_legacy_server_before_systemd_restart() -> None:
    text = _installer_text()

    cleanup = text.index('stop_legacy_package_server "$target_path" "$DEPLOY_USER"')
    restart = text.index("systemctl restart open-ace.service", cleanup)

    assert cleanup < restart
    assert 'print_error "Unable to stop the legacy Open ACE server safely"' in text


def test_legacy_pid_is_validated_before_signal() -> None:
    text = _installer_text()
    function = text[text.index("stop_legacy_package_server() {") : text.index("wait_for_openace_service() {")]

    assert '[[ "$legacy_pid" =~ ^[1-9][0-9]*$ ]]' in function
    assert 'process_user=$(ps -o user= -p "$legacy_pid"' in function
    assert 'process_cwd=$(readlink -f "/proc/$legacy_pid/cwd"' in function
    assert "server\\.py" in function
    assert function.index('process_user=$(ps') < function.index('kill "$legacy_pid"')
    assert "pkill" not in function


def test_systemd_main_pid_is_never_signaled_as_legacy() -> None:
    text = _installer_text()
    function = text[text.index("stop_legacy_package_server() {") : text.index("wait_for_openace_service() {")]

    assert "systemctl show open-ace.service -p MainPID --value" in function
    assert '[ "$legacy_pid" = "$service_pid" ]' in function


def test_upgrade_requires_systemd_main_pid_to_own_web_port() -> None:
    text = _installer_text()
    function = text[text.index("wait_for_openace_service() {") : text.index("# Local Installation")]

    assert "systemctl is-active --quiet open-ace.service" in function
    assert "systemctl show open-ace.service -p MainPID --value" in function
    assert 'grep -q "pid=${main_pid},"' in function
    assert 'print_error "open-ace.service did not become ready' in text
