"""Issue #3378: WebUIManager sandboxed-form fork (§7.2/§7.5 manager side).

Fake-launcher injections pin the manager's half of the contract: the form fork
in get_user_webui_url (no OS account, no pid, proxy-port URL, re-minted
per-instance tokens on hit), the is_alive fork (health via the launcher),
cross-form stop-and-restart, same-form port reuse, single-user sandboxed URL,
idle-reclaim teardown, periodic maintenance (export + renew), shutdown, the
prestart reorder, and the per-instance-secret token validation/refresh fork.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from app.services import workspace_isolation_contract as wic
from app.services.webui_manager import WebUIInstance, WebUIManager, WorkspaceConfig
from app.services.webui_sandbox import mint_instance_token

pytestmark = [pytest.mark.issue(3378)]


class _FakeProxy:
    """Stands in for SandboxWebuiProxy."""

    def __init__(self, *, sandbox_id, upstream_resolver, on_activity=None, **kwargs):
        self.sandbox_id = sandbox_id
        self.upstream_resolver = upstream_resolver
        self.on_activity = on_activity
        self.started_port = None
        self.stopped = False

    def start(self, port=0):
        self.started_port = port or 45678
        return self.started_port

    def stop(self):
        self.stopped = True


@dataclass(frozen=True)
class _FakeLaunchResult:
    """Frozen-dataclass stand-in for SandboxedWebuiLaunchResult.

    Must be a real (frozen) dataclass: _launch_sandboxed rewrites
    restore_confirmed via dataclasses.replace on the unreadable-snapshot
    degrade path (T-E).
    """

    sandbox_id: str
    tier: str = "kata"
    token_secret: str = "a" * 64
    restore_confirmed: bool = True
    proxy_token: str = "proxytok"
    proxy_token_expires_at: datetime = datetime.now() + timedelta(hours=24)
    webui_port: int = 3100


class _FakeLauncher:
    """Stands in for SandboxedWebuiLauncher; records every call."""

    def __init__(self, *, restore_confirmed=True, healthy=True):
        self.launch_calls: list[dict] = []
        self.destroy_calls: list[dict] = []
        self.renew_calls: list[str] = []
        self.exports: list[bool] = []
        self.health_calls: list[dict] = []
        self.snapshots: dict[int, bytes | None] = {}
        self.persisted: dict[int, bytes] = {}
        self.healthy = healthy
        self._restore_confirmed = restore_confirmed
        self._next_id = 0

    def load_snapshot(self, user_id):
        return self.snapshots.get(user_id)

    def launch(self, *, user_id, callback_url, snapshot=None):
        self.launch_calls.append(
            {"user_id": user_id, "callback_url": callback_url, "snapshot": snapshot}
        )
        self._next_id += 1
        return _FakeLaunchResult(
            sandbox_id=f"sb-{user_id}-{self._next_id}",
            restore_confirmed=self._restore_confirmed,
        )

    def resolve_webui_endpoint(self, sandbox_id):
        return (f"http://upstream.invalid/{sandbox_id}", {"OpenSandbox-Ingress-To": "x"})

    def health_check(self, *, proxy_port, token):
        self.health_calls.append({"proxy_port": proxy_port, "token": token})
        return self.healthy

    def renew_expiration(self, sandbox_id, *, proxy_token):
        self.renew_calls.append(sandbox_id)
        return "2099-01-01T00:00:00"

    def export_snapshot(self, sandbox_id, *, restore_confirmed):
        self.exports.append(restore_confirmed)
        return b"TAR"

    def persist_snapshot(self, user_id, blob):
        self.persisted[user_id] = blob
        return f"/state/webui-{user_id}.tar"

    def destroy(self, sandbox_id, user_id, *, restore_confirmed, final_export=True):
        self.destroy_calls.append(
            {
                "sandbox_id": sandbox_id,
                "user_id": user_id,
                "restore_confirmed": restore_confirmed,
                "final_export": final_export,
            }
        )


def _manager(*, multi_user=True, launcher=None, **config_kwargs):
    config_kwargs.setdefault("port_range_start", 3100)
    config_kwargs.setdefault("port_range_end", 3200)
    config = WorkspaceConfig(
        enabled=True,
        url="http://127.0.0.1",
        multi_user_mode=multi_user,
        webui_callback_url="http://openace.open-ace.svc.cluster.local:8080",
        **config_kwargs,
    )
    manager = WebUIManager(config)
    manager.stop_cleanup_thread()
    if launcher is not None:
        manager._sandbox_launcher = launcher
    return manager


@pytest.fixture(autouse=True)
def _fake_proxy(monkeypatch):
    monkeypatch.setattr("app.services.webui_sandbox.SandboxWebuiProxy", _FakeProxy)


# ── multi-user sandboxed start ────────────────────────────────────────


def test_sandboxed_start_launches_pod_and_returns_proxy_port_url():
    launcher = _FakeLauncher()
    manager = _manager(launcher=launcher)
    url, token = manager.get_user_webui_url(
        7, "", "http://192.168.1.5:19888", required_isolation="sandboxed"
    )

    assert len(launcher.launch_calls) == 1
    call = launcher.launch_calls[0]
    assert call["user_id"] == 7
    assert call["callback_url"] == "http://openace.open-ace.svc.cluster.local:8080"
    assert call["snapshot"] is None  # first launch: no stored snapshot

    instance = manager.get_user_instance(7)
    assert instance.form == "sandboxed"
    assert instance.sandbox_id == "sb-7-1"
    assert instance.sandbox_tier == "kata"
    assert instance.restore_confirmed is True
    assert instance.token_secret == "a" * 64
    assert instance.pid is None  # no OS process
    assert instance.process is None
    assert instance.proxy.started_port == instance.port
    # URL: request host + the LOCAL proxy port (never the in-pod 3100).
    assert url == f"http://192.168.1.5:{instance.port}"
    # Token is v2 signed with the per-instance secret.
    assert token.startswith("v2:7:")
    expect = mint_instance_token(7, instance.port, "nope")
    assert token != expect  # sanity: not minted with a foreign secret


def test_sandboxed_hit_reuses_pod_and_remints_token():
    launcher = _FakeLauncher()
    manager = _manager(launcher=launcher)
    url1, token1 = manager.get_user_webui_url(7, "u7", None, required_isolation="sandboxed")
    url2, token2 = manager.get_user_webui_url(7, "u7", None, required_isolation="sandboxed")

    assert len(launcher.launch_calls) == 1  # pod reused, not recreated
    assert launcher.health_calls  # is_alive went through the launcher probe
    assert token1 != token2  # re-minted per access (aligned with single-user)
    assert token2.startswith("v2:7:")
    instance = manager.get_user_instance(7)
    assert token2 == instance.token


def test_sandboxed_token_validates_against_instance_secret_and_dies_with_it():
    launcher = _FakeLauncher()
    manager = _manager(launcher=launcher)
    _url, token = manager.get_user_webui_url(7, "u7", None, required_isolation="sandboxed")
    valid, user_id, error = manager.validate_token(token)
    assert valid is True and user_id == 7 and error is None

    ok, new_token, err = manager.refresh_token(token)
    assert ok is True and new_token and err is None

    # Teardown: no matching instance → the token no longer validates (N8).
    manager.stop_user_webui(7)
    valid, user_id, error = manager.validate_token(token)
    assert valid is False


def test_is_alive_fork_declares_dead_after_consecutive_failures():
    launcher = _FakeLauncher(healthy=False)
    manager = _manager(launcher=launcher)
    manager.get_user_webui_url(7, "u7", None, required_isolation="sandboxed")
    instance = manager.get_user_instance(7)
    instance._health_check_ttl = 0.0  # bypass the cache window

    for _ in range(instance._max_consecutive_failures - 1):
        assert instance.is_alive() is True  # tolerant until the threshold
    assert instance.is_alive() is False
    # The failing health probe carried an instance-signed token + proxy port.
    assert launcher.health_calls[0]["proxy_port"] == instance.port
    assert launcher.health_calls[0]["token"].startswith("v2:7:")


def test_cross_form_stop_and_restart():
    launcher = _FakeLauncher()
    manager = _manager(launcher=launcher)
    # A live LOCAL instance (form local) is serving user 7.
    local = WebUIInstance(user_id=7, system_account="u7", port=3100, form="local")
    local.is_alive = lambda: True
    manager._instances[7] = local
    manager._port_allocations[3100] = (7, "local")

    url, _token = manager.get_user_webui_url(
        7, "u7", "http://192.168.1.5:19888", required_isolation="sandboxed"
    )
    # The local instance was replaced by a sandboxed one.
    instance = manager.get_user_instance(7)
    assert instance.form == "sandboxed"
    assert len(launcher.launch_calls) == 1


def test_same_form_restart_reuses_previous_port():
    launcher = _FakeLauncher()
    manager = _manager(launcher=launcher)
    _url1, _t1 = manager.get_user_webui_url(7, "u7", None, required_isolation="sandboxed")
    port1 = manager.get_user_instance(7).port
    manager.stop_user_webui(7)
    assert manager.get_user_instance(7) is None

    _url2, _t2 = manager.get_user_webui_url(7, "u7", None, required_isolation="sandboxed")
    port2 = manager.get_user_instance(7).port
    assert port2 == port1  # SEC-Q4


def test_idle_reclaim_exports_destroys_and_unproxies():
    launcher = _FakeLauncher()
    manager = _manager(launcher=launcher)
    manager.get_user_webui_url(7, "u7", None, required_isolation="sandboxed")
    instance = manager.get_user_instance(7)
    proxy = instance.proxy

    manager.cleanup_idle_instances()  # instance just started → not idle yet
    assert launcher.destroy_calls == []

    instance.last_activity = datetime.now() - timedelta(hours=2)
    manager.cleanup_idle_instances()

    assert len(launcher.destroy_calls) == 1
    call = launcher.destroy_calls[0]
    assert call["sandbox_id"] == instance.sandbox_id
    assert call["restore_confirmed"] is True
    assert call["final_export"] is True
    assert proxy.stopped
    assert manager.get_user_instance(7) is None
    assert instance.port not in manager._port_allocations


def test_periodic_maintenance_exports_and_renews_each_live_sandbox():
    launcher = _FakeLauncher()
    manager = _manager(launcher=launcher)
    manager.get_user_webui_url(7, "u7", None, required_isolation="sandboxed")

    manager._maintain_sandboxed_instances()
    assert launcher.exports == [True]
    assert launcher.persisted == {7: b"TAR"}
    assert launcher.renew_calls == [manager.get_user_instance(7).sandbox_id]


def test_maintenance_tick_refreshes_process_heartbeat(tmp_path, monkeypatch):
    """T-D: the maintenance cadence refreshes this process's heartbeat file
    (the multi-replica reconcile mutex) before exporting/renewing."""
    from app.services import webui_sandbox as ws

    launcher = _FakeLauncher()
    manager = _manager(launcher=launcher)
    manager.get_user_webui_url(7, "u7", None, required_isolation="sandboxed")
    monkeypatch.setenv(ws.STATE_ROOT_ENV, str(tmp_path))
    manager._last_sandbox_maintenance = 0.0  # force the gate open

    manager._sandbox_maintenance_tick()

    heartbeats = list(tmp_path.glob(f"{ws.HEARTBEAT_FILENAME_PREFIX}*.json"))
    assert len(heartbeats) == 1
    assert launcher.exports == [True]  # the tick still maintained the pod
    # A second tick inside the window refreshes the heartbeat but is a no-op
    # gate-wise (the maintenance interval has not elapsed).
    manager._sandbox_maintenance_tick()
    assert list(tmp_path.glob(f"{ws.HEARTBEAT_FILENAME_PREFIX}*.json")) == heartbeats


def test_maintenance_is_fail_soft_per_instance():
    class _ExplodingLauncher(_FakeLauncher):
        def export_snapshot(self, sandbox_id, *, restore_confirmed):
            raise RuntimeError("execd gone")

    launcher = _ExplodingLauncher()
    manager = _manager(launcher=launcher)
    manager.get_user_webui_url(7, "u7", None, required_isolation="sandboxed")
    manager._maintain_sandboxed_instances()  # must not raise
    # The renew after the failed export never ran for this instance…
    assert launcher.renew_calls == []


def test_unreadable_snapshot_degrades_start_and_suspends_exports():
    """T-E: an existing-but-unreadable snapshot is not a first launch — the
    pod starts with an EMPTY history, exports stay suspended (the unreadable
    file is left for an operator), and the launch is recorded honestly."""

    class _UnreadableSnapshotLauncher(_FakeLauncher):
        def load_snapshot(self, user_id):
            from app.services.webui_sandbox import SnapshotUnreadableError

            raise SnapshotUnreadableError(f"snapshot for user {user_id}: EACCES")

    launcher = _UnreadableSnapshotLauncher()
    manager = _manager(launcher=launcher)
    _url, _token = manager.get_user_webui_url(7, "u7", None, required_isolation="sandboxed")

    instance = manager.get_user_instance(7)
    assert instance.restore_confirmed is False  # exports suspended
    # The launch itself ran with the degraded (empty) history.
    assert len(launcher.launch_calls) == 1
    assert launcher.launch_calls[0]["snapshot"] is None
    # Maintenance and teardown both honor the suspended export guard.
    manager._maintain_sandboxed_instances()
    assert launcher.exports == [False]
    manager.stop_all_instances()
    assert launcher.destroy_calls[0]["restore_confirmed"] is False


def test_shutdown_exports_and_destroys_sandboxed_instances():
    launcher = _FakeLauncher()
    manager = _manager(launcher=launcher)
    manager.get_user_webui_url(7, "u7", None, required_isolation="sandboxed")
    proxy = manager.get_user_instance(7).proxy

    manager.stop_all_instances()
    assert len(launcher.destroy_calls) == 1
    assert proxy.stopped
    assert manager.get_user_instance(7) is None


# ── single-user sandboxed ─────────────────────────────────────────────


def test_single_user_sandboxed_url_uses_proxy_port_not_3100():
    launcher = _FakeLauncher()
    # Range starts at 3200 so the allocator's answer is provably NOT the
    # hardcoded single-user 3100.
    manager = _manager(multi_user=False, launcher=launcher, port_range_start=3200)
    url, token = manager.get_user_webui_url(
        3, "u3", "http://192.168.1.5:19888", required_isolation="sandboxed"
    )
    instance = manager._single_user_instance
    assert instance.form == "sandboxed"
    assert instance.port == 3200  # §7.2: allocator port, not the hardcoded 3100
    assert url == f"http://192.168.1.5:{instance.port}"
    assert token.startswith("v2:3:")

    # Hit: same pod, fresh token.
    url2, token2 = manager.get_user_webui_url(
        3, "u3", "http://192.168.1.5:19888", required_isolation="sandboxed"
    )
    assert len(launcher.launch_calls) == 1
    assert token2 != token

    # Shutdown path (server.py SIGTERM → shutdown_webui_manager).
    manager.stop_all_instances()
    assert len(launcher.destroy_calls) == 1
    assert instance.proxy.stopped


def test_single_user_sandboxed_token_mints_for_the_requester():
    """T-C: the shared single-user sandboxed instance must mint each token
    for the REQUESTING user. The reuse branch minted with the pod creator's
    user_id, so a second user's token validated as the creator — including
    against the admin paths URL_TOKEN_ALLOWED_PATHS admits."""
    launcher = _FakeLauncher()
    manager = _manager(multi_user=False, launcher=launcher, port_range_start=3200)
    _url, token_creator = manager.get_user_webui_url(
        3, "u3", "http://192.168.1.5:19888", required_isolation="sandboxed"
    )
    instance = manager._single_user_instance
    assert len(launcher.launch_calls) == 1  # pod reused below, never recreated

    _url2, token_other = manager.get_user_webui_url(
        9, "u9", "http://192.168.1.5:19888", required_isolation="sandboxed"
    )
    assert len(launcher.launch_calls) == 1  # same shared pod
    assert token_other.startswith("v2:9:")

    # Each token validates (against the instance secret) to ITS OWN user.
    assert manager.validate_token(token_creator)[:2] == (True, 3)
    assert manager.validate_token(token_other)[:2] == (True, 9)
    # And the pod-creator subject never leaks into the second user's token.
    assert not token_other.startswith("v2:3:")
    assert instance.user_id == 3  # the pod still belongs to its creator


def test_single_user_sandboxed_instance_resolves_for_proxy_token_lifecycle(monkeypatch, tmp_path):
    """M1: the pod's baked-in LLM proxy token only validates while its
    instance resolves as alive — api_key_proxy._webui_instance_alive goes
    through WebUIManager.get_user_instance, which must recognize the
    single-user SANDBOXED registry (a plain _instances lookup returns None in
    single-user mode, so every pod-side LLM call would 401)."""
    import os

    from app.modules.workspace.api_key_proxy import APIKeyProxyService

    launcher = _FakeLauncher()
    manager = _manager(multi_user=False, launcher=launcher)
    manager.get_user_webui_url(3, "u3", None, required_isolation="sandboxed")
    instance = manager._single_user_instance

    # Resolution: the shared instance answers only for the user whose pod
    # token it carries; other users (multi-user registry empty here) do not.
    assert manager.get_user_instance(3) is instance
    assert manager.get_user_instance(4) is None

    with patch.dict(os.environ, {"OPENACE_ENCRYPTION_KEY": "unit-3378-encryption-key"}):
        service = APIKeyProxyService(db_path=str(tmp_path / "proxy_tokens.db"))
    revoked: list[tuple[str, str]] = []
    monkeypatch.setattr(
        service,
        "revoke_proxy_tokens_for_session",
        lambda session_id, reason="session_revoked": revoked.append((session_id, reason)) or 1,
    )
    monkeypatch.setattr(
        "app.modules.workspace.api_key_proxy.get_api_key_proxy_service", lambda: service
    )
    monkeypatch.setattr("app.services.webui_manager.get_webui_manager", lambda: manager)

    # The REAL _webui_instance_alive path resolves the instance and keeps the
    # pod's proxy token alive while the instance lives...
    assert service._webui_instance_alive(f"webui:{instance.user_id}", instance.user_id) is True
    # ...and stopping revokes the session (Q1) and kills the resolution.
    manager.stop_all_instances()
    assert revoked == [("webui:3", "webui_stopped")]
    assert service._webui_instance_alive("webui:3", 3) is False


def test_single_user_sandboxed_proxy_activity_updates_last_activity():
    """m1: the proxy's on_activity heartbeat reaches the instance."""
    launcher = _FakeLauncher()
    manager = _manager(multi_user=False, launcher=launcher)
    manager.get_user_webui_url(3, "u3", None, required_isolation="sandboxed")
    instance = manager._single_user_instance
    assert instance.proxy.on_activity is not None
    instance.last_activity = datetime.now() - timedelta(hours=3)
    stale = instance.last_activity
    instance.proxy.on_activity()
    assert instance.last_activity > stale


