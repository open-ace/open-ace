"""Issue #3438: ``os_user_confinement = "kata"`` in the manager + contract.

The Kata mode is the local-container form of #3431 Option 2 on a Kata
Containers runtime: SANDBOXED with backend ``local-container:kata``, identity
still the OS account, home still the host directory. Pinned here: the root
probe command (``--backend kata``, the longer boot budget) and its reason
tokens, the ``--backend kata`` launch, the snapshot and the local launch form.
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
    WebUIManager,
    WorkspaceConfig,
)

pytestmark = [pytest.mark.issue(3438)]


def _manager(**overrides) -> WebUIManager:
    config = WorkspaceConfig(
        enabled=True,
        multi_user_mode=True,
        token_secret="secret-3438",
        webui_callback_url="http://10.0.0.5:19888",
        os_user_confinement="kata",
    )
    for key, value in overrides.items():
        setattr(config, key, value)
    manager = WebUIManager(config)
    manager._platform = "linux"
    return manager


def _probe_result(rc, token):
    return subprocess.CompletedProcess([], rc, stdout=token + "\n", stderr="")


def test_kata_readiness_runs_the_kata_probe():
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
    find_webui.assert_not_called()  # the WebUI lives in the image
    assert run.call_args.args[0] == [
        "sudo", "-n", _WEBUI_CONFINE_WRAPPER, "launch", "--probe", "--backend", "kata",
    ]  # fmt: skip
    # a nested-virtualization guest can take minutes to boot
    assert run.call_args.kwargs["timeout"] >= 300
    assert manager.confinement_mode() == "kata"


@pytest.mark.parametrize(
    ("token", "reason"),
    [
        ("kvm:unavailable", "confinement_kvm_unavailable"),
        ("channel:failed", "confinement_channel_failed"),
        ("kernel:unverified", "confinement_kernel_unverified"),
        ("runtime:missing", "confinement_runtime_missing"),
        ("image:missing", "confinement_image_missing"),
    ],
)
def test_kata_probe_tokens_map_to_reasons(token, reason):
    manager = _manager()
    with (
        patch("app.utils.workspace._is_wrapper_available", return_value=True),
        patch("app.services.webui_manager.subprocess.run", return_value=_probe_result(1, token)),
    ):
        assert manager._container_readiness() == reason


def test_runsc_probe_command_is_unchanged():
    manager = _manager(os_user_confinement="runsc")
    with (
        patch("app.utils.workspace._is_wrapper_available", return_value=True),
        patch(
            "app.services.webui_manager.subprocess.run", return_value=_probe_result(0, "ok")
        ) as run,
    ):
        manager._container_readiness()
    assert run.call_args.args[0] == ["sudo", "-n", _WEBUI_CONFINE_WRAPPER, "launch", "--probe"]


@patch("app.services.webui_manager.pwd")
@patch("app.services.webui_manager.subprocess.Popen")
@patch("app.services.webui_manager.run_as_root_if_needed")
def test_kata_launch_uses_the_kata_backend(mock_chown, mock_popen, mock_pwd):
    manager = _manager()
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
    assert cmd[cmd.index("--backend") + 1] == "kata"
    assert cmd[cmd.index("--webui") + 1] == "/usr/bin/qwen-code-webui"


class _Stub:
    def __init__(self, *, readiness=None, mode="kata"):
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


def test_kata_snapshot_is_sandboxed_with_every_dimension(linux_no_opensandbox):
    snap = wic.build_workspace_isolation_snapshot(_Stub())
    assert snap.supported is True
    assert snap.isolation_level == wic.ISOLATION_LEVEL_SANDBOXED
    assert snap.backend == "local-container:kata"
    assert set(snap.enforced) == set(wic.ALL_DIMENSIONS)
    assert snap.policy_revision == "2026-09-26.2"


def test_a_host_without_kvm_degrades(linux_no_opensandbox):
    snap = wic.build_workspace_isolation_snapshot(_Stub(readiness="confinement_kvm_unavailable"))
    assert snap.supported is False
    assert "confinement_kvm_unavailable" in snap.reasons[0].message


def test_an_unknown_mode_is_not_reported_as_sandboxed(linux_no_opensandbox):
    snap = wic.build_workspace_isolation_snapshot(_Stub(mode="firecracker"))
    assert snap.isolation_level != wic.ISOLATION_LEVEL_SANDBOXED


def test_kata_keeps_the_identity_chain_and_the_local_form(linux_no_opensandbox):
    snap = wic.build_workspace_isolation_snapshot(_Stub())
    reason = wic.evaluate_isolation_requirement(
        "sandboxed", snapshot=snap, system_account=None, manager=_Stub()
    )
    assert reason is not None and reason.code == "identity_mapping_missing"
    manager = _manager()
    assert manager._resolve_form("", snapshot=snap) == WEBUI_FORM_LOCAL
    assert manager._resolve_form("sandboxed", snapshot=snap) == WEBUI_FORM_LOCAL


def test_concurrent_readiness_checks_share_one_probe():
    """Under Kata each probe boots a VM: callers wait for the running one."""
    import threading

    manager = _manager()
    started = threading.Event()
    release = threading.Event()
    calls = []

    def _slow_probe(*args, **kwargs):
        calls.append(1)
        started.set()
        release.wait(5)
        return _probe_result(0, "ok")

    results = []
    with (
        patch("app.utils.workspace._is_wrapper_available", return_value=True),
        patch("app.services.webui_manager.subprocess.run", side_effect=_slow_probe),
    ):
        workers = [
            threading.Thread(target=lambda: results.append(manager._container_readiness()))
            for _ in range(4)
        ]
        for worker in workers:
            worker.start()
        assert started.wait(5)
        release.set()
        for worker in workers:
            worker.join(5)
    assert calls == [1]
    assert results == [None] * 4


def test_a_mode_switch_reprobes():
    manager = _manager()
    with (
        patch("app.utils.workspace._is_wrapper_available", return_value=True),
        patch(
            "app.services.webui_manager.subprocess.run", return_value=_probe_result(0, "ok")
        ) as run,
    ):
        manager._container_readiness()
        manager.config.os_user_confinement = "runsc"
        manager._container_readiness()
    assert run.call_count == 2
    assert "--backend" not in run.call_args.args[0]


def test_a_slow_failing_probe_is_shared_and_cached():
    """A Kata probe that fails after minutes: its result is stamped when it
    ENDS, so the callers waiting on it (and the next ones) do not re-probe."""
    import threading

    manager = _manager()
    clock = [1000.0]
    lock = threading.Lock()
    started = threading.Event()
    release = threading.Event()
    calls = []

    def _slow_failing_probe(*args, **kwargs):
        calls.append(1)
        started.set()
        release.wait(5)
        with lock:
            clock[0] += 300  # the boot timed out after five minutes
        return _probe_result(1, "kernel:unverified")

    def _now():
        with lock:
            return clock[0]

    results = []
    with (
        patch("app.utils.workspace._is_wrapper_available", return_value=True),
        patch("app.services.webui_manager.time.monotonic", side_effect=_now),
        patch("app.services.webui_manager.subprocess.run", side_effect=_slow_failing_probe),
    ):
        workers = [
            threading.Thread(target=lambda: results.append(manager._container_readiness()))
            for _ in range(4)
        ]
        for worker in workers:
            worker.start()
        assert started.wait(5)
        release.set()
        for worker in workers:
            worker.join(5)
        assert manager._container_readiness() == "confinement_kernel_unverified"
    assert calls == [1]
    assert results == ["confinement_kernel_unverified"] * 4
