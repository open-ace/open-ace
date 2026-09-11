"""Unit tests for WebUIManager per-user launch probe (Issue #3374)."""

import pytest

from app.services.webui_manager import WebUIManager

pytestmark = [pytest.mark.issue(3374)]


class _PwEntry:
    def __init__(self, pw_name, pw_uid=1500):
        self.pw_name = pw_name
        self.pw_uid = pw_uid


class _LaunchProbeManager(WebUIManager):
    """Bypass __init__ (config/env side effects); stub launch-path inputs."""

    def __init__(self, platform, webui_cmd, webui_dir):
        self._platform = platform
        self._webui_cmd = webui_cmd
        self._webui_dir = webui_dir
        self._resolved_webui = None  # probe memoization slot
        self._readiness_memo = None

    def _find_webui_executable(self, probe_only=False):
        self._last_probe_only = probe_only
        return self._webui_cmd, self._webui_dir


def _make(platform="linux", webui_cmd="/usr/local/bin/qwen-code-webui", webui_dir=None):
    return _LaunchProbeManager(platform, webui_cmd, webui_dir)


def _patch_identity(
    monkeypatch,
    current_user="open-ace",
    sudo="/usr/bin/sudo",
    wrapper=True,
    target_uid=1500,
):
    monkeypatch.setattr(
        "app.services.webui_manager.pwd.getpwuid",
        lambda uid: _PwEntry(current_user),
    )
    monkeypatch.setattr(
        "app.services.webui_manager.pwd.getpwnam",
        lambda name: _PwEntry(name, target_uid),
    )
    monkeypatch.setattr(
        "app.services.webui_manager.shutil.which",
        lambda name: sudo if name == "sudo" else None,
    )
    monkeypatch.setattr("app.utils.workspace._is_wrapper_available", lambda path: wrapper)


# --- readiness (account-independent path checks) ---


def test_readiness_rejects_unsupported_platform():
    assert _make(platform="windows").per_user_launch_readiness() == "platform_unsupported"


def test_readiness_rejects_missing_executable(monkeypatch):
    _patch_identity(monkeypatch)
    assert _make(webui_cmd=None).per_user_launch_readiness() == "webui_executable_missing"


def test_readiness_rejects_dev_directory_mode(monkeypatch):
    _patch_identity(monkeypatch)
    assert (
        _make(webui_dir="/srv/qwen-code-webui").per_user_launch_readiness()
        == "dev_directory_mode_shared_account"
    )


def test_readiness_rejects_missing_wrapper(monkeypatch):
    # review #8: the audited launch wrapper, not just the sudo binary, is
    # the precondition of the sudo path
    _patch_identity(monkeypatch, wrapper=False)
    assert _make().per_user_launch_readiness() == "launch_wrapper_missing"


def test_readiness_rejects_missing_sudo(monkeypatch):
    _patch_identity(monkeypatch, sudo=None)
    assert _make().per_user_launch_readiness() == "sudo_unavailable"


def test_readiness_ok_for_global_executable(monkeypatch):
    _patch_identity(monkeypatch)
    assert _make().per_user_launch_readiness() is None


def test_readiness_probe_never_builds(monkeypatch):
    # review #9: probe resolution must not run the 60s npm build
    manager = _make(webui_cmd=None, webui_dir="/srv/qwen-code-webui/backend")
    # dev-dir already short-circuits; force the build branch with a dir whose
    # isdir is True and node_entry missing by pointing webui_dir at backend:
    # _compute uses probe_only path — assert the flag was passed through.
    manager._webui_dir = None
    manager._webui_cmd = None

    def _assert_probe_only(probe_only=False):
        assert probe_only is True
        return None, None

    manager._find_webui_executable = _assert_probe_only
    assert manager.per_user_launch_readiness() == "webui_executable_missing"


def test_readiness_memoized_in_both_directions(monkeypatch):
    # review #9: degraded states are also memoized (short TTL) so repeated
    # GETs cannot loop a probe
    manager = _make(webui_cmd=None)
    calls = []

    def _counting_find(probe_only=False):
        calls.append(1)
        return None, None

    manager._find_webui_executable = _counting_find
    assert manager.per_user_launch_readiness() == "webui_executable_missing"
    assert manager.per_user_launch_readiness() == "webui_executable_missing"
    assert len(calls) == 1
    # TTL expiry re-probes (webui installed later becomes visible)
    manager._readiness_memo = (manager._readiness_memo[0] - 60.0, None)
    assert manager.per_user_launch_readiness() == "webui_executable_missing"
    assert len(calls) == 2