def test_multi_user_sandboxed_proxy_activity_updates_last_activity():
    """m1: same wiring on the multi-user branch (shared _launch_sandboxed)."""
    launcher = _FakeLauncher()
    manager = _manager(launcher=launcher)
    manager.get_user_webui_url(7, "u7", None, required_isolation="sandboxed")
    instance = manager.get_user_instance(7)
    assert instance.proxy.on_activity is not None
    instance.last_activity = datetime.now() - timedelta(hours=3)
    stale = instance.last_activity
    instance.proxy.on_activity()
    assert instance.last_activity > stale


def test_idle_single_user_sandboxed_instance_is_reaped():
    """m1: the single-user sandboxed instance holds a pod + a proxy port —
    the idle reaper must cover it, not just the _instances registry."""
    launcher = _FakeLauncher()
    manager = _manager(multi_user=False, launcher=launcher)
    manager.get_user_webui_url(3, "u3", None, required_isolation="sandboxed")
    instance = manager._single_user_instance
    proxy = instance.proxy

    manager.cleanup_idle_instances()  # fresh start → not idle yet
    assert launcher.destroy_calls == []

    instance.last_activity = datetime.now() - timedelta(hours=2)
    manager.cleanup_idle_instances()
    assert len(launcher.destroy_calls) == 1
    assert proxy.stopped
    assert manager._single_user_instance is None
    assert instance.port not in manager._port_allocations


