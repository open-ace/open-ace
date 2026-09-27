"""Issue #3446: converting pre-#3446 config.json files to ``workspace.isolation``.

``scripts/convert_workspace_isolation.py`` runs on every install and upgrade
path (package installer local + remote, Docker entrypoint). These tests pin
the mapping, that every result passes the server's own ``parse_isolation``,
and that each path actually calls the converter before anything reads the
backend (configure_sudoers keys its confined-launch rules off it).
"""

from __future__ import annotations

import errno
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from app.services.workspace_isolation_config import parse_isolation

pytestmark = [pytest.mark.regression, pytest.mark.issue(3446)]

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "convert_workspace_isolation.py"
PACKAGE_INSTALLER = ROOT / "scripts" / "install-central" / "package-method" / "install.sh"
DOCKER_INSTALLER = ROOT / "scripts" / "install-central" / "docker-method" / "install.sh"
ENTRYPOINT = ROOT / "docker-entrypoint.sh"

_spec = importlib.util.spec_from_file_location("convert_workspace_isolation", SCRIPT)
conv = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(conv)


@pytest.fixture(autouse=True)
def _no_host_sandbox_config(monkeypatch, tmp_path):
    monkeypatch.delenv(conv.SANDBOX_BACKENDS_ENV, raising=False)
    monkeypatch.setattr(conv, "SYSTEM_SANDBOX_BACKENDS", str(tmp_path / "absent.json"))


def _run(tmp_path: Path, workspace, *args: str) -> dict:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"host_name": "h", "workspace": workspace}))
    assert conv.main([str(path), *args]) == 0
    result = json.loads(path.read_text())["workspace"]
    parse_isolation(result)  # the server must accept every result
    return result


@pytest.mark.parametrize(
    ("legacy", "expected"),
    [
        ({"multi_user_mode": True}, {"level": "os_user", "backend": "plain"}),
        ({"multi_user_mode": False}, {"level": "none", "backend": "shared"}),
        ({"multi_user_mode": "true"}, {"level": "os_user", "backend": "plain"}),
        # The old server used truthiness: the string "false" meant per-user.
        ({"multi_user_mode": "false"}, {"level": "os_user", "backend": "plain"}),
        (
            {"multi_user_mode": True, "os_user_confinement": "bwrap"},
            {"level": "os_user", "backend": "bwrap"},
        ),
        (
            {"multi_user_mode": True, "os_user_confinement": "off"},
            {"level": "os_user", "backend": "plain"},
        ),
        (
            {"multi_user_mode": True, "os_user_confinement": "runsc"},
            {"level": "sandboxed", "backend": "local-gvisor"},
        ),
        (
            {"multi_user_mode": True, "os_user_confinement": "KATA"},
            {"level": "sandboxed", "backend": "local-kata"},
        ),
        # B2: OpenSandbox deployments stay OpenSandbox, tier and floor kept.
        (
            {"multi_user_mode": True, "required_isolation_level": "sandboxed"},
            {"level": "sandboxed", "backend": "opensandbox"},
        ),
        (
            {
                "multi_user_mode": True,
                "sandbox_tier": "gold",
                "required_isolation_level": "sandboxed",
            },
            {"level": "sandboxed", "backend": "opensandbox", "tier": "gold"},
        ),
        # A tier without a webui_image fell back to the local form.
        (
            {"multi_user_mode": True, "sandbox_tier": "gold"},
            {"level": "os_user", "backend": "plain"},
        ),
        (
            {"multi_user_mode": False, "required_isolation_level": "sandboxed"},
            {"level": "sandboxed", "backend": "opensandbox"},
        ),
        # A floor above the old mode keeps the floor.
        (
            {"multi_user_mode": False, "required_isolation_level": "os_user"},
            {"level": "os_user", "backend": "plain"},
        ),
        (
            {"os_user_confinement": "bwrap", "required_isolation_level": "sandboxed"},
            {"level": "sandboxed", "backend": "opensandbox"},
        ),
        (
            {"os_user_confinement": "kata", "required_isolation_level": "sandboxed"},
            {"level": "sandboxed", "backend": "local-kata"},
        ),
    ],
)
def test_legacy_keys_map_to_one_block(tmp_path, legacy, expected):
    result = _run(tmp_path, {"enabled": True, **legacy})
    assert result["isolation"] == expected
    assert not set(conv.REMOVED_KEYS) & set(result)
    assert result["enabled"] is True


