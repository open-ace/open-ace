import os
import stat
import subprocess
from pathlib import Path


def test_install_script_reports_missing_python(tmp_path):
    """The installer should not silently exit when Python is unavailable."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()

    hostname = fake_bin / "hostname"
    hostname.write_text("#!/bin/sh\necho test-host\n", encoding="utf-8")
    hostname.chmod(hostname.stat().st_mode | stat.S_IXUSR)

    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "remote-agent" / "install.sh"
    env = {
        "HOME": str(tmp_path),
        "PATH": str(fake_bin),
    }

    result = subprocess.run(
        [
            "/bin/bash",
            str(script),
            "--server",
            "http://127.0.0.1:5000",
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