def test_idle_local_single_user_instance_is_never_reaped():
    """m1: the LOCAL single-user instance keeps its historic semantics
    (shared, fixed port, no remote resource) — only the sandboxed form is
    idle-reaped."""
    manager = _manager(multi_user=False)
    local = WebUIInstance(user_id=3, system_account="u3", port=3100, form="local")
    local.is_alive = lambda: True
    local.last_activity = datetime.now() - timedelta(hours=2)
    manager._single_user_instance = local

    manager.cleanup_idle_instances()
    assert manager._single_user_instance is local


def test_single_user_local_request_restarts_live_sandboxed_instance(monkeypatch):
    """MINOR-2: a single-user SANDBOXED instance is alive while the request
    resolves to the LOCAL form (e.g. the backend was unconfigured between
    launches). The old branch answered "already running" with the hardcoded
    3100 plus a global-secret token the remote pod cannot validate; the fix
    mirrors the multi-user form-mismatch stop-and-restart."""
    launcher = _FakeLauncher()
    manager = _manager(multi_user=False, launcher=launcher)
    sandboxed = WebUIInstance(
        user_id=3,
        system_account="u3",
        port=45678,
        form="sandboxed",
        sandbox_id="sb-live",
        token_secret="s" * 64,
        launcher=launcher,
        proxy=_FakeProxy(sandbox_id="sb-live", upstream_resolver=lambda: ("http://up", {})),
    )
    sandboxed.is_alive = lambda: True
    manager._single_user_instance = sandboxed
    manager._port_allocations[45678] = (3, "sandboxed")

    # The local start path must not spawn a real webui process.
    def fake_launch(user_id, system_account, port, base_url):
        process = MagicMock()
        process.pid = 4242
        return process, MagicMock()

    manager._launch_webui_process = MagicMock(side_effect=fake_launch)
    manager._wait_for_service_ready = MagicMock(return_value=True)

    # The snapshot verifies only os_user (sandbox probe no longer passes), so
    # the strongest verified form satisfying the request is the LOCAL form.
    os_user_snapshot = wic.IsolationCapabilitySnapshot(
        supported=True,
        backend=wic.BACKEND_PER_USER,
        isolation_level=wic.ISOLATION_LEVEL_OS_USER,
        enforced=wic._OS_USER_ENFORCED,
        unsupported=wic._OS_USER_UNSUPPORTED,
        reasons=(),
    )
    monkeypatch.setattr(wic, "build_workspace_isolation_snapshot", lambda mgr: os_user_snapshot)
    monkeypatch.setattr(wic, "resolve_required_floor", lambda config, snap: "os_user")

    url, token = manager.get_user_webui_url(3, "u3", "http://192.168.1.5:19888")

    # The sandboxed instance was stopped-and-restarted, not "already running":
    assert len(launcher.destroy_calls) == 1
    assert launcher.destroy_calls[0]["sandbox_id"] == "sb-live"
    assert launcher.destroy_calls[0]["final_export"] is True
    assert sandboxed.proxy.stopped
    assert 45678 not in manager._port_allocations
    # ...and the replacement is a LOCAL single-user instance on the fixed port.
    replacement = manager._single_user_instance
    assert replacement is not sandboxed
    assert replacement.form == "local"
    assert replacement.port == 3100
    assert url == "http://192.168.1.5:3100"
    # The token is the local-form (global-secret) token for 3100 — minted for
    # the NEW instance, not a stale sandboxed-form artifact.
    assert token.startswith("v2:3:3100:")
    assert manager._launch_webui_process.call_count == 1


