"""Issue #3446: converting pre-#3446 config.json files to ``workspace.isolation``.

``scripts/convert_workspace_isolation.py`` runs on every install and upgrade
path (package installer local + remote, Docker entrypoint). These tests pin
the mapping, that every result passes the server's own ``parse_isolation``,
and that each path actually calls the converter before anything reads the
backend (configure_sudoers keys its confined-launch rules off it).
"""

from __future__ import annotations

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
            {"multi_user_mode": True, "sandbox_tier": "gold"},
            {"level": "sandboxed", "backend": "opensandbox", "tier": "gold"},
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


def test_webui_image_beside_config_means_opensandbox(tmp_path):
    (tmp_path / "sandbox-backends.json").write_text(
        json.dumps({"default_tier": "std", "endpoints": {"std": {"webui_image": "img@sha256:x"}}})
    )
    result = _run(tmp_path, {"multi_user_mode": True})
    assert result["isolation"] == {"level": "sandboxed", "backend": "opensandbox"}


def test_webui_image_from_env_path_and_named_tier(tmp_path, monkeypatch):
    backends = tmp_path / "elsewhere.json"
    backends.write_text(
        json.dumps(
            {
                "default_tier": "std",
                "endpoints": {"std": {}, "gold": {"webui_image": "img@sha256:x"}},
            }
        )
    )
    monkeypatch.setenv(conv.SANDBOX_BACKENDS_ENV, str(backends))
    assert _run(tmp_path, {"multi_user_mode": True})["isolation"]["backend"] == "plain"


def test_webui_image_without_multi_user_stays_shared(tmp_path):
    (tmp_path / "sandbox-backends.json").write_text(
        json.dumps({"default_tier": "std", "endpoints": {"std": {"webui_image": "img"}}})
    )
    assert _run(tmp_path, {"multi_user_mode": False})["isolation"]["backend"] == "shared"


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
    assert conv.main([str(path), "--default-multi-user", "true"]) == 0
    assert path.read_text() == text


def test_stale_keys_beside_a_block_are_removed_and_the_block_wins(tmp_path):
    result = _run(
        tmp_path,
        {"isolation": {"level": "none", "backend": "shared"}, "multi_user_mode": True},
    )
    assert result == {"isolation": {"level": "none", "backend": "shared"}}


def test_default_applies_only_when_the_config_states_nothing(tmp_path):
    assert _run(tmp_path, {}, "--default-multi-user", "true")["isolation"]["backend"] == "plain"
    assert _run(tmp_path, {}, "--default-multi-user", "false")["isolation"]["backend"] == "shared"
    assert (
        _run(tmp_path, {"multi_user_mode": False}, "--default-multi-user", "true")["isolation"][
            "backend"
        ]
        == "shared"
    )
    assert "isolation" not in _run(tmp_path, {})


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


def test_package_installer_converts_the_remote_config_on_upgrade():
    body = _function_body(PACKAGE_INSTALLER.read_text(), "do_upgrade_remote")
    assert "scripts/convert_workspace_isolation.py' ~/.open-ace/config.json" in body
    assert body.index("convert_workspace_isolation.py") < body.index("systemctl restart open-ace")


def _sudoers_confined_check() -> str:
    body = _function_body(PACKAGE_INSTALLER.read_text(), "configure_sudoers")
    match = re.search(r"python3 -c '([^']+)'", body[body.index("confine_configured=false") :])
    assert match
    return match.group(1)


@pytest.mark.parametrize(
    ("workspace", "confined"),
    [
        ({"isolation": {"level": "os_user", "backend": "bwrap"}}, True),
        ({"isolation": {"level": "sandboxed", "backend": "local-kata"}}, True),
        ({"isolation": {"level": "os_user", "backend": "plain"}}, False),
        ({"isolation": {"level": "sandboxed", "backend": "opensandbox"}}, False),
        ({}, False),
        # A config that escaped conversion still keeps the unconfined rule out.
        ({"multi_user_mode": True, "os_user_confinement": "runsc"}, True),
        ({"multi_user_mode": True, "os_user_confinement": "off"}, False),
    ],
)
def test_sudoers_confined_check(tmp_path, workspace, confined):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"workspace": workspace}))
    rc = subprocess.run(
        [sys.executable, "-c", _sudoers_confined_check(), str(path)], check=False
    ).returncode
    assert (rc == 0) is confined


def test_docker_entrypoint_converts_an_existing_config():
    body = _function_body(ENTRYPOINT.read_text(), "generate_default_config")
    exists = body.index('if [ -f "$CONFIG_FILE" ]; then')
    assert 'python3 /app/scripts/convert_workspace_isolation.py "$CONFIG_FILE"' in body[exists:]


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq not installed")
@pytest.mark.parametrize(
    ("workspace", "backend"),
    [
        ({"isolation": {"level": "sandboxed", "backend": "opensandbox"}}, "opensandbox"),
        ({"isolation": {"level": "os_user", "backend": "plain"}}, "plain"),
        ({"isolation": {"level": "none", "backend": "shared"}}, "shared"),
        ({"multi_user_mode": True}, "plain"),
        ({"multi_user_mode": True, "sandbox_tier": "std"}, "opensandbox"),
        ({}, "shared"),
    ],
)
def test_docker_installer_reads_the_real_backend(tmp_path, workspace, backend):
    text = DOCKER_INSTALLER.read_text()
    expr = re.search(r"WORKSPACE_ISOLATION_BACKEND=\$\(jq -r '([^']+)'", text)
    assert expr
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"workspace": workspace}))
    out = subprocess.run(["jq", "-r", expr.group(1), str(path)], capture_output=True, text=True)
    assert out.stdout.strip() == backend


def test_docker_installer_writes_the_backend_not_a_multi_user_guess():
    text = DOCKER_INSTALLER.read_text()
    assert "echo plain || echo shared)" not in text
    assert text.count("WORKSPACE_ISOLATION_BACKEND=$(docker_isolation_backend)") == 2
