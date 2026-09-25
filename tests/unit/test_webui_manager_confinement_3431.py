"""Issue #3431 (Option 1): confined os_user launch in WebUIManager + contract.

Pins the manager half of the confinement: mode parsing (fail-closed on an
unknown value), the readiness probe mapping of the wrapper's ``check`` tokens,
the environment hand-off on stdin (never argv), the egress allowlist
derivation, and the capability snapshot the contract reports for a confined,
a degraded and a cold deployment.
"""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.services import workspace_isolation_contract as wic
from app.services.webui_manager import _WEBUI_CONFINE_WRAPPER, WebUIManager, WorkspaceConfig

pytestmark = [pytest.mark.issue(3431)]


def _manager(**overrides) -> WebUIManager:
    config = WorkspaceConfig(
        enabled=True,
        multi_user_mode=True,
        token_secret="secret-3431",
        webui_callback_url="http://10.0.0.5:19888",
        os_user_confinement="bwrap",
        confinement_memory_max="2G",
        confinement_cpu_quota=150,
        confinement_tasks_max=256,
        confinement_egress_allow=("registry.npmjs.org:443",),
    )
    for key, value in overrides.items():
        setattr(config, key, value)
    manager = WebUIManager(config)
    manager._platform = "linux"
    return manager


# ── mode ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("value", "enabled"),
    [("", False), ("off", False), ("OFF", False), ("bwrap", True), (" Bwrap ", True),
     ("docker", True), (None, False), (MagicMock(), False)],
)  # fmt: skip
def test_confinement_enabled_is_string_only(value, enabled):
    manager = _manager(os_user_confinement=value)
    assert manager._confinement_enabled() is enabled


def test_unknown_mode_is_a_readiness_error_not_a_silent_off():
    manager = _manager(os_user_confinement="docker")
    assert manager._confinement_readiness("/usr/bin/qwen-code-webui") == "confinement_mode_invalid"


def test_non_linux_platform_cannot_confine():
    manager = _manager()
    manager._platform = "darwin"
    assert (
        manager._confinement_readiness("/usr/bin/qwen-code-webui")
        == "confinement_platform_unsupported"
    )


def test_callback_url_is_required():
    # Without it the API URL (and so the allowlist) would come from the
    # request's Host header, which the user controls.
    manager = _manager(webui_callback_url="")
    assert (
        manager._confinement_readiness("/usr/bin/qwen-code-webui")
        == "confinement_callback_url_missing"
    )


def test_missing_wrapper_is_reported():
    manager = _manager()
    with patch("app.utils.workspace._is_wrapper_available", return_value=False):
        assert (
            manager._confinement_readiness("/usr/bin/qwen-code-webui")
            == "confinement_wrapper_missing"
        )


@pytest.mark.parametrize(
    ("rc", "stdout", "reason"),
    [
        (0, "ok\n", None),
        (1, "missing:bwrap\n", "confinement_bwrap_missing"),
        (1, "missing:setpriv\n", "confinement_setpriv_missing"),
        (1, "missing:systemd-run\n", "confinement_systemd_unavailable"),
        (1, "systemd:not-running\n", "confinement_systemd_unavailable"),
        (1, "userns:unavailable\n", "confinement_userns_unavailable"),
        (1, "policy:invalid\n", "confinement_policy_invalid"),
        (1, "bwrap:too-old\n", "confinement_bwrap_too_old"),
        (1, "", "confinement_check_failed"),
        (2, "something new\n", "confinement_check_failed"),
    ],
)
def test_check_tokens_map_to_reason_codes(rc, stdout, reason):
    manager = _manager()
    result = subprocess.CompletedProcess([], rc, stdout=stdout, stderr="")
    with (
        patch("app.utils.workspace._is_wrapper_available", return_value=True),
        patch("app.services.webui_manager.subprocess.run", return_value=result) as run,
    ):
        assert manager._confinement_readiness("/usr/bin/qwen-code-webui") == reason
    assert run.call_args.args[0] == [
        _WEBUI_CONFINE_WRAPPER,
        "check",
        "--webui",
        "/usr/bin/qwen-code-webui",
    ]


def test_check_that_cannot_run_fails_closed():
    manager = _manager()
    with (
        patch("app.utils.workspace._is_wrapper_available", return_value=True),
        patch(
            "app.services.webui_manager.subprocess.run",
            side_effect=subprocess.TimeoutExpired("x", 15),
        ),
    ):
        assert manager._confinement_readiness("/w") == "confinement_check_failed"


def test_launch_readiness_includes_confinement_after_base_checks():
    manager = _manager()
    manager._resolved_webui = ("/usr/bin/qwen-code-webui", None)
    with (
        patch("app.utils.workspace._is_wrapper_available", return_value=True),
        patch("app.services.webui_manager.shutil.which", return_value="/usr/bin/sudo"),
        patch.object(
            manager, "_confinement_readiness", return_value="confinement_userns_unavailable"
        ),
    ):
        assert manager._compute_launch_readiness() == "confinement_userns_unavailable"
        assert manager.confinement_active() is False


