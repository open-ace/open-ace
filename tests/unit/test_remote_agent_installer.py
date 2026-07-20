import os
import re
import stat
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def runtime_agent_files():
    agent_dir = REPO_ROOT / "remote-agent"
    excluded = {"install.ps1", "install.sh", "uninstall.ps1", "uninstall.sh"}
    return sorted(
        path.name for path in agent_dir.iterdir() if path.is_file() and path.name not in excluded
    )


def test_install_script_reports_missing_python(tmp_path):
    """The installer should not silently exit when Python is unavailable."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()

    hostname = fake_bin / "hostname"
    hostname.write_text("#!/bin/sh\necho test-host\n", encoding="utf-8")
    hostname.chmod(hostname.stat().st_mode | stat.S_IXUSR)

    script = REPO_ROOT / "remote-agent" / "install.sh"
    env = {
        "HOME": str(tmp_path),
        "PATH": str(fake_bin),
    }

    result = subprocess.run(
        [
            "/bin/bash",
            str(script),
            "--server",
            "http://127.0.0.1:19888",
            "--token",
            "test-token",
        ],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "Checking prerequisites" in result.stdout
    assert "Python 3.8+ is not installed" in result.stdout


def test_shell_installer_downloads_all_runtime_agent_files():
    expected = runtime_agent_files()

    script = (REPO_ROOT / "remote-agent" / "install.sh").read_text(encoding="utf-8")
    match = re.search(r"AGENT_FILES=\(\n(?P<files>.*?)\n\)", script, re.DOTALL)

    assert match is not None
    assert sorted(match.group("files").split()) == expected


def test_powershell_installer_downloads_all_runtime_agent_files():
    expected = sorted(name for name in runtime_agent_files() if name != "__init__.py")

    script = (REPO_ROOT / "remote-agent" / "install.ps1").read_text(encoding="utf-8")
    match = re.search(r"\$files = @\((?P<files>.*?)\)", script, re.DOTALL)

    assert match is not None
    assert sorted(re.findall(r'"([^"]+)"', match.group("files"))) == expected


def test_shell_installer_downloads_all_cli_adapters():
    adapter_dir = REPO_ROOT / "remote-agent" / "cli_adapters"
    expected = sorted(path.name for path in adapter_dir.glob("*.py"))

    script = (REPO_ROOT / "remote-agent" / "install.sh").read_text(encoding="utf-8")
    match = re.search(r"for file in (__init__\.py [^;\n]+); do", script)

    assert match is not None
    assert sorted(match.group(1).split()) == expected


def test_powershell_installer_downloads_all_cli_adapters():
    adapter_dir = REPO_ROOT / "remote-agent" / "cli_adapters"
    expected = sorted(path.name for path in adapter_dir.glob("*.py"))

    script = (REPO_ROOT / "remote-agent" / "install.ps1").read_text(encoding="utf-8")
    match = re.search(r"\$adapterFiles = @\(([^)]+)\)", script)

    assert match is not None
    assert sorted(re.findall(r'"([^"]+)"', match.group(1))) == expected


# Issue #1892: SSL configuration tests


def test_shell_installer_has_insecure_option():
    """Verify install.sh has --insecure-skip-tls-verify option (Issue #1892)."""
    script = (REPO_ROOT / "remote-agent" / "install.sh").read_text(encoding="utf-8")

    # Check for the option in argument parsing
    assert "--insecure-skip-tls-verify" in script
    assert "INSECURE_SKIP_TLS_VERIFY" in script


def test_shell_installer_has_ca_bundle_option():
    """Verify install.sh has --ca-bundle option (Issue #1892)."""
    script = (REPO_ROOT / "remote-agent" / "install.sh").read_text(encoding="utf-8")

    # Check for the option in argument parsing
    assert "--ca-bundle" in script
    assert "CA_BUNDLE_PATH" in script


def test_shell_installer_security_warning_for_insecure():
    """Verify install.sh outputs security warning for insecure mode (Issue #1892)."""
    script = (REPO_ROOT / "remote-agent" / "install.sh").read_text(encoding="utf-8")

    # Check for security warning
    assert "[SECURITY WARNING]" in script
    assert "TLS certificate verification will be DISABLED" in script


def test_powershell_installer_has_insecure_option():
    """Verify install.ps1 has -InsecureSkipTlsVerify option (Issue #1892)."""
    script = (REPO_ROOT / "remote-agent" / "install.ps1").read_text(encoding="utf-8")

    # Check for the parameter
    assert "InsecureSkipTlsVerify" in script


def test_powershell_installer_has_ca_bundle_option():
    """Verify install.ps1 has -CaBundlePath option (Issue #1892)."""
    script = (REPO_ROOT / "remote-agent" / "install.ps1").read_text(encoding="utf-8")

    # Check for the parameter
    assert "CaBundlePath" in script


def test_powershell_installer_preserves_ssl_no_revoke():
    """Verify install.ps1 preserves --ssl-no-revoke for Windows (Issue #1892)."""
    script = (REPO_ROOT / "remote-agent" / "install.ps1").read_text(encoding="utf-8")

    # Check that --ssl-no-revoke is still used
    assert "--ssl-no-revoke" in script


def test_shell_installer_ssl_diagnostic_on_failure():
    """Verify install.sh provides SSL diagnostic on registration failure (Issue #1892)."""
    script = (REPO_ROOT / "remote-agent" / "install.sh").read_text(encoding="utf-8")

    # Check for SSL diagnostic hints
    assert "SSL/TLS certificate issue" in script
    assert "--ca-bundle" in script