# --- account-level checks ---


def test_allows_when_already_target_user(monkeypatch):
    _patch_identity(monkeypatch, current_user="alice_acct", target_uid=1500)
    ok, reason = _make().supports_per_user_launch("alice_acct")
    assert ok is True and reason is None


def test_rejects_privileged_system_account(monkeypatch):
    # review #3: uid 0 (e.g. mapping system_account to root) must never
    # count as per-user isolation
    _patch_identity(monkeypatch, target_uid=0)
    ok, reason = _make().supports_per_user_launch("root")
    assert ok is False and reason == "privileged_system_account"


def test_rejects_reserved_system_account(monkeypatch):
    _patch_identity(monkeypatch, target_uid=42)
    ok, reason = _make().supports_per_user_launch("sysacct")
    assert ok is False and reason == "reserved_system_account"


def test_absent_os_account_is_not_a_probe_failure(monkeypatch):
    # in verified multi-user mode ensure_system_user provisions the account
    # at launch time
    monkeypatch.setattr(
        "app.services.webui_manager.pwd.getpwuid",
        lambda uid: _PwEntry("open-ace"),
    )

    def _missing(name):
        raise KeyError(name)

    monkeypatch.setattr("app.services.webui_manager.pwd.getpwnam", _missing)
    _patch_identity(monkeypatch)
    monkeypatch.setattr("app.services.webui_manager.pwd.getpwnam", _missing)
    ok, reason = _make().supports_per_user_launch("newuser_acct")
    assert ok is True and reason is None


def test_degraded_readiness_short_circuits_account_checks(monkeypatch):
    _patch_identity(monkeypatch)
    ok, reason = _make(webui_dir="/srv/qwen-code-webui").supports_per_user_launch("alice_acct")
    assert ok is False and reason == "dev_directory_mode_shared_account"


def test_cached_instance_with_stale_account_is_restarted(monkeypatch):
    # 评审 #2:复用分支必须比对实例的启动账户——映射变更后不能继续服务旧身份
    from gevent import lock as gevent_lock

    manager = _make()
    manager._lock = gevent_lock.RLock()
    manager._instances = {}
    manager._port_allocations = {}
    manager.config = type(
        "C",
        (),
        {"max_instances": 30, "url": "http://127.0.0.1", "multi_user_mode": True},
    )()

    class _FakeInstance:
        system_account = "old_acct"
        port = 3105
        url = "http://127.0.0.1:3105"
        token = "tok"

        def is_alive(self):
            return True

        def update_activity(self):
            pass

    manager._instances[7] = _FakeInstance()
    stopped, started = [], []
    monkeypatch.setattr(manager, "_stop_instance_internal", lambda uid: stopped.append(uid))

    def _fake_start(uid, account, base_url=None):
        started.append((uid, account))
        fresh = _FakeInstance()
        fresh.system_account = account
        return fresh

    monkeypatch.setattr(manager, "_start_instance_internal", _fake_start)
    url, token = manager.get_user_webui_url(7, "new_acct", None)
    assert stopped == [7]
    assert started == [(7, "new_acct")]


def test_cached_instance_with_same_account_is_reused(monkeypatch):
    from gevent import lock as gevent_lock

    manager = _make()
    manager._lock = gevent_lock.RLock()
    manager._instances = {}
    manager._port_allocations = {}
    manager.config = type(
        "C",
        (),
        {"max_instances": 30, "url": "http://127.0.0.1", "multi_user_mode": True},
    )()

    class _FakeInstance:
        system_account = "alice_acct"
        port = 3105
        url = "http://127.0.0.1:3105"
        token = "tok"

        def is_alive(self):
            return True

        def update_activity(self):
            pass

    manager._instances[7] = _FakeInstance()
    stopped, started = [], []
    monkeypatch.setattr(manager, "_stop_instance_internal", lambda uid: stopped.append(uid))
    monkeypatch.setattr(
        manager, "_start_instance_internal", lambda uid, account, base_url=None: started.append(1)
    )
    url, token = manager.get_user_webui_url(7, "alice_acct", None)
    assert stopped == [] and started == []
    assert url == "http://127.0.0.1:3105"