def test_launch_readiness_unchanged_when_confinement_off():
    manager = _manager(os_user_confinement="")
    manager._resolved_webui = ("/usr/bin/qwen-code-webui", None)
    with (
        patch("app.utils.workspace._is_wrapper_available", return_value=True),
        patch("app.services.webui_manager.shutil.which", return_value="/usr/bin/sudo"),
        patch.object(manager, "_confinement_readiness") as confinement,
    ):
        assert manager._compute_launch_readiness() is None
    confinement.assert_not_called()


# ── environment + allowlist + command ───────────────────────────────────────


def test_confined_env_drops_reserved_loader_and_empty_keys():
    manager = _manager()
    env = {
        "OPENAI_API_KEY": "tok",
        "OPENAI_BASE_URL": "http://10.0.0.5:19888/api/proxy/v1",
        "PATH": "/usr/bin",
        "HOME": "/root",
        "HTTPS_PROXY": "http://corp-proxy:3128",
        "NO_PROXY": "localhost",
        "LD_PRELOAD": "/x.so",
        "PYTHONPATH": "/x",
        "LC_ALL": "",
        "BAILIAN_CODING_PLAN_API_KEY": "pool-key",
    }
    assert manager._confined_env(env) == {
        "OPENAI_API_KEY": "tok",
        "OPENAI_BASE_URL": "http://10.0.0.5:19888/api/proxy/v1",
        "BAILIAN_CODING_PLAN_API_KEY": "pool-key",
    }


def test_allowlist_comes_from_server_config_only():
    manager = _manager(
        webui_callback_url="https://openace.example.com/",
        confinement_egress_allow=("registry.npmjs.org:443", "openace.example.com:443"),
    )
    assert manager._confinement_allowlist() == [
        "openace.example.com:443",
        "registry.npmjs.org:443",
    ]
    ipv6 = _manager(webui_callback_url="http://[fd00::1]:8080", confinement_egress_allow=())
    assert ipv6._confinement_allowlist() == ["[fd00::1]:8080"]


def test_request_derived_urls_never_reach_the_allowlist():
    manager = _manager()
    cmd = manager._build_confined_command(
        system_account="alice",
        port=3150,
        webui_cmd="/usr/bin/qwen-code-webui",
        webui_log_dir="/tmp/qwen-code-webui-7",
        openace_api_url="http://attacker.example:19888",  # e.g. from a forged Host header
    )
    allows = [cmd[i + 1] for i, arg in enumerate(cmd) if arg == "--allow"]
    assert allows == ["10.0.0.5:19888", "registry.npmjs.org:443"]


def test_confined_command_shape():
    manager = _manager()
    cmd = manager._build_confined_command(
        system_account="alice",
        port=3150,
        webui_cmd="/usr/bin/qwen-code-webui",
        webui_log_dir="/tmp/qwen-code-webui-7",
        openace_api_url="http://10.0.0.5:19888",
    )
    assert cmd[:4] == ["sudo", "-n", _WEBUI_CONFINE_WRAPPER, "launch"]
    pairs = dict(zip(cmd[4::2], cmd[5::2], strict=False))
    assert pairs["--account"] == "alice"
    assert pairs["--memory-max"] == "2G"
    assert pairs["--cpu-quota"] == "150"
    assert pairs["--tasks-max"] == "256"
    assert cmd[cmd.index("--webui") + 1] == "/usr/bin/qwen-code-webui"
    webui_args = cmd[cmd.index("--") + 1 :]
    # the WebUI binds loopback inside the sandbox; the wrapper's host side
    # listens on the instance port
    assert webui_args[webui_args.index("--host") + 1] == "127.0.0.1"
    assert webui_args[webui_args.index("--port") + 1] == "3150"


@patch("app.services.webui_manager.pwd")
@patch("app.services.webui_manager.subprocess.Popen")
@patch("app.services.webui_manager.run_as_root_if_needed")
def test_launch_hands_environment_over_stdin(mock_chown, mock_popen, mock_pwd):
    manager = _manager()
    mock_pwd.getpwuid.return_value.pw_name = "openace"
    mock_chown.return_value = SimpleNamespace(returncode=0, stderr="")
    process = MagicMock()
    mock_popen.return_value = process
    pool = {"proxy_token": "tok", "provider": "openai", "models": []}
    env = {
        "OPENAI_API_KEY": "tok-3431",
        "OPENAI_BASE_URL": "http://10.0.0.5:19888/v1",
        "PATH": "/bin",
    }
    with (
        patch.object(manager, "_build_webui_env", return_value=(env, pool)),
        patch.object(
            manager, "_find_webui_executable", return_value=("/usr/bin/qwen-code-webui", None)
        ),
        patch("app.services.webui_manager.os.makedirs"),
    ):
        result, _ = manager._launch_webui_process(7, "alice", 3150, "http://10.0.0.5")
    assert result is process
    cmd = mock_popen.call_args.args[0]
    assert cmd[2] == _WEBUI_CONFINE_WRAPPER
    assert "tok-3431" not in " ".join(cmd)
    assert mock_popen.call_args.kwargs["stdin"] == subprocess.PIPE
    written = json.loads(process.stdin.write.call_args.args[0])
    assert written["OPENAI_API_KEY"] == "tok-3431"
    assert "PATH" not in written
    process.stdin.close.assert_called_once()
    # --auth-type is appended to the WebUI's own arguments (after "--")
    assert cmd[-2:] == ["--auth-type", "openai"]


