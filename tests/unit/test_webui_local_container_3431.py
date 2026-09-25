"""Issue #3431 (Option 2): ``os_user_confinement = "runsc"`` in the manager + contract.

A local gVisor container is the SANDBOXED level with backend
``local-container:runsc``, but unlike the OpenSandbox pod form its identity is
the OS account and its home is the host directory. Pinned here: readiness via
the root probe (and its memo), the container launch command, the snapshot,
the entry-point matrix, the /user-url gate's identity chain and the launch
form (never the pod launcher).
"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.services import workspace_isolation_contract as wic
from app.services.webui_manager import (
    _WEBUI_CONFINE_WRAPPER,
    WEBUI_FORM_LOCAL,
    WEBUI_FORM_SANDBOXED,
    WebUIManager,
    WorkspaceConfig,
)

pytestmark = [pytest.mark.issue(3431)]


def _manager(**overrides) -> WebUIManager:
    config = WorkspaceConfig(
        enabled=True,
        multi_user_mode=True,
        token_secret="secret-3431",
        webui_callback_url="http://10.0.0.5:19888",
        os_user_confinement="runsc",
    )
    for key, value in overrides.items():
        setattr(config, key, value)
    manager = WebUIManager(config)
    manager._platform = "linux"
    return manager


def _probe_result(rc, token, stderr=""):
    return subprocess.CompletedProcess([], rc, stdout=token + "\n", stderr=stderr)


# ── readiness ───────────────────────────────────────────────────────────────


def test_runsc_readiness_skips_host_webui_and_runs_the_root_probe():
    manager = _manager()
    with (
        patch("app.utils.workspace._is_wrapper_available", return_value=True),
        patch("app.services.webui_manager.shutil.which", return_value="/usr/bin/sudo"),
        patch.object(manager, "_find_webui_executable") as find_webui,
        patch(
            "app.services.webui_manager.subprocess.run", return_value=_probe_result(0, "ok")
        ) as run,
    ):
        assert manager._compute_launch_readiness() is None
        assert manager.confinement_active() is True
    find_webui.assert_not_called()  # the WebUI lives in the image
    assert run.call_args.args[0] == ["sudo", "-n", _WEBUI_CONFINE_WRAPPER, "launch", "--probe"]
    assert manager.confinement_mode() == "runsc"


@pytest.mark.parametrize(
    ("token", "reason"),
    [
        ("policy:invalid", "confinement_policy_invalid"),
        ("docker:unavailable", "confinement_docker_unavailable"),
        ("runtime:missing", "confinement_runtime_missing"),
        ("image:missing", "confinement_image_missing"),
        ("kernel:unverified", "confinement_kernel_unverified"),
        ("runtime:no-host-uds", "confinement_runtime_host_uds_disabled"),
        ("probe:failed", "confinement_check_failed"),
        ("something-new", "confinement_check_failed"),
    ],
)
def test_probe_tokens_map_to_reasons(token, reason):
    manager = _manager()
    with (
        patch("app.utils.workspace._is_wrapper_available", return_value=True),
        patch("app.services.webui_manager.subprocess.run", return_value=_probe_result(1, token)),
    ):
        assert manager._container_readiness() == reason


def test_probe_memo_keeps_success_for_an_hour_and_failure_briefly():
    manager = _manager()
    clock = [1000.0]
    with (
        patch("app.utils.workspace._is_wrapper_available", return_value=True),
        patch("app.services.webui_manager.time.monotonic", side_effect=lambda: clock[0]),
        patch(
            "app.services.webui_manager.subprocess.run", return_value=_probe_result(0, "ok")
        ) as run,
    ):
        assert manager._container_readiness() is None
        clock[0] += 3000
        assert manager._container_readiness() is None
        assert run.call_count == 1  # success cached (< 1 h)
        clock[0] += 700
        manager._container_readiness()
        assert run.call_count == 2  # expired after 1 h
    manager = _manager()
    with (
        patch("app.utils.workspace._is_wrapper_available", return_value=True),
        patch("app.services.webui_manager.time.monotonic", side_effect=lambda: clock[0]),
        patch(
            "app.services.webui_manager.subprocess.run",
            return_value=_probe_result(1, "image:missing"),
        ) as run,
    ):
        manager._container_readiness()
        clock[0] += 31
        manager._container_readiness()
        assert run.call_count == 2  # a failure is re-probed after 30 s


@pytest.mark.parametrize(
    ("override", "reason"),
    [({"webui_callback_url": ""}, "confinement_callback_url_missing")],
)
def test_runsc_readiness_requires_callback_url(override, reason):
    assert _manager(**override)._container_readiness() == reason


def test_runsc_readiness_requires_linux_and_the_wrapper():
    manager = _manager()
    manager._platform = "darwin"
    assert manager._container_readiness() == "confinement_platform_unsupported"
    manager = _manager()
    with patch("app.utils.workspace._is_wrapper_available", return_value=False):
        assert manager._container_readiness() == "confinement_wrapper_missing"


# ── launch command ──────────────────────────────────────────────────────────


@patch("app.services.webui_manager.pwd")
@patch("app.services.webui_manager.subprocess.Popen")
@patch("app.services.webui_manager.run_as_root_if_needed")
def test_runsc_launch_uses_the_container_backend_and_image_webui(mock_chown, mock_popen, mock_pwd):
    manager = _manager(confinement_container_webui="/usr/bin/qwen-code-webui")
    mock_pwd.getpwuid.return_value.pw_name = "openace"
    mock_chown.return_value = SimpleNamespace(returncode=0, stderr="")
    with (
        patch.object(manager, "_build_webui_env", return_value=({"OPENAI_API_KEY": "t"}, {})),
        patch.object(manager, "_find_webui_executable") as find_webui,
        patch("app.services.webui_manager.os.makedirs"),
    ):
        manager._launch_webui_process(7, "alice", 3150, "http://10.0.0.5")
    find_webui.assert_not_called()
    cmd = mock_popen.call_args.args[0]
    assert cmd[:4] == ["sudo", "-n", _WEBUI_CONFINE_WRAPPER, "launch"]
    assert cmd[cmd.index("--backend") + 1] == "container"
    assert cmd[cmd.index("--webui") + 1] == "/usr/bin/qwen-code-webui"


# ── contract ────────────────────────────────────────────────────────────────


class _Stub:
    def __init__(self, *, readiness=None, mode="runsc"):
        self.config = SimpleNamespace(enabled=True, multi_user_mode=True, sandbox_tier="")
        self._readiness = readiness
        self._mode = mode

    def per_user_launch_readiness(self):
        return self._readiness

    def confinement_active(self):
        return bool(self._mode) and self._readiness is None

    def confinement_mode(self):
        return self._mode

    def supports_per_user_launch(self, account):
        return True, None


@pytest.fixture
def linux_no_opensandbox(monkeypatch):
    monkeypatch.setattr(wic, "_current_platform", lambda: "linux")
    monkeypatch.setattr(
        wic,
        "_sandboxed_readiness",
        lambda config: (False, "", wic.IsolationReason("sandbox_backend_unconfigured", "none")),
    )


def test_local_container_snapshot_is_sandboxed_with_every_dimension(linux_no_opensandbox):
    snap = wic.build_workspace_isolation_snapshot(_Stub())
    assert snap.supported is True
    assert snap.isolation_level == wic.ISOLATION_LEVEL_SANDBOXED
    assert snap.backend == "local-container:runsc"
    assert set(snap.enforced) == set(wic.ALL_DIMENSIONS)
    assert snap.unsupported == ()
    assert snap.policy_revision == "2026-09-26.1"


def test_local_container_keeps_the_os_user_entry_matrix(linux_no_opensandbox):
    data = wic.build_workspace_isolation_snapshot(_Stub()).public_dict()
    # the home is the host directory: the /fs entry points stay enforced
    assert data["entry_points"]["filesystem_api"] == "enforced"
    assert "sandboxed_entry_not_wired" not in data["entry_points"].values()


def test_a_host_that_cannot_run_the_container_degrades(linux_no_opensandbox):
    snap = wic.build_workspace_isolation_snapshot(_Stub(readiness="confinement_image_missing"))
    assert snap.supported is False
    assert "confinement_image_missing" in snap.reasons[0].message


def test_gate_keeps_the_identity_chain_for_local_containers(linux_no_opensandbox):
    snap = wic.build_workspace_isolation_snapshot(_Stub())
    for level in ("os_user", "sandboxed"):
        reason = wic.evaluate_isolation_requirement(
            level, snapshot=snap, system_account=None, manager=_Stub()
        )
        assert reason is not None and reason.code == "identity_mapping_missing"
        assert (
            wic.evaluate_isolation_requirement(
                level, snapshot=snap, system_account="alice", manager=_Stub()
            )
            is None
        )


def test_opensandbox_snapshot_still_skips_the_identity_chain():
    snap = wic.IsolationCapabilitySnapshot(
        supported=True,
        backend="opensandbox:kata",
        isolation_level=wic.ISOLATION_LEVEL_SANDBOXED,
        enforced=(),
        unsupported=(),
        reasons=(),
    )
    assert (
        wic.evaluate_isolation_requirement(
            "sandboxed", snapshot=snap, system_account=None, manager=_Stub()
        )
        is None
    )


def test_launch_form_is_local_for_local_containers(linux_no_opensandbox):
    manager = _manager()
    snap = wic.build_workspace_isolation_snapshot(_Stub())
    assert manager._resolve_form("", snapshot=snap) == WEBUI_FORM_LOCAL
    # an explicit sandboxed request must not reach the OpenSandbox launcher
    assert manager._resolve_form("sandboxed", snapshot=snap) == WEBUI_FORM_LOCAL


def test_launch_form_is_the_pod_for_opensandbox():
    manager = _manager(os_user_confinement="")
    snap = wic.IsolationCapabilitySnapshot(
        supported=True,
        backend="opensandbox:kata",
        isolation_level=wic.ISOLATION_LEVEL_SANDBOXED,
        enforced=(),
        unsupported=(),
        reasons=(),
    )
    assert manager._resolve_form("", snapshot=snap) == WEBUI_FORM_SANDBOXED
    assert manager._resolve_form("sandboxed", snapshot=snap) == WEBUI_FORM_SANDBOXED