# ── prestart reorder (§7.7) ───────────────────────────────────────────


def _snapshot_at_level(level: str) -> wic.IsolationCapabilitySnapshot:
    """A capability snapshot reporting *level* (T-B invariant fixtures)."""
    if level == wic.ISOLATION_LEVEL_SANDBOXED:
        return wic.IsolationCapabilitySnapshot(
            supported=True,
            backend="opensandbox:kata",
            isolation_level=level,
            enforced=wic._SANDBOXED_ENFORCED,
            unsupported=wic._SANDBOXED_UNSUPPORTED,
            reasons=(),
        )
    return wic.IsolationCapabilitySnapshot(
        supported=True,
        backend=wic.BACKEND_PER_USER,
        isolation_level=level,
        enforced=wic._OS_USER_ENFORCED,
        unsupported=wic._OS_USER_UNSUPPORTED,
        reasons=(),
    )


@pytest.mark.parametrize(
    ("pin", "snapshot_level", "request_param", "expected_form"),
    [
        # The invariant: the default launch form is the strongest VERIFIED
        # form satisfying max(pin floor, request) — same source as the
        # contract snapshot. The second row is the T-B regression: the
        # entrypoint pins `os_user` on multi-user deployments, and the old
        # code silently launched local processes on a sandboxed-capable one.
        ("", wic.ISOLATION_LEVEL_SANDBOXED, "", "sandboxed"),
        ("os_user", wic.ISOLATION_LEVEL_SANDBOXED, "", "sandboxed"),
        ("os_user", wic.ISOLATION_LEVEL_SANDBOXED, "os_user", "sandboxed"),
        ("os_user", wic.ISOLATION_LEVEL_OS_USER, "", "local"),
        ("", wic.ISOLATION_LEVEL_OS_USER, "", "local"),
        ("", wic.ISOLATION_LEVEL_NONE, "", "local"),
        # An explicit sandboxed request keeps its fail-closed shape.
        ("os_user", wic.ISOLATION_LEVEL_SANDBOXED, "sandboxed", "sandboxed"),
    ],
)
def test_resolve_form_invariant_strongest_verified_form(
    monkeypatch, pin, snapshot_level, request_param, expected_form
):
    launcher = _FakeLauncher()
    manager = _manager(launcher=launcher, required_isolation_level=pin)
    snap = _snapshot_at_level(snapshot_level)
    monkeypatch.setattr(wic, "build_workspace_isolation_snapshot", lambda mgr: snap)

    # What the /user-url gate hands the manager: max(pin floor, request).
    effective = pin or snapshot_level
    if request_param and wic.isolation_level_at_least(request_param, effective):
        effective = request_param
    assert manager._resolve_form(effective) == expected_form

    # And a real DEFAULT request (no parameter) through get_user_webui_url
    # lands on the form the snapshot's isolation level dictates: sandboxed
    # capability → the pod form, regardless of the pin. (Local rows stub the
    # OS-process start — launching a real webui is not this test's business.)
    form = "sandboxed" if snap.isolation_level == wic.ISOLATION_LEVEL_SANDBOXED else "local"
    if form == "local":
        stub = WebUIInstance(user_id=7, system_account="u7", port=3110, form="local")
        manager._start_instance_internal = lambda *a, **kw: stub
    url, token = manager.get_user_webui_url(
        7,
        "u7",
        "http://192.168.1.5:19888",
        required_isolation=effective if request_param else "",
    )
    instance = manager._instances.get(7)
    chosen = getattr(instance, "form", "local") if instance else "local"
    assert chosen == form
    if form == "sandboxed":
        assert token.startswith("v2:7:")
        # The URL carries the LOCAL proxy port of the pod instance (never a
        # global-secret 3100 token path).
        assert url == f"http://192.168.1.5:{instance.port}"