IMAGE = "ghcr.io/open-ace/webui@sha256:" + "a" * 64


def _backends(path: Path, tier: str = "std", **extra) -> Path:
    endpoints = {tier: {"webui_image": IMAGE}, **extra}
    path.write_text(json.dumps({"default_tier": tier, "endpoints": endpoints}))
    return path


@pytest.mark.parametrize(
    "legacy",
    [
        {"multi_user_mode": True},
        # Single-user deployments ran the WebUI in a pod too (B2, round 2).
        {"multi_user_mode": False},
        # The OpenSandbox check came first: pods beat local confinement.
        {"multi_user_mode": True, "os_user_confinement": "bwrap"},
        {"multi_user_mode": True, "os_user_confinement": "kata"},
    ],
)
def test_a_tier_with_a_webui_image_means_opensandbox(tmp_path, legacy):
    _backends(tmp_path / "sandbox-backends.json")
    result = _run(tmp_path, {"enabled": True, **legacy})
    assert result["isolation"] == {"level": "sandboxed", "backend": "opensandbox"}


def test_named_tier_and_env_path_are_honoured(tmp_path, monkeypatch):
    backends = _backends(tmp_path / "elsewhere.json", tier="std")
    backends.write_text(
        json.dumps(
            {
                "default_tier": "std",
                "endpoints": {"std": {}, "gold": {"webui_image": IMAGE}},
            }
        )
    )
    monkeypatch.setenv(conv.SANDBOX_BACKENDS_ENV, str(backends))
    assert _run(tmp_path, {"multi_user_mode": True})["isolation"]["backend"] == "plain"
    assert _run(tmp_path, {"multi_user_mode": True, "sandbox_tier": "gold"})["isolation"] == {
        "level": "sandboxed",
        "backend": "opensandbox",
        "tier": "gold",
    }


def test_a_missing_explicit_env_path_does_not_fall_back(tmp_path, monkeypatch):
    _backends(tmp_path / "sandbox-backends.json")
    monkeypatch.setenv(conv.SANDBOX_BACKENDS_ENV, str(tmp_path / "missing.json"))
    assert _run(tmp_path, {"multi_user_mode": True})["isolation"]["backend"] == "plain"


@pytest.mark.parametrize("value", ["true", "gvisor", True, ["bwrap"], "container"])
def test_unknown_confinement_refuses_to_guess(tmp_path, value, capsys):
    """The old server refused these (confinement_mode_invalid); plain would fail open."""
    path = tmp_path / "config.json"
    text = json.dumps({"workspace": {"multi_user_mode": True, "os_user_confinement": value}})
    path.write_text(text)
    assert conv.main([str(path)]) == 2
    assert path.read_text() == text
    assert "os_user_confinement" in capsys.readouterr().err


def test_limits_are_coerced_and_invalid_ones_dropped(tmp_path, capsys):
    result = _run(
        tmp_path,
        {
            "multi_user_mode": True,
            "os_user_confinement": "runsc",
            "confinement_memory_max": "2G",
            "confinement_cpu_quota": "150",
            "confinement_tasks_max": "lots",
            "confinement_egress_allow": "pypi.org:443, github.com:443",
            "confinement_container_webui": "/opt/webui",
        },
    )
    assert result["isolation"] == {
        "level": "sandboxed",
        "backend": "local-gvisor",
        "limits": {"memory": "2G", "cpu_percent": 150},
        "egress_allow": ["pypi.org:443", "github.com:443"],
        "container_webui": "/opt/webui",
    }
    assert "dropped invalid confinement_tasks_max" in capsys.readouterr().out