# ── capability contract ─────────────────────────────────────────────────────


class _ConfinedStub:
    def __init__(self, *, readiness=None, active=True):
        self.config = SimpleNamespace(enabled=True, multi_user_mode=True, sandbox_tier="")
        self._readiness = readiness
        self._active = active

    def per_user_launch_readiness(self):
        return self._readiness

    def confinement_active(self):
        return self._active and self._readiness is None


@pytest.fixture
def linux_no_sandbox(monkeypatch):
    monkeypatch.setattr(wic, "_current_platform", lambda: "linux")
    monkeypatch.setattr(
        wic,
        "_sandboxed_readiness",
        lambda config: (False, "", wic.IsolationReason("sandbox_backend_unconfigured", "none")),
    )


def test_confined_snapshot_adds_resources_and_egress(linux_no_sandbox):
    snap = wic.build_workspace_isolation_snapshot(_ConfinedStub())
    assert snap.supported is True
    assert snap.isolation_level == wic.ISOLATION_LEVEL_OS_USER
    assert snap.backend == wic.BACKEND_PER_USER_CONFINED
    assert set(snap.enforced) == {
        "identity", "filesystem", "environment", "process", "resources", "network_egress",
    }  # fmt: skip
    assert snap.unsupported == ("kernel",)
    assert snap.policy_revision == "2026-09-26.1"


def test_unconfined_os_user_snapshot_is_unchanged(linux_no_sandbox):
    snap = wic.build_workspace_isolation_snapshot(_ConfinedStub(active=False))
    assert snap.backend == wic.BACKEND_PER_USER
    assert set(snap.unsupported) == {"resources", "network_egress", "kernel"}


def test_host_that_cannot_confine_degrades_instead_of_running_unconfined(linux_no_sandbox):
    snap = wic.build_workspace_isolation_snapshot(
        _ConfinedStub(readiness="confinement_userns_unavailable")
    )
    assert snap.supported is False
    assert [r.code for r in snap.reasons] == ["launch_path_degraded"]
    assert "confinement_userns_unavailable" in snap.reasons[0].message


def test_cold_worker_does_not_claim_confinement(linux_no_sandbox, monkeypatch):
    monkeypatch.setattr("app.services.webui_manager.peek_webui_manager", lambda: None)
    monkeypatch.setattr(
        "app.services.webui_manager.read_workspace_config",
        lambda: WorkspaceConfig(enabled=True, multi_user_mode=True, os_user_confinement="bwrap"),
    )
    snap = wic.build_workspace_isolation_snapshot()
    assert snap.backend == wic.BACKEND_PER_USER
    assert "launch_path_unverified" in [r.code for r in snap.reasons]
    assert "resources" in snap.unsupported


def test_read_workspace_config_parses_confinement(tmp_path, monkeypatch):
    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "workspace": {
                    "os_user_confinement": " BWRAP ",
                    "confinement_memory_max": "8G",
                    "confinement_cpu_quota": 400,
                    "confinement_tasks_max": 1024,
                    "confinement_egress_allow": ["pypi.org:443", " ", "files.pythonhosted.org:443"],
                }
            }
        )
    )
    monkeypatch.setattr("app.repositories.database.CONFIG_DIR", str(tmp_path))
    from app.services.webui_manager import read_workspace_config

    config = read_workspace_config()
    assert config.os_user_confinement == "bwrap"
    assert config.confinement_memory_max == "8G"
    assert config.confinement_cpu_quota == 400
    assert config.confinement_tasks_max == 1024
    assert config.confinement_egress_allow == ("pypi.org:443", "files.pythonhosted.org:443")


@pytest.mark.parametrize("form", ["dev_directory", "same_account"])
@patch("app.services.webui_manager.pwd")
@patch("app.services.webui_manager.subprocess.Popen")
@patch("app.services.webui_manager.run_as_root_if_needed")
def test_unconfinable_launch_forms_fail_closed(mock_chown, mock_popen, mock_pwd, form):
    manager = _manager()
    mock_pwd.getpwuid.return_value.pw_name = "alice" if form == "same_account" else "openace"
    mock_chown.return_value = SimpleNamespace(returncode=0, stderr="")
    webui = ("/src/webui/cli.js", "/src/webui") if form == "dev_directory" else ("/usr/bin/w", None)
    with (
        patch.object(manager, "_build_webui_env", return_value=({"OPENAI_API_KEY": "t"}, {})),
        patch.object(manager, "_find_webui_executable", return_value=webui),
        patch("app.services.webui_manager.os.makedirs"),
    ):
        process, _ = manager._launch_webui_process(7, "alice", 3150, "http://10.0.0.5")
    assert process is None
    mock_popen.assert_not_called()
