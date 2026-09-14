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


def _qwen_stack_functions() -> str:
    """Extract the Node-gate + pinned-install functions verbatim from the
    package-method installer for sandboxed execution tests."""
    script = (
        REPO_ROOT / "scripts" / "install-central" / "package-method" / "install.sh"
    ).read_text(encoding="utf-8")
    start = script.index("QWEBUI_VERSION=")
    end = script.index("create_webui_symlinks() {")
    return script[start:end]


def _run_qwen_stack(tmp_path, node_version: str | None, qwen_version: str | None):
    """Execute install_qwen_stack() from the installer under a fake PATH.

    npm is a recorder; node/qwen/qwen-code-webui are shims. No dnf/yum/apt on
    PATH, so a Node upgrade can never succeed in the sandbox — exactly the
    "npm present, Node 20, webui missing" host the PR #3386 review found
    bypassing the gate.
    """
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(parents=True)
    for tool in ("sed", "cut", "grep"):
        os.symlink(_which(tool), fake_bin / tool)
    npm_log = tmp_path / "npm.log"

    def shim(name: str, body: str) -> None:
        (fake_bin / name).write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
        (fake_bin / name).chmod(0o755)

    if node_version is not None:
        shim("node", f"echo '{node_version}'")
    shim("npm", f'echo "$@" >> "{npm_log}"')
    if qwen_version is not None:
        shim("qwen", f"echo '{qwen_version}'")
    shim("qwen-code-webui", "exit 0")

    harness = (
        "print_info() { :; }\nprint_success() { :; }\nprint_warning() { :; }\n"
        "print_error() { :; }\n" + _qwen_stack_functions() + "\ninstall_qwen_stack\n"
    )
    return (
        subprocess.run(
            ["/bin/bash", "-c", harness],
            env={"PATH": str(fake_bin), "NPM_LOG": str(npm_log)},
            text=True,
            capture_output=True,
            check=False,
        ),
        npm_log,
    )


def test_qwen_stack_gates_node20_before_any_npm_install(tmp_path):
    """Regression (PR #3386 review): a host with npm present and Node 20 must
    not reach `npm install` at all (npm exits 0 on a mere EBADENGINE warning
    and used to install an unsupported combination)."""
    result, npm_log = _run_qwen_stack(tmp_path, node_version="v20.19.1", qwen_version="0.23.3")

    assert result.returncode == 1
    # npm was never invoked (recorder log absent or empty)
    assert not npm_log.exists() or npm_log.read_text(encoding="utf-8") == ""


def test_qwen_stack_installs_pinned_versions_on_node22(tmp_path):
    result, npm_log = _run_qwen_stack(tmp_path, node_version="v22.22.3", qwen_version="0.23.3")

    assert result.returncode == 0
    assert npm_log.read_text(encoding="utf-8").splitlines() == [
        "install -g qwen-code-webui@0.2.43",
        "install -g @qwen-code/qwen-code@0.23.3",
    ]


def test_qwen_stack_verifies_installed_cli_version(tmp_path):
    """The pinned install must verify, not trust npm's exit code: a stale
    qwen on PATH (0.15.10) fails the stack check."""
    result, npm_log = _run_qwen_stack(tmp_path, node_version="v22.22.3", qwen_version="0.15.10")

    assert result.returncode == 1


def test_package_installer_runs_gated_stack_before_fresh_and_upgrade_split():
    """Contract: the gated pinned install runs for BOTH fresh installs and
    upgrades (existing old webui/CLI must also reach the pinned versions)."""
    pkg = (REPO_ROOT / "scripts" / "install-central" / "package-method" / "install.sh").read_text(
        encoding="utf-8"
    )
    gate_pos = pkg.index("if ! install_qwen_stack; then")
    split_pos = pkg.index('if [ "$DO_UPGRADE" != "yes" ]; then\n        setup_postgresql')
    assert gate_pos < split_pos


def _run_remote_qwen_stack(tmp_path, node_version: str | None, qwen_version: str | None):
    """Execute ensure_qwen_stack_remote() with a fake ssh that simulates the
    remote host by running the transported script in the same sandbox (fake
    node/npm recorder/qwen shim/sudo pass-through)."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(parents=True)
    for tool in ("sed", "cut", "grep"):
        os.symlink(_which(tool), fake_bin / tool)
    npm_log = tmp_path / "npm.log"
    ssh_log = tmp_path / "ssh.log"

    def shim(name: str, body: str) -> None:
        (fake_bin / name).write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
        (fake_bin / name).chmod(0o755)

    if node_version is not None:
        shim("node", f"echo '{node_version}'")
    shim("npm", f'echo "$@" >> "{npm_log}"')
    if qwen_version is not None:
        shim("qwen", f"echo '{qwen_version}'")
    shim("qwen-code-webui", "exit 0")
    shim("sudo", 'exec "$@"')
    # fake ssh: log the invocation, then run the transported script locally
    # (simulating the remote shell with the same fake PATH)
    shim(
        "ssh",
        f'echo "$@" >> "{ssh_log}"\nexec /bin/sh -c "$2"',
    )

    harness = (
        "print_info() { :; }\nprint_success() { :; }\nprint_warning() { :; }\n"
        "print_error() { :; }\n"
        + _qwen_stack_functions()
        + '\nensure_qwen_stack_remote "deploy-user@remote-host"\n'
    )
    return (
        subprocess.run(
            ["/bin/bash", "-c", harness],
            env={"PATH": str(fake_bin), "NPM_LOG": str(npm_log)},
            text=True,
            capture_output=True,
            check=False,
        ),
        npm_log,
    )


def test_deploy_ensures_pinned_stack_on_remote(tmp_path):
    """Regression (PR #3386 review): SSH deploys must install and verify the
    pinned qwen stack on the remote host, not just copy new app code."""
    result, npm_log = _run_remote_qwen_stack(
        tmp_path, node_version="v20.19.1", qwen_version="0.23.3"
    )
    # Node 20 on the simulated remote with no supported package manager:
    # the remote script must refuse (exit 1) BEFORE any npm install.
    assert result.returncode == 1
    assert not npm_log.exists() or npm_log.read_text(encoding="utf-8") == ""


def test_deploy_remote_node22_installs_pinned_versions(tmp_path):
    result, npm_log = _run_remote_qwen_stack(
        tmp_path, node_version="v22.22.3", qwen_version="0.23.3"
    )

    assert result.returncode == 0
    assert npm_log.read_text(encoding="utf-8").splitlines() == [
        "install -g qwen-code-webui@0.2.43",
        "install -g @qwen-code/qwen-code@0.23.3",
    ]


def test_deploy_remote_version_mismatch_fails(tmp_path):
    result, npm_log = _run_remote_qwen_stack(
        tmp_path, node_version="v22.22.3", qwen_version="0.15.10"
    )

    assert result.returncode == 1


def test_deploy_wires_stack_check_before_fresh_upgrade_split():
    """Contract: install_deploy runs the remote stack check before dispatching
    to do_upgrade_remote / do_fresh_install_remote."""
    pkg = (REPO_ROOT / "scripts" / "install-central" / "package-method" / "install.sh").read_text(
        encoding="utf-8"
    )
    deploy_pos = pkg.index('ensure_qwen_stack_remote "$remote"')
    split_pos = pkg.index('do_upgrade_remote "$remote"')
    assert deploy_pos < split_pos