def test_limits_are_dropped_for_backends_that_do_not_enforce_them(tmp_path):
    result = _run(
        tmp_path,
        {"multi_user_mode": True, "confinement_memory_max": "2G", "sandbox_tier": ""},
    )
    assert result["isolation"] == {"level": "os_user", "backend": "plain"}


def test_already_converted_config_is_left_byte_for_byte(tmp_path):
    path = tmp_path / "config.json"
    text = '{"workspace": {"isolation": {"level": "os_user", "backend": "bwrap"}}}'
    path.write_text(text)
    assert conv.main([str(path)]) == 0
    assert conv.main([str(path), "--multi-user", "true"]) == 0
    assert path.read_text() == text


def test_stale_keys_beside_a_block_are_removed_and_the_block_wins(tmp_path, capsys):
    result = _run(
        tmp_path,
        {"isolation": {"level": "os_user", "backend": "plain"}, "os_user_confinement": "bwrap"},
    )
    assert result == {"isolation": {"level": "os_user", "backend": "plain"}}
    assert 'removed os_user_confinement="bwrap"' in capsys.readouterr().out


@pytest.mark.parametrize(
    ("workspace", "answer", "backend"),
    [
        ({}, "true", "plain"),
        ({}, "false", "shared"),
        # The shipped sample's block is the default, not a choice (B1, round 3).
        ({"isolation": {"level": "none", "backend": "shared"}}, "true", "plain"),
        ({"isolation": {"level": "os_user", "backend": "plain"}}, "false", "shared"),
        # The old installer wrote multi_user_mode from the wizard on every run.
        ({"multi_user_mode": False}, "true", "plain"),
        # Another backend is a deliberate choice: kept.
        ({"isolation": {"level": "os_user", "backend": "bwrap"}}, "false", "bwrap"),
        ({"isolation": {"level": "sandboxed", "backend": "opensandbox"}}, "true", "opensandbox"),
        ({"multi_user_mode": True, "os_user_confinement": "kata"}, "false", "local-kata"),
    ],
)
def test_installer_answer_sets_plain_or_shared(tmp_path, workspace, answer, backend):
    result = _run(tmp_path, workspace, "--multi-user", answer)
    assert result["isolation"]["backend"] == backend


def test_fresh_install_from_the_shipped_sample_honours_the_wizard(tmp_path):
    sample = json.loads((ROOT / "config" / "config.json.sample").read_text())
    path = tmp_path / "config.json"
    path.write_text(json.dumps(sample))
    assert conv.main([str(path), "--multi-user", "true"]) == 0
    workspace = json.loads(path.read_text())["workspace"]
    assert workspace["isolation"] == {"level": "os_user", "backend": "plain"}
    parse_isolation(workspace)


def test_no_old_keys_means_no_inference(tmp_path):
    """A new-format config without the block is "shared", even beside a
    sandbox-backends.json with a usable image (N1, round 3)."""
    _backends(tmp_path / "sandbox-backends.json")
    assert "isolation" not in _run(tmp_path, {"enabled": True})


@pytest.mark.parametrize(
    "backends",
    [
        {"default_tier": "std", "endpoints": {"std": {"webui_image": "webui:latest"}}},
        {
            "default_tier": "std",
            "image_allowlist": ["other@sha256:" + "b" * 64],
            "endpoints": {"std": {"webui_image": "ghcr.io/x@sha256:" + "a" * 64}},
        },
    ],
)
def test_an_unpinned_or_unlisted_image_fell_back_to_local(tmp_path, backends):
    (tmp_path / "sandbox-backends.json").write_text(json.dumps(backends))
    assert _run(tmp_path, {"multi_user_mode": True})["isolation"]["backend"] == "plain"