def test_prestart_skips_when_gate_rejects(monkeypatch):
    # 评审 #6:prestart 与 /user-url 同闸——拒绝则不 spawn
    manager = _make()
    manager.config = type("C", (), {"multi_user_mode": True, "required_isolation_level": ""})()
    spawned = []
    monkeypatch.setattr("app.services.webui_manager.gevent.spawn", lambda fn: spawned.append(fn))

    class _Contract:
        @staticmethod
        def build_workspace_isolation_snapshot(mgr):
            return wic_snap()

        @staticmethod
        def evaluate_isolation_requirement(required, snapshot, system_account, manager):
            return wic_reason("identity_mapping_missing", "no mapping")

    import app.services.workspace_isolation_contract as real_contract

    monkeypatch.setattr(
        "app.services.workspace_isolation_contract.build_workspace_isolation_snapshot",
        _Contract.build_workspace_isolation_snapshot,
    )
    monkeypatch.setattr(
        "app.services.workspace_isolation_contract.evaluate_isolation_requirement",
        _Contract.evaluate_isolation_requirement,
    )
    manager.prestart_user_instance_async(7, "alice_acct", "http://h")
    assert spawned == []


def test_prestart_skips_without_explicit_mapping():
    manager = _make()
    manager.config = type("C", (), {"multi_user_mode": True, "required_isolation_level": ""})()
    manager.prestart_user_instance_async(7, "", "http://h")  # no spawn, no raise


def wic_snap():
    from app.services.workspace_isolation_contract import IsolationCapabilitySnapshot

    return IsolationCapabilitySnapshot(
        supported=True,
        backend="qwen-code-webui-per-user",
        isolation_level="os_user",
        enforced=("identity",),
        unsupported=(),
        reasons=(),
    )


def wic_reason(code, message):
    from app.services.workspace_isolation_contract import IsolationReason

    return IsolationReason(code, message)


def test_prestart_invalid_config_floor_still_gates(monkeypatch):
    # PR review round 2:无效 config 下限在 prestart 也 fail-closed(归一化回退
    # 快照等级),不再跳过整个门闸
    manager = _make()
    manager.config = type(
        "C", (), {"multi_user_mode": True, "required_isolation_level": "strong"}
    )()
    spawned = []
    monkeypatch.setattr("app.services.webui_manager.gevent.spawn", lambda fn: spawned.append(fn))

    class _Contract:
        @staticmethod
        def build_workspace_config_snapshot(mgr):
            return wic_snap()

    rejected_with = []

    def _eval(required, snapshot, system_account, manager):
        rejected_with.append(required)
        return wic_reason("identity_mapping_missing", "no mapping")

    import app.services.workspace_isolation_contract as real_contract

    monkeypatch.setattr(
        "app.services.workspace_isolation_contract.build_workspace_isolation_snapshot",
        lambda mgr: wic_snap(),
    )
    monkeypatch.setattr(
        "app.services.workspace_isolation_contract.evaluate_isolation_requirement",
        _eval,
    )
    manager.prestart_user_instance_async(7, "alice_acct", "http://h")
    assert spawned == []
    assert rejected_with == ["os_user"]  # 归一化后按快照等级过闸


def test_launch_path_shares_resolution_memo_with_probe(monkeypatch):
    # PR review round 2 (遗留):启动路径复用探针的成功解析记忆,两个调用点
    # 共享同一份解析结果
    manager = _make()
    manager._lock = __import__("gevent").lock.RLock()
    manager._instances = {}
    manager._port_allocations = {}
    manager.config = type(
        "C",
        (),
        {
            "max_instances": 30,
            "url": "http://127.0.0.1",
            "multi_user_mode": True,
        },
    )()
    calls = []

    def _counting_find(probe_only=False):
        calls.append(probe_only)
        return "/usr/local/bin/qwen-code-webui", None

    manager._find_webui_executable = _counting_find
    import unittest.mock as _um

    with _um.patch("app.utils.workspace._is_wrapper_available", return_value=True):
        # 探针一次
        assert manager.per_user_launch_readiness() is None
    # 启动路径直接复用,不再解析
    resolved = manager._resolved_webui
    assert resolved == ("/usr/local/bin/qwen-code-webui", None)
    assert calls == [True]