def test_prestart_proceeds_without_mapping_when_floor_is_sandboxed(monkeypatch):
    launcher = _FakeLauncher()
    manager = _manager(launcher=launcher)
    spawned = []
    monkeypatch.setattr("app.services.webui_manager.gevent.spawn", lambda fn: spawned.append(fn))
    from app.services import workspace_isolation_contract as wic

    class _SandboxSnapshot(wic.IsolationCapabilitySnapshot):
        pass

    snap = wic.IsolationCapabilitySnapshot(
        supported=True,
        backend="opensandbox:kata",
        isolation_level=wic.ISOLATION_LEVEL_SANDBOXED,
        enforced=wic._SANDBOXED_ENFORCED,
        unsupported=wic._SANDBOXED_UNSUPPORTED,
        reasons=(),
    )
    monkeypatch.setattr(wic, "build_workspace_isolation_snapshot", lambda mgr: snap, raising=True)
    monkeypatch.setattr(wic, "resolve_required_floor", lambda config, s: "sandboxed")

    manager.prestart_user_instance_async(7, "", "http://h")
    assert len(spawned) == 1  # sandboxed prestart: no mapping required
    # Run the spawned starter: it launches the sandbox form.
    spawned[0]()
    assert len(launcher.launch_calls) == 1


def test_prestart_still_requires_mapping_for_os_user(monkeypatch):
    manager = _manager()
    spawned = []
    monkeypatch.setattr("app.services.webui_manager.gevent.spawn", lambda fn: spawned.append(fn))
    from app.services import workspace_isolation_contract as wic

    monkeypatch.setattr(wic, "resolve_required_floor", lambda config, s: "os_user")
    monkeypatch.setattr(
        wic,
        "evaluate_isolation_requirement",
        lambda required, **kw: None,  # gate passes; the no-account rule must fire
    )
    manager.prestart_user_instance_async(7, "", "http://h")
    assert spawned == []


