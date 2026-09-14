import os
import re
import stat
import subprocess
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
    for tool in ("sed", "cut", "grep", "true"):
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
    for tool in ("sed", "cut", "grep"):
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


def _run_agent_qwen_case(tmp_path, npm_exit: int, with_qwen: bool):
    fake_bin = tmp_path / "agent-bin"
    fake_bin.mkdir(parents=True)

    def shim(name: str, body: str) -> None:
        (fake_bin / name).write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
        (fake_bin / name).chmod(0o755)

    shim("npm", f"exit {npm_exit}")
    shim("node", "echo 'v22.22.3'")
    if with_qwen:
        shim("qwen", "echo '0.23.3'")

    harness = (
        "log_info() { :; }\nlog_success() { :; }\nlog_warn() { :; }\n"
        "log_error() { :; }\nget_node_major() { echo 22; }\n"
        "INSTALL_CLI=qwen-code-cli\n"
        'case "$INSTALL_CLI" in\n            qwen-code-cli)\n'
        + _agent_qwen_case_body()
        + "\n                ;;\nesac\n"
        "echo CASE_COMPLETED\n"
    )
    return subprocess.run(
        ["/bin/bash", "-c", harness],
        env={"PATH": str(fake_bin)},
        text=True,
        capture_output=True,
        check=False,
    )


def test_agent_installer_qwen_npm_failure_is_fatal(tmp_path):
    """PR #3386 review: npm failing for the requested qwen CLI must abort the
    install (the machine config declares cli_tool=qwen-code-cli), not warn
    and register an agent whose default CLI cannot start."""
    result = _run_agent_qwen_case(tmp_path, npm_exit=1, with_qwen=True)

    assert result.returncode == 1
    assert "CASE_COMPLETED" not in result.stdout


def test_agent_installer_qwen_version_verification_is_fatal(tmp_path):
    result = _run_agent_qwen_case(tmp_path, npm_exit=0, with_qwen=False)

    assert result.returncode == 1
    assert "CASE_COMPLETED" not in result.stdout


def test_agent_installer_qwen_happy_path_completes(tmp_path):
    result = _run_agent_qwen_case(tmp_path, npm_exit=0, with_qwen=True)

    assert result.returncode == 0
    assert "CASE_COMPLETED" in result.stdout


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
    for tool in ("sed", "cut", "grep", "true", "python3"):
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
    # flags were read from the remote config ...
    assert "bash -s" in ssh_calls
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
        "switch ('qwen-code-cli') {\n"
        '"qwen-code-cli" {\n' + _ps1_qwen_case_body() + "\n}\n}\n"
        "Write-Output 'CASE_COMPLETED'\n"
    )
    harness_file = tmp_path / "harness.ps1"
    harness_file.write_text(harness, encoding="utf-8")

    env = dict(os.environ)
    env["PATH"] = str(fake_bin)
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
