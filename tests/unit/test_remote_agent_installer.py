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


def _node_probe_function() -> str:
    """Extract get_node_major() verbatim from install.sh for strict-mode tests."""
    script = (REPO_ROOT / "remote-agent" / "install.sh").read_text(encoding="utf-8")
    match = re.search(r"^get_node_major\(\) \{.*?^\}", script, re.DOTALL | re.MULTILINE)
    assert match is not None, "get_node_major() not found in install.sh"
    return match.group(0)


def _strict_bash_probe(tmp_path, node_version: str | None):
    """Run get_node_major under `bash -euo pipefail` with a PATH lacking node.

    Mirrors the PR #3386 review repro: install.sh runs with `set -euo
    pipefail`, so an unguarded `node --version | ...` pipeline aborts with
    exit 127 on fresh machines before the NodeSource branch can run.
    """
    fake_bin = tmp_path / "probe-bin"
    fake_bin.mkdir(parents=True)
    for tool in ("sed", "cut"):
        os.symlink(_which(tool), fake_bin / tool)
    if node_version is not None:
        (fake_bin / "node").write_text(f"#!/bin/sh\necho '{node_version}'\n", encoding="utf-8")
        (fake_bin / "node").chmod(0o755)

    return subprocess.run(
        [
            "/bin/bash",
            "-euo",
            "pipefail",
            "-c",
            f"{_node_probe_function()}; get_node_major",
        ],
        env={"PATH": str(fake_bin)},
        text=True,
        capture_output=True,
        check=False,
    )


def _which(tool: str) -> str:
    import shutil

    path = shutil.which(tool)
    assert path is not None, f"{tool} not available for probe test"
    return path


def test_node_probe_survives_missing_node_under_strict_mode(tmp_path):
    """Regression (PR #3386 review): no 127 abort when node/npm are absent."""
    result = _strict_bash_probe(tmp_path, node_version=None)

    assert result.returncode == 0
    assert result.stdout.strip() == "0"


def test_node_probe_reads_major_version(tmp_path):
    result20 = _strict_bash_probe(tmp_path / "v20", node_version="v20.19.1")
    result22 = _strict_bash_probe(tmp_path / "v22", node_version="v22.22.3")

    assert result20.stdout.strip() == "20"
    assert result22.stdout.strip() == "22"


def test_qwen_node_gates_fail_the_install():
    """Regression (PR #3386 review): the Node<22 gates must end the install
    with a failure status, not print errors and continue to register a
    machine whose cli_tool=qwen-code-cli can never run."""
    sh = (REPO_ROOT / "remote-agent" / "install.sh").read_text(encoding="utf-8")
    qwen_case = re.search(r"qwen-code-cli\)(.*?)claude-code\)", sh, re.DOTALL)
    assert qwen_case is not None
    assert "exit 1" in qwen_case.group(1)

    ps1 = (REPO_ROOT / "remote-agent" / "install.ps1").read_text(encoding="utf-8")
    ps1_case = re.search(r'"qwen-code-cli" \{(.*?)"claude-code" \{', ps1, re.DOTALL)
    assert ps1_case is not None
    assert "exit 1" in ps1_case.group(1)

    pkg = (REPO_ROOT / "scripts" / "install-central" / "package-method" / "install.sh").read_text(
        encoding="utf-8"
    )
    assert "return 1" in pkg  # install_webui gate; caller wraps it in `if`