# ── route passes the effective level through ─────────────────────────


MOCK_USER = {"id": 7, "username": "alice", "role": "user", "tenant_id": 1}


def test_webui_token_auth_paths_validate_against_the_singleton(app, client, monkeypatch):
    """T-G: both one-shot WebUI-manager construction sites (the workspace
    blueprint's before_request and session_access._set_user_from_webui_token)
    must validate against the manager SINGLETON — a fresh WebUIManager() has
    neither the running instances nor their per-instance secrets, so every
    sandboxed-instance token validated to a constant 401."""
    launcher = _FakeLauncher()
    manager = _manager(launcher=launcher)
    _url, token = manager.get_user_webui_url(7, "u7", None, required_isolation="sandboxed")
    # Sanity: the token needs the instance (per-instance secret) to validate.
    assert manager.validate_token(token)[0] is True

    monkeypatch.setattr(
        "app.services.webui_manager.get_webui_manager", lambda: manager, raising=True
    )
    # Keep the /user-url flow after auth on the (fake-launcher) sandbox path —
    # no real webui process may be spawned from a unit test.
    sandbox_snapshot = wic.IsolationCapabilitySnapshot(
        supported=True,
        backend="opensandbox:kata",
        isolation_level=wic.ISOLATION_LEVEL_SANDBOXED,
        enforced=wic._SANDBOXED_ENFORCED,
        unsupported=wic._SANDBOXED_UNSUPPORTED,
        reasons=(),
    )
    monkeypatch.setattr(wic, "build_workspace_isolation_snapshot", lambda mgr: sandbox_snapshot)

    # Path 1: the workspace blueprint's before_request (load_user). A fresh
    # WebUIManager() would 401 here; the singleton resolves user 7 and the
    # request proceeds into the route handler.
    with (
        patch("app.repositories.user_repo.UserRepository") as repo_cls,
        patch("app.routes.workspace._load_user_from_token", return_value=None),
    ):
        repo_cls.return_value.get_user_by_id.return_value = {
            **MOCK_USER,
            "system_account": "",
        }
        resp = client.get(f"/api/workspace/user-url?token={token}")
    assert resp.status_code == 200

    # Path 2: session_access._set_user_from_webui_token (load_remote_user).
    from flask import g

    from app.modules.workspace.session_access import load_remote_user

    with (
        patch("app.repositories.user_repo.UserRepository") as repo_cls,
        patch("app.modules.workspace.session_access._load_user_from_token", return_value=None),
        app.test_request_context(f"/?token={token}"),
    ):
        repo_cls.return_value.get_user_by_id.return_value = {**MOCK_USER}
        assert load_remote_user() is None  # authenticated, no rejection tuple
        assert g.user_id == 7