def test_second_run_is_a_no_op(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"workspace": {"multi_user_mode": True}}))
    assert conv.main([str(path)]) == 0
    once = path.read_text()
    assert conv.main([str(path)]) == 0
    assert path.read_text() == once


def test_rewrite_keeps_the_file_mode(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"workspace": {"multi_user_mode": True}}))
    path.chmod(0o600)
    assert conv.main([str(path)]) == 0
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert [p.name for p in tmp_path.iterdir()] == ["config.json"]


def test_symlinked_config_converts_the_target(tmp_path):
    real = tmp_path / "real.json"
    real.write_text(json.dumps({"workspace": {"multi_user_mode": True}}))
    link = tmp_path / "config.json"
    link.symlink_to(real)
    assert conv.main([str(link)]) == 0
    assert link.is_symlink()
    assert json.loads(real.read_text())["workspace"]["isolation"]["backend"] == "plain"


def test_owner_and_mode_are_set_on_the_descriptor_not_the_path(tmp_path, monkeypatch):
    """Root rewrites a file in a directory the service account can write: a
    temp file swapped for a symlink must not receive chmod/chown (B3, round 2)."""
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"workspace": {"multi_user_mode": True}}))

    def refuse(*_a, **_k):
        raise AssertionError("path-based chmod/chown")

    monkeypatch.setattr(conv.os, "chmod", refuse)
    monkeypatch.setattr(conv.os, "chown", refuse)
    fchown = []
    monkeypatch.setattr(conv.os, "fchown", lambda fd, uid, gid: fchown.append((uid, gid)))
    assert conv.main([str(path)]) == 0
    st = path.stat()
    assert fchown == [(st.st_uid, st.st_gid)]


def test_single_file_bind_mount_is_rewritten_in_place(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"workspace": {"multi_user_mode": True}}))
    inode = path.stat().st_ino

    def busy(*_a):
        raise OSError(errno.EBUSY, "Device or resource busy")

    monkeypatch.setattr(conv.os, "replace", busy)
    assert conv.main([str(path)]) == 0
    assert path.stat().st_ino == inode
    assert json.loads(path.read_text())["workspace"]["isolation"]["backend"] == "plain"
    assert [p.name for p in tmp_path.iterdir()] == ["config.json"]


def test_root_does_not_follow_a_link_to_another_owners_file(tmp_path, monkeypatch):
    real = tmp_path / "real.json"
    real.write_text(json.dumps({"workspace": {"multi_user_mode": True}}))
    link = tmp_path / "config.json"
    link.symlink_to(real)
    monkeypatch.setattr(conv.os, "geteuid", lambda: 0, raising=False)
    link_path = str(link)
    real_lstat = os.lstat

    def lstat(path, *a, **k):  # the link belongs to someone else (the service account)
        st = real_lstat(path, *a, **k)
        if os.fspath(path) != link_path:
            return st
        fields = list(st)
        fields[4] = st.st_uid + 1
        return os.stat_result(fields)

    monkeypatch.setattr(conv.os, "lstat", lstat)
    before = real.read_text()
    assert conv.main([str(link)]) == 1
    assert real.read_text() == before


def test_unreadable_config_fails(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{not json")
    assert conv.main([str(path)]) == 1


def test_script_runs_standalone(tmp_path):
    """The installer runs it with the system python3 before app deps exist."""
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"workspace": {"multi_user_mode": True}}))
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env[conv.SANDBOX_BACKENDS_ENV] = str(tmp_path / "absent.json")
    out = subprocess.run(
        [sys.executable, "-S", "-I", str(SCRIPT), str(path)],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        check=False,
    )
    assert out.returncode == 0, out.stderr
    assert json.loads(path.read_text())["workspace"]["isolation"]["backend"] == "plain"


# ── Every install/upgrade path runs it ──────────────────────────────────────


