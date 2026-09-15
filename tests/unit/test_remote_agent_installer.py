import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest

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
    for tool in ("sed", "cut", "grep", "head", "tr"):
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
    for tool in ("sed", "cut", "grep", "head", "tr"):
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


def _run_remote_qwen_stack(
    tmp_path,
    node_version: str | None,
    qwen_version: str | None,
    *,
    uid: str = "1000",
    with_sudo: bool = False,
    prefix_writable: bool = True,
):
    """Execute ensure_qwen_stack_remote() with a fake ssh simulating the
    remote host: it logs the invocation and runs the transported script
    locally under /bin/bash with the same sandbox PATH.

    uid/with_sudo/prefix_writable control the remote privilege landscape the
    PR #3386 review asked to cover: root without sudo, user-scoped npm
    (writable prefix, no sudo), and system prefix with passwordless sudo.
    """
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(parents=True)
    for tool in ("sed", "cut", "grep", "true", "head", "tr"):
        os.symlink(_which(tool), fake_bin / tool)
    npm_log = tmp_path / "npm.log"
    npm_prefix = tmp_path / ("npm-prefix-writable" if prefix_writable else "npm-prefix-ro")
    npm_prefix.mkdir(parents=True)
    if not prefix_writable:
        npm_prefix.chmod(0o500)

    def shim(name: str, body: str) -> None:
        (fake_bin / name).write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
        (fake_bin / name).chmod(0o755)

    if node_version is not None:
        shim("node", f"echo '{node_version}'")
    shim("id", f"echo '{uid}'")
    # npm: 'config get prefix' answers the prefix; installs are recorded
    shim(
        "npm",
        f'if [ "$1" = "config" ]; then echo "{npm_prefix}"; exit 0; fi\n'
        f'echo "$@" >> "{npm_log}"',
    )
    if qwen_version is not None:
        shim("qwen", f"echo '{qwen_version}'")
    shim("qwen-code-webui", "exit 0")
    if with_sudo:
        # passwordless sudo: '-n ...' drops the flag and passes through
        shim("sudo", 'if [ "$1" = "-n" ]; then shift; fi\nexec "$@"')
    # fake ssh: log args, drop the remote spec, exec the rest (bash -s -- args)
    ssh_log = tmp_path / "ssh.log"
    shim(
        "ssh",
        f'echo "$@" >> "{ssh_log}"\nshift\n'
        'if [ "$1" = "bash" ]; then shift; exec /bin/bash "$@"; fi\n'
        'exec "$@"',
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


def test_deploy_remote_node20_without_upgrade_route_refuses(tmp_path):
    """Node 20 on the simulated remote, non-root without passwordless sudo
    and no supported package manager: refuse BEFORE any npm install."""
    result, npm_log = _run_remote_qwen_stack(
        tmp_path, node_version="v20.19.1", qwen_version="0.23.3"
    )

    assert result.returncode == 1
    assert not npm_log.exists() or npm_log.read_text(encoding="utf-8") == ""


def test_deploy_remote_root_without_sudo_installs_directly(tmp_path):
    """Root login on a sudo-less minimal system must work (PR #3386 review)."""
    result, npm_log = _run_remote_qwen_stack(
        tmp_path,
        node_version="v22.22.3",
        qwen_version="0.23.3",
        uid="0",
        with_sudo=False,
    )

    assert result.returncode == 0
    assert npm_log.read_text(encoding="utf-8").splitlines() == [
        "install -g qwen-code-webui@0.2.43",
        "install -g @qwen-code/qwen-code@0.23.3",
    ]


def test_deploy_remote_user_scoped_npm_needs_no_sudo(tmp_path):
    """Non-root with a writable (nvm-style) npm prefix installs directly."""
    result, npm_log = _run_remote_qwen_stack(
        tmp_path,
        node_version="v22.22.3",
        qwen_version="0.23.3",
        uid="1000",
        with_sudo=False,
        prefix_writable=True,
    )

    assert result.returncode == 0
    assert npm_log.read_text(encoding="utf-8").splitlines() == [
        "install -g qwen-code-webui@0.2.43",
        "install -g @qwen-code/qwen-code@0.23.3",
    ]


def test_deploy_remote_unwritable_prefix_without_sudo_refuses(tmp_path):
    """Non-root, system (unwritable) npm prefix, no passwordless sudo:
    refuse instead of sudo-invoking npm that cannot work."""
    result, npm_log = _run_remote_qwen_stack(
        tmp_path,
        node_version="v22.22.3",
        qwen_version="0.23.3",
        uid="1000",
        with_sudo=False,
        prefix_writable=False,
    )

    assert result.returncode == 1
    assert not npm_log.exists() or npm_log.read_text(encoding="utf-8") == ""


def test_deploy_remote_system_prefix_uses_passwordless_sudo(tmp_path):
    result, npm_log = _run_remote_qwen_stack(
        tmp_path,
        node_version="v22.22.3",
        qwen_version="0.23.3",
        uid="1000",
        with_sudo=True,
        prefix_writable=False,
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


def test_maybe_install_skips_for_api_only_deployment(tmp_path):
    """WORKSPACE disabled via --config and no existing qwen stack: the
    installer must not require Node/npm at all (PR #3386 review)."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(parents=True)
    npm_log = tmp_path / "npm.log"

    def shim(name: str, body: str) -> None:
        (fake_bin / name).write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
        (fake_bin / name).chmod(0o755)

    shim("npm", f'echo "$@" >> "{npm_log}"')

    harness = (
        "print_info() { :; }\nprint_success() { :; }\nprint_warning() { :; }\n"
        "print_error() { :; }\n"
        "WORKSPACE_ENABLED=false\nWORKSPACE_MULTI_USER_MODE=false\n"
        + _qwen_stack_functions()
        + "\nmaybe_install_qwen_stack\n"
    )
    result = subprocess.run(
        ["/bin/bash", "-c", harness],
        env={"PATH": str(fake_bin)},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert not npm_log.exists() or npm_log.read_text(encoding="utf-8") == ""


def test_maybe_install_proceeds_when_workspace_enabled(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(parents=True)
    for tool in ("sed", "cut", "grep", "head", "tr"):
        os.symlink(_which(tool), fake_bin / tool)
    npm_log = tmp_path / "npm.log"
    npm_prefix = tmp_path / "prefix"
    npm_prefix.mkdir()

    def shim(name: str, body: str) -> None:
        (fake_bin / name).write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
        (fake_bin / name).chmod(0o755)

    shim("node", "echo 'v22.22.3'")
    shim(
        "npm",
        f'if [ "$1" = "config" ]; then echo "{npm_prefix}"; exit 0; fi\n'
        f'echo "$@" >> "{npm_log}"',
    )
    shim("qwen", "echo '0.23.3'")
    shim("qwen-code-webui", "exit 0")

    harness = (
        "print_info() { :; }\nprint_success() { :; }\nprint_warning() { :; }\n"
        "print_error() { :; }\n"
        "WORKSPACE_ENABLED=true\nWORKSPACE_MULTI_USER_MODE=true\n"
        + _qwen_stack_functions()
        + "\nmaybe_install_qwen_stack\n"
    )
    result = subprocess.run(
        ["/bin/bash", "-c", harness],
        env={"PATH": str(fake_bin)},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert npm_log.read_text(encoding="utf-8").splitlines() == [
        "install -g qwen-code-webui@0.2.43",
        "install -g @qwen-code/qwen-code@0.23.3",
    ]


def test_deploy_wires_stack_check_before_fresh_upgrade_split():
    """Contract: install_deploy runs the remote stack check before dispatching
    to do_upgrade_remote / do_fresh_install_remote."""
    pkg = (REPO_ROOT / "scripts" / "install-central" / "package-method" / "install.sh").read_text(
        encoding="utf-8"
    )
    deploy_pos = pkg.index('ensure_qwen_stack_remote "$remote"')
    split_pos = pkg.index('do_upgrade_remote "$remote"')
    assert deploy_pos < split_pos


def _agent_qwen_case_body() -> str:
    """Extract the qwen-code-cli case body verbatim from the agent installer."""
    script = (REPO_ROOT / "remote-agent" / "install.sh").read_text(encoding="utf-8")
    match = re.search(
        r'case "\$INSTALL_CLI" in\n            qwen-code-cli\)\n(.*?)\n                ;;',
        script,
        re.DOTALL,
    )
    assert match is not None, "qwen-code-cli case not found in agent installer"
    return match.group(1)


def _run_agent_qwen_case(tmp_path, npm_exit: int, qwen_version: str | None):
    fake_bin = tmp_path / "agent-bin"
    fake_bin.mkdir(parents=True)
    for tool in ("head", "tr"):
        os.symlink(_which(tool), fake_bin / tool)

    npm_log = tmp_path / "agent-npm.log"

    def shim(name: str, body: str) -> None:
        (fake_bin / name).write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
        (fake_bin / name).chmod(0o755)

    shim("npm", f'echo "$@" >> "{npm_log}"; exit {npm_exit}')
    shim("node", "echo 'v22.22.3'")
    if qwen_version is not None:
        shim("qwen", f"echo '{qwen_version}'")

    harness = (
        "log_info() { :; }\nlog_success() { :; }\nlog_warn() { :; }\n"
        "log_error() { :; }\nget_node_major() { echo 22; }\n"
        "QWEN_CLI_VERSION=0.23.3\n"
        "INSTALL_CLI=qwen-code-cli\n"
        'case "$INSTALL_CLI" in\n            qwen-code-cli)\n'
        + _agent_qwen_case_body()
        + "\n                ;;\nesac\n"
        "echo CASE_COMPLETED\n"
    )
    result = subprocess.run(
        ["/bin/bash", "-c", harness],
        env={"PATH": str(fake_bin), "NPM_LOG": str(npm_log)},
        text=True,
        capture_output=True,
        check=False,
    )
    return result, npm_log


def test_agent_installer_qwen_npm_failure_is_fatal(tmp_path):
    """PR #3386 review: npm failing for the requested qwen CLI must abort the
    install (the machine config declares cli_tool=qwen-code-cli), not warn
    and register an agent whose default CLI cannot start."""
    result, npm_log = _run_agent_qwen_case(tmp_path, npm_exit=1, qwen_version="0.23.3")

    assert result.returncode == 1
    assert "CASE_COMPLETED" not in result.stdout


def test_agent_installer_qwen_version_verification_is_fatal(tmp_path):
    result, npm_log = _run_agent_qwen_case(tmp_path, npm_exit=0, qwen_version=None)

    assert result.returncode == 1
    assert "CASE_COMPLETED" not in result.stdout


def test_agent_installer_qwen_happy_path_completes(tmp_path):
    result, npm_log = _run_agent_qwen_case(tmp_path, npm_exit=0, qwen_version="0.23.3")

    assert result.returncode == 0
    assert "CASE_COMPLETED" in result.stdout
    # the install must be PINNED, not @latest (PR #3386 review)
    assert npm_log.read_text(encoding="utf-8").strip() == ("install -g @qwen-code/qwen-code@0.23.3")


def test_deploy_upgrade_api_only_remote_config_skips_stack(tmp_path):
    """Regression (PR #3386 review): an interactively confirmed remote UPGRADE
    of an existing API-only deployment (workspace flags false in the REMOTE
    config.json, no qwen stack on the remote) must skip the stack gate —
    interactive_config only reads the LOCAL config, so the flags would
    otherwise still hold their `true` defaults."""
    home = tmp_path / "remote-home"
    (home / ".open-ace").mkdir(parents=True)
    (home / ".open-ace" / "config.json").write_text(
        '{"workspace": {"enabled": false, "multi_user_mode": false}}',
        encoding="utf-8",
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    for tool in ("sed", "cut", "grep", "true", "python3", "head", "tr"):
        os.symlink(_which(tool), fake_bin / tool)
    ssh_log = tmp_path / "ssh.log"
    npm_log = tmp_path / "npm.log"

    def shim(name: str, body: str) -> None:
        (fake_bin / name).write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
        (fake_bin / name).chmod(0o755)

    shim("npm", f'echo "$@" >> "{npm_log}"')
    # smart fake ssh: log; drop remote spec; bash -s [args] runs with stdin,
    # plain command strings run via sh -c
    shim(
        "ssh",
        f'echo "$@" >> "{ssh_log}"\nshift\n'
        'if [ "$1" = "bash" ]; then shift; exec /bin/bash "$@"; fi\n'
        'exec /bin/sh -c "$1"',
    )

    harness = (
        "print_info() { :; }\nprint_success() { :; }\nprint_warning() { :; }\n"
        "print_error() { :; }\n"
        # defaults, as they would be after interactive_config on the LOCAL side
        "WORKSPACE_ENABLED=true\nWORKSPACE_MULTI_USER_MODE=true\n"
        + _qwen_stack_functions()
        + '\nmaybe_install_qwen_stack_remote "deploy-user@remote-host"\n'
    )
    result = subprocess.run(
        ["/bin/bash", "-c", harness],
        env={"PATH": str(fake_bin), "HOME": str(home)},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    ssh_calls = ssh_log.read_text(encoding="utf-8")
    # flags were read from the remote config (python3 -c one-liner) ...
    assert "python3 -c" in ssh_calls
    assert "~/.open-ace/config.json" in ssh_calls
    # ... the stack-probe ran ...
    assert "command -v qwen-code-webui" in ssh_calls
    # ... but the pinned install (bash -s -- <versions>) was never invoked
    assert "-- 0.2.43" not in ssh_calls
    assert not npm_log.exists() or npm_log.read_text(encoding="utf-8") == ""


def _ps1_qwen_case_body() -> str:
    """Extract the qwen-code-cli switch block verbatim from install.ps1."""
    script = (REPO_ROOT / "remote-agent" / "install.ps1").read_text(encoding="utf-8")
    match = re.search(r'"qwen-code-cli" \{\n(.*?)\n            "claude-code" \{', script, re.DOTALL)
    assert match is not None, "qwen-code-cli block not found in install.ps1"
    return match.group(1)


def _run_ps1_qwen_case(tmp_path, npm_exit: int, with_qwen: bool):
    """Execute the extracted PowerShell qwen block under pwsh with a fake
    PATH (npm/node shims, qwen optionally absent)."""
    import shutil

    fake_bin = tmp_path / "ps-bin"
    fake_bin.mkdir(parents=True)

    def shim(name: str, body: str) -> None:
        (fake_bin / name).write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
        (fake_bin / name).chmod(0o755)

    shim("npm", f"echo npm-output; exit {npm_exit}")
    shim("node", "echo 'v22.22.3'")
    if with_qwen:
        shim("qwen", "echo '0.23.3'")

    harness = (
        "$prevErrorAction = $ErrorActionPreference\n"
        "$ErrorActionPreference = 'Continue'\n"
        "$QwenCliVersion = '0.23.3'\n"
        "switch ('qwen-code-cli') {\n"
        # the extracted body already closes the qwen case block; the wrapper
        # opens the switch and closes it ONCE (an extra brace makes the whole
        # harness a parse error, and pwsh then exits 1 for the wrong reason).
        '"qwen-code-cli" {\n' + _ps1_qwen_case_body() + "\n}\n"
        "Write-Output 'CASE_COMPLETED'\n"
    )
    harness_file = tmp_path / "harness.ps1"
    harness_file.write_text(harness, encoding="utf-8")

    env = dict(os.environ)
    # prepend (not replace): pwsh itself must stay resolvable
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    return subprocess.run(
        ["pwsh", "-NoProfile", "-File", str(harness_file)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


# GitHub-hosted runners ship pwsh on Linux; dev machines may not have it.
_pwsh_available = None


def _pwsh_exists() -> bool:
    global _pwsh_available
    if _pwsh_available is None:
        import shutil

        _pwsh_available = shutil.which("pwsh") is not None
    return _pwsh_available


@pytest.mark.skipif(not _pwsh_exists(), reason="pwsh not installed locally; runs on CI runners")
def test_ps1_qwen_missing_after_install_is_fatal(tmp_path):
    """PR #3386 review: npm exit 0 + qwen NOT on PATH must fail closed —
    $LASTEXITCODE keeps the stale npm code when the invocation runs no
    native program, and 2>&1 binds a truthy ErrorRecord, so the naive
    check printed success and registered a broken agent."""
    result = _run_ps1_qwen_case(tmp_path, npm_exit=0, with_qwen=False)

    assert result.returncode == 1
    assert "CASE_COMPLETED" not in result.stdout


@pytest.mark.skipif(not _pwsh_exists(), reason="pwsh not installed locally; runs on CI runners")
def test_ps1_qwen_npm_failure_is_fatal(tmp_path):
    result = _run_ps1_qwen_case(tmp_path, npm_exit=1, with_qwen=True)

    assert result.returncode == 1
    assert "CASE_COMPLETED" not in result.stdout


@pytest.mark.skipif(not _pwsh_exists(), reason="pwsh not installed locally; runs on CI runners")
def test_ps1_qwen_happy_path_completes(tmp_path):
    result = _run_ps1_qwen_case(tmp_path, npm_exit=0, with_qwen=True)

    assert result.returncode == 0
    assert "CASE_COMPLETED" in result.stdout


def test_local_upgrade_read_defaults_missing_keys_to_false(tmp_path):
    """PR #3386 review: the LOCAL upgrade config read must follow the runtime
    default (WorkspaceConfig false) for missing workspace keys — executed
    against the exact python snippet shipped in the installer."""
    script = (
        REPO_ROOT / "scripts" / "install-central" / "package-method" / "install.sh"
    ).read_text(encoding="utf-8")
    match = re.search(
        r"python3 -c \"import json; c=json\.load\(open\('\$config_file'\)\); "
        r"print\(c\.get\('workspace', \{\}\)\.get\('enabled', '(\w+)'\)\)\"",
        script,
    )
    assert match is not None, "local workspace read snippet not found"
    default_value = match.group(1)
    assert (
        default_value == "false"
    ), f"missing-key default is {default_value!r}, must mirror the runtime default 'false'"

    cfg = tmp_path / "config.json"
    cfg.write_text('{"database": {"url": "sqlite:///x.db"}}', encoding="utf-8")
    snippet = (
        f"import json; c=json.load(open('{cfg}')); "
        "print(c.get('workspace', {}).get('enabled', 'false'))"
    )
    result = subprocess.run(["python3", "-c", snippet], capture_output=True, text=True, check=True)
    assert result.stdout.strip() == "false"


def test_deploy_upgrade_key_missing_remote_config_skips_stack(tmp_path):
    """Pre-workspace-era remote config (no workspace keys) + no qwen stack:
    the remote UPGRADE must skip the stack gate (runtime default false)."""
    home = tmp_path / "remote-home2"
    (home / ".open-ace").mkdir(parents=True)
    (home / ".open-ace" / "config.json").write_text(
        '{"database": {"url": "sqlite://x.db"}}', encoding="utf-8"
    )
    result, npm_log, ssh_calls = _run_api_only_remote(tmp_path, home)

    assert result.returncode == 0
    assert "-- 0.2.43" not in ssh_calls
    assert not npm_log.exists() or npm_log.read_text(encoding="utf-8") == ""


def _run_api_only_remote(tmp_path, home):
    fake_bin = tmp_path / "bin2"
    fake_bin.mkdir(parents=True)
    for tool in ("sed", "cut", "grep", "true", "python3", "head", "tr"):
        os.symlink(_which(tool), fake_bin / tool)
    ssh_log = tmp_path / "ssh2.log"
    npm_log = tmp_path / "npm2.log"

    def shim(name: str, body: str) -> None:
        (fake_bin / name).write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
        (fake_bin / name).chmod(0o755)

    shim("npm", f'echo "$@" >> "{npm_log}"')
    shim(
        "ssh",
        f'echo "$@" >> "{ssh_log}"\nshift\n'
        'if [ "$1" = "bash" ]; then shift; exec /bin/bash "$@"; fi\n'
        'exec /bin/sh -c "$1"',
    )
    harness = (
        "print_info() { :; }\nprint_success() { :; }\nprint_warning() { :; }\n"
        "print_error() { :; }\n"
        "WORKSPACE_ENABLED=true\nWORKSPACE_MULTI_USER_MODE=true\n"
        + _qwen_stack_functions()
        + '\nmaybe_install_qwen_stack_remote "u@h"\n'
    )
    result = subprocess.run(
        ["/bin/bash", "-c", harness],
        env={"PATH": str(fake_bin), "HOME": str(home)},
        text=True,
        capture_output=True,
        check=False,
    )
    return result, npm_log, ssh_log.read_text(encoding="utf-8")


def test_local_stack_rejects_similar_version_0_23_30(tmp_path):
    """Exact-match verification: 0.23.30 must NOT satisfy a 0.23.3 pin."""
    result, npm_log = _run_qwen_stack(tmp_path, node_version="v22.22.3", qwen_version="0.23.30")

    assert result.returncode == 1


def test_remote_stack_rejects_similar_version_0_23_30(tmp_path):
    result, npm_log = _run_remote_qwen_stack(
        tmp_path, node_version="v22.22.3", qwen_version="0.23.30"
    )

    assert result.returncode == 1


def test_agent_case_rejects_similar_version_0_23_30(tmp_path):
    result, npm_log = _run_agent_qwen_case(tmp_path, npm_exit=0, qwen_version="0.23.30")

    assert result.returncode == 1
    assert "CASE_COMPLETED" not in result.stdout


def _run_qwen_precheck(tmp_path, node_version: str | None, qwen_version: str | None):
    """Execute terminal_menu.qwen_precheck() under a fake PATH."""
    fake_bin = tmp_path / "tm-bin"
    fake_bin.mkdir(parents=True, exist_ok=True)

    def shim(name: str, body: str) -> None:
        (fake_bin / name).write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
        (fake_bin / name).chmod(0o755)

    if node_version is not None:
        shim("node", f"echo '{node_version}'")
    if qwen_version is not None:
        shim("qwen", f"echo '{qwen_version}'")

    env = dict(os.environ)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    code = (
        "import sys, json\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "import terminal_menu\n"
        "print(json.dumps(terminal_menu.qwen_precheck()))\n"
    )
    return subprocess.run(
        [sys.executable, "-c", code, str(REPO_ROOT / "remote-agent")],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_menu_qwen_precheck_refuses_node20_without_qwen(tmp_path):
    """Regression (PR #3386 review): a Claude/Codex-provisioned Node 20 agent
    must NOT reach `npm install`/launch from the menu — npm exits 0 on a mere
    EBADENGINE warning and would start an unsupported combination."""
    import json

    result = _run_qwen_precheck(tmp_path, node_version="v20.19.1", qwen_version=None)
    ok, reason = json.loads(result.stdout)

    assert ok is False
    assert "Node" in reason


def test_menu_qwen_precheck_refuses_old_qwen_on_path(tmp_path):
    import json

    result = _run_qwen_precheck(tmp_path, node_version="v22.22.3", qwen_version="0.15.10")
    ok, reason = json.loads(result.stdout)

    assert ok is False
    assert "0.15.10" in reason


def test_menu_qwen_precheck_rejects_similar_version(tmp_path):
    import json

    result = _run_qwen_precheck(tmp_path, node_version="v22.22.3", qwen_version="0.23.30")
    ok, reason = json.loads(result.stdout)

    assert ok is False


def test_menu_qwen_precheck_accepts_pinned_pair(tmp_path):
    import json

    ok_installed = _run_qwen_precheck(tmp_path, "v22.22.3", "0.23.3")
    ok_missing = _run_qwen_precheck(tmp_path, "v22.22.3", None)

    assert json.loads(ok_installed.stdout) == [True, ""]
    assert json.loads(ok_missing.stdout) == [True, ""]


def test_menu_qwen_precheck_is_wired_into_select():
    """Contract: handle_select routes the qwen entry through qwen_precheck
    before either OS install/launch path."""
    src = (REPO_ROOT / "remote-agent" / "terminal_menu.py").read_text(encoding="utf-8")
    select_body = src[src.index("def handle_select") : src.index("def run_windows_menu")]
    assert 'item.get("cli") == "qwen"' in select_body
    assert "qwen_precheck()" in select_body
    # the gate must precede every install/launch path: the first use of
    # install_cmd (Windows & POSIX installs) and of item["cmd"] (launches)
    assert select_body.index("qwen_precheck()") < select_body.index('item["install_cmd"]')
    assert select_body.index("qwen_precheck()") < select_body.index('item["cmd"]')


def test_menu_qwen_precheck_node_probe_timeout_fails_closed(monkeypatch):
    """Regression (PR #3386 review): a hung 'node --version' must fail the
    precheck with a reason instead of crashing the menu — TimeoutExpired is
    not an OSError, so the old except clauses let it escape qwen_precheck()."""
    terminal_menu = _import_terminal_menu()

    def raise_timeout(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=10)

    monkeypatch.setattr(terminal_menu.shutil, "which", lambda name: f"/fake/{name}")
    monkeypatch.setattr(terminal_menu.subprocess, "run", raise_timeout)

    ok, reason = terminal_menu.qwen_precheck()

    assert ok is False
    assert "timed out" in reason


def test_menu_qwen_precheck_qwen_probe_timeout_fails_closed(monkeypatch):
    """Regression (PR #3386 review): a hung/broken qwen wrapper answering
    '--version' must be refused as unknown, never crash the menu and never
    be launched."""
    terminal_menu = _import_terminal_menu()

    def fake_run(cmd, **kwargs):
        if cmd[0] == "node":
            return subprocess.CompletedProcess(cmd, 0, stdout="v22.22.3\n")
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=10)

    monkeypatch.setattr(terminal_menu.shutil, "which", lambda name: f"/fake/{name}")
    monkeypatch.setattr(terminal_menu.subprocess, "run", fake_run)

    ok, reason = terminal_menu.qwen_precheck()

    assert ok is False
    assert "unknown" in reason
    assert "Refusing" in reason


def _import_terminal_menu():
    if str(REPO_ROOT / "remote-agent") not in sys.path:
        sys.path.insert(0, str(REPO_ROOT / "remote-agent"))
    import terminal_menu

    return terminal_menu


def _run_executor_missing_cli(tmp_path, cli_tool):
    """Execute ProcessExecutor.start_session for a missing CLI under a
    controlled PATH/HOME so no real tool can satisfy _find_executable()."""
    home = tmp_path / "home"
    home.mkdir()
    fake_bin = tmp_path / "ex-bin"  # empty: no node/qwen/zcode shadowing
    fake_bin.mkdir()
    env = dict(os.environ)
    env["PATH"] = str(fake_bin)
    env["HOME"] = str(home)
    code = (
        "import sys, json\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "import executor\n"
        "import cli_adapters.zcode as _zc\n"
        # dev machines with the ZCode desktop app would pass check_installed
        # via the bundled engine; force the missing-tool error path
        "_zc._APP_ENGINE = '/nonexistent-zcode-engine'\n"
        "ex = executor.ProcessExecutor(server_url='http://127.0.0.1:1')\n"
        "r = ex.start_session('sess-probe', sys.argv[2], sys.argv[3], 'tok')\n"
        "print(json.dumps(r))\n"
    )
    return subprocess.run(
        [sys.executable, "-c", code, str(REPO_ROOT / "remote-agent"), str(tmp_path), cli_tool],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_executor_qwen_missing_error_includes_node_requirement(tmp_path):
    """Regression (PR #3386 review): qwen-code-cli is NOT in _APPSERVER_TOOLS,
    so the executable-missing error users actually see comes from the generic
    branch — it must carry the Node >= 22 hint because npm exits 0 on a mere
    EBADENGINE warning and manual recovery would otherwise reinstall onto an
    unsupported Node."""
    import json

    result = _run_executor_missing_cli(tmp_path, "qwen-code-cli")
    payload = json.loads(result.stdout)

    assert payload["success"] is False
    assert "npm install -g @qwen-code/qwen-code@0.23.3" in payload["error"]
    assert "Node.js >= 22" in payload["error"]


def test_executor_zcode_missing_error_has_no_hint_padding(tmp_path):
    """The hint lives on BaseCLIAdapter (empty default); ZCode goes through
    _start_zcode_session and must render cleanly without dangling ' ()'."""
    import json

    result = _run_executor_missing_cli(tmp_path, "zcode")
    payload = json.loads(result.stdout)

    assert payload["success"] is False
    assert "not found on this machine" in payload["error"]
    assert " ()" not in payload["error"]