def test_user_url_route_forwards_effective_level(app, client, monkeypatch):
    class _StubManager:
        def __init__(self):
            import app.services.webui_manager as wm

            self.config = wm.WorkspaceConfig(enabled=True, multi_user_mode=True)
            self.received_isolation = None

        def per_user_launch_readiness(self):
            return None

        def supports_per_user_launch(self, system_account):
            return True, None

        def get_user_webui_url(self, user_id, system_account, host_url, required_isolation=""):
            self.received_isolation = required_isolation
            return "http://127.0.0.1:3123", "tok"

        def update_user_activity(self, user_id):
            pass

    stub = _StubManager()
    # The gate consults the capability snapshot; a sandboxed snapshot lets a
    # sandboxed request through so the route can reach the manager fork.
    sandbox_snapshot = wic.IsolationCapabilitySnapshot(
        supported=True,
        backend="opensandbox:kata",
        isolation_level=wic.ISOLATION_LEVEL_SANDBOXED,
        enforced=wic._SANDBOXED_ENFORCED,
        unsupported=wic._SANDBOXED_UNSUPPORTED,
        reasons=(),
    )
    monkeypatch.setattr(wic, "build_workspace_isolation_snapshot", lambda mgr: sandbox_snapshot)
    with (
        patch("app.repositories.user_repo.UserRepository") as repo_cls,
        patch("app.services.webui_manager.get_webui_manager", return_value=stub),
    ):
        repo_cls.return_value.get_user_by_id.return_value = {
            **MOCK_USER,
            "system_account": "alice_acct",
        }
        client.set_cookie("session_token", "test-token")
        with patch("app.routes.workspace._load_user_from_token", return_value=MOCK_USER):
            resp = client.get("/api/workspace/user-url?required_isolation=sandboxed")
    assert resp.status_code == 200
    assert stub.received_isolation == "sandboxed"