def _function_body(text: str, name: str) -> str:
    start = text.index(f"\n{name}() {{")
    end = text.index("\n}\n", start)
    return text[start:end]


def test_package_installer_converts_on_every_local_path_before_sudoers():
    text = PACKAGE_INSTALLER.read_text()
    body = _function_body(text, "install_local")
    convert = body.index('convert_workspace_isolation_config "$config_dir/config.json"')
    assert body.index('do_upgrade "$target_path"') < convert
    assert convert < body.index('configure_sudoers "$sudoers_run_user"')
    # The fresh-install writer uses the same script (no second copy of the mapping).
    assert "convert_workspace_isolation_config" in _function_body(text, "update_config_workspace")
    assert "os_user_confinement" not in _function_body(text, "update_config_workspace")


def test_package_installer_converts_the_remote_config_on_upgrade_and_fresh_install():
    text = PACKAGE_INSTALLER.read_text()
    helper = _function_body(text, "convert_workspace_isolation_config_remote")
    assert "scripts/convert_workspace_isolation.py' ~/.open-ace/config.json" in helper
    upgrade = _function_body(text, "do_upgrade_remote")
    call = upgrade.index('convert_workspace_isolation_config_remote "$remote"')
    assert upgrade.index('scp -r "$SOURCE_DIR"') < call < upgrade.index("systemctl restart")
    fresh = _function_body(text, "do_fresh_install_remote")
    call = fresh.index('convert_workspace_isolation_config_remote "$remote"')
    assert fresh.index('scp -r "$SOURCE_DIR"') < call


def _sudoers_omits_launch_rule(config_dir: Path, *, wrapper_installed: bool = True) -> bool:
    """Run configure_sudoers' decision block: is the unconfined launch rule omitted?"""
    body = _function_body(PACKAGE_INSTALLER.read_text(), "configure_sudoers")
    start = body.index("    local confine_configured=false")
    end = body.index("    local security_wrapper_rules=")
    rule = "svc ALL=(root) NOPASSWD: /usr/local/bin/openace-webui-confine launch *"
    script = (
        f"decide() {{\n local config_dir={str(config_dir)!r}\n"
        f" local confine_rule={rule if wrapper_installed else ''!r}\n"
        f"{body[start:end]}\n echo $confine_configured\n}}\ndecide\n"
    )
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True)
    return out.stdout.strip() == "true"


@pytest.mark.parametrize(
    ("workspace", "omitted"),
    [
        ({"isolation": {"level": "os_user", "backend": "bwrap"}}, True),
        ({"isolation": {"level": "sandboxed", "backend": "local-kata"}}, True),
        ({"isolation": {"level": "os_user", "backend": "plain"}}, False),
        # opensandbox never launches OS-account WebUIs here (B2, round 3).
        ({"isolation": {"level": "sandboxed", "backend": "opensandbox"}}, True),
        ({}, False),
        # A config that escaped conversion still keeps the unconfined rule out.
        ({"multi_user_mode": True, "os_user_confinement": "runsc"}, True),
        ({"multi_user_mode": True, "os_user_confinement": "off"}, False),
        ({"multi_user_mode": True, "os_user_confinement": 0}, True),
    ],
)
def test_sudoers_launch_rule_decision(tmp_path, workspace, omitted):
    (tmp_path / "config.json").write_text(json.dumps({"workspace": workspace}))
    assert _sudoers_omits_launch_rule(tmp_path) is omitted


def test_sudoers_keeps_opensandbox_hosts_without_the_wrapper_closed(tmp_path):
    (tmp_path / "config.json").write_text(
        json.dumps({"workspace": {"isolation": {"level": "sandboxed", "backend": "opensandbox"}}})
    )
    assert _sudoers_omits_launch_rule(tmp_path, wrapper_installed=False) is True