def test_user_url_route_surfaces_sandbox_error_code(app, client, monkeypatch):
    """MINOR-5: a launcher SandboxWebuiError must cross the API boundary with
    its machine-readable reason code (gate-rejection body shape), not collapse
    into the generic 500 "Internal server error"."""
    from app.services.webui_sandbox import SandboxWebuiError

    class _StubManager:
        def __init__(self):
            import app.services.webui_manager as wm

            self.config = wm.WorkspaceConfig(enabled=True, multi_user_mode=True)

        def per_user_launch_readiness(self):
            return None

        def supports_per_user_launch(self, system_account):
            return True, None

        def get_user_webui_url(self, user_id, system_account, host_url, required_isolation=""):
            raise SandboxWebuiError(
                "sandbox gateway at https://lifecycle.internal:9443/v1 could not "
                "resolve the pod's webui endpoint (allowlist=['lifecycle.internal']) "
                "for sandbox sb-1234",
                reason_code="sandbox_endpoint_unresolved",
            )

        def update_user_activity(self, user_id):
            pass

    stub = _StubManager()
    sandbox_snapshot = wic.IsolationCapabilitySnapshot(
        supported=True,
        backend="opensandbox:kata",
        isolation_level=wic.ISOLATION_LEVEL_SANDBOXED,
        enforced=wic._SANDBOXED_ENFORCED,
        unsupported=wic._SANDBOXED_UNSUPPORTED,
        reasons=(),
    )
    monkeypatch.setattr(wic, "build_workspace_isolation_snapshot", lambda mgr: sandbox_snapshot)
    with (
        patch("app.repositories.user_repo.UserRepository") as repo_cls,
        patch("app.services.webui_manager.get_webui_manager", return_value=stub),
    ):
        repo_cls.return_value.get_user_by_id.return_value = {
            **MOCK_USER,
            "system_account": "alice_acct",
        }
        client.set_cookie("session_token", "test-token")
        with patch("app.routes.workspace._load_user_from_token", return_value=MOCK_USER):
            resp = client.get("/api/workspace/user-url?required_isolation=sandboxed")
    assert resp.status_code == 502  # upstream sandbox refusal, not a policy 400
    body = resp.get_json()
    assert body["success"] is False
    assert body["error_code"] == "sandbox_endpoint_unresolved"
    # Sanitized per-code wording — the raw exception (internal lifecycle URL,
    # execd allowlist contents, sandbox id) must NOT reach the response body;
    # it stays in the server log (security review on bdf74d9e).
    assert "lifecycle.internal" not in body["error"]
    assert "allowlist=" not in body["error"]
    assert "sb-1234" not in body["error"]
    assert body["error"]  # a real message, not "Internal server error"
    assert body["reasons"][0]["code"] == "sandbox_endpoint_unresolved"
    assert "lifecycle.internal" not in body["reasons"][0]["message"]
    assert body["isolation"]["isolation_level"] == "sandboxed"


# ── allocator form-awareness ──────────────────────────────────────────


def test_allocator_keys_ports_by_user_and_form():
    manager = _manager()
    p_local = manager.allocate_port(9, "local")
    # STILL-HOLDING check keys on (user_id, form) (m4): a repeated same-form
    # allocate returns the held port, but a cross-form allocate for the SAME
    # user must never steal the other form's port.
    assert manager.allocate_port(9, "local") == p_local
    p_other = manager.allocate_port(9, "sandboxed")
    assert p_other != p_local
    assert manager._port_allocations[p_other] == (9, "sandboxed")
    assert p_other in range(3100, 3201)
    # After release, a same-form restart prefers its previous port (SEC-Q4).
    manager.release_port(p_local, "local")
    assert manager.allocate_port(9, "local") == p_local