def test_a_confined_host_converted_to_opensandbox_does_not_regain_the_rule(tmp_path):
    """Legacy bwrap + a usable webui_image: the converter picks opensandbox and
    drops bwrap; sudoers must still leave the unconfined launch rule out."""
    _backends(tmp_path / "sandbox-backends.json")
    workspace = _run(tmp_path, {"multi_user_mode": True, "os_user_confinement": "bwrap"})
    assert workspace["isolation"]["backend"] == "opensandbox"
    assert _sudoers_omits_launch_rule(tmp_path) is True


def test_docker_entrypoint_converts_an_existing_config():
    body = _function_body(ENTRYPOINT.read_text(), "generate_default_config")
    exists = body.index('if [ -f "$CONFIG_FILE" ]; then')
    assert 'python3 /app/scripts/convert_workspace_isolation.py "$CONFIG_FILE"' in body[exists:]


def _docker_installer_backend(config: Path) -> tuple[int, str]:
    """Run the Docker installer's backend-reading block on ``config``."""
    text = DOCKER_INSTALLER.read_text()
    start = text.index("        # Issue #3446: workspace.isolation.backend. A pre-#3446")
    end = text.index("        # Multi-user mode here means per-user OS accounts")
    script = (
        'print_error() { echo "$*" >&2; }\n'
        f"read_backend() {{\n local config_file={str(config)!r}\n{text[start:end]}\n"
        ' echo "$WORKSPACE_ISOLATION_BACKEND"\n}\nread_backend\n'
    )
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False)
    return out.returncode, out.stdout.strip()


_DOCKER_CASES = [
    {"isolation": {"level": "sandboxed", "backend": "opensandbox"}},
    {"isolation": {"level": "os_user", "backend": "plain"}},
    {"isolation": {"level": "none", "backend": "shared"}},
    {"multi_user_mode": True},
    {"multi_user_mode": "false"},
    {"multi_user_mode": False, "required_isolation_level": "os_user"},
    {"multi_user_mode": True, "required_isolation_level": "sandboxed"},
    {"multi_user_mode": True, "sandbox_tier": "std"},
    {},
]


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq not installed")
@pytest.mark.parametrize("with_image", [False, True])
@pytest.mark.parametrize("workspace", _DOCKER_CASES)
def test_docker_installer_agrees_with_the_converter(tmp_path, workspace, with_image):
    """compose (root or not) and the converted config must name one backend (N3)."""
    if with_image:
        _backends(tmp_path / "sandbox-backends.json")
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"workspace": workspace}))
    rc, backend = _docker_installer_backend(config)
    assert rc == 0
    expected = _run(tmp_path, dict(workspace)).get("isolation", {}).get("backend", "shared")
    assert backend == expected


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq not installed")
@pytest.mark.parametrize(
    "workspace",
    [
        {"multi_user_mode": True, "os_user_confinement": "bwrap"},
        {"isolation": {"level": "sandboxed", "backend": "local-kata"}},
    ],
)
def test_docker_installer_refuses_backends_docker_cannot_run(tmp_path, workspace):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"workspace": workspace}))
    assert _docker_installer_backend(config)[0] != 0


@pytest.mark.parametrize("value", ["bwrap", "local-kata", "nonsense"])
def test_docker_installer_validates_the_backend_env(value):
    env = {**os.environ, "WORKSPACE_ISOLATION_BACKEND": value}
    out = subprocess.run(
        ["bash", str(DOCKER_INSTALLER), "--help"], capture_output=True, text=True, env=env
    )
    assert out.returncode != 0
    assert "WORKSPACE_ISOLATION_BACKEND must be shared, plain or opensandbox" in out.stderr


def test_docker_installer_writes_the_backend_not_a_multi_user_guess():
    text = DOCKER_INSTALLER.read_text()
    assert "echo plain || echo shared)" not in text
    assert text.count("WORKSPACE_ISOLATION_BACKEND=$(docker_isolation_backend)") == 2
