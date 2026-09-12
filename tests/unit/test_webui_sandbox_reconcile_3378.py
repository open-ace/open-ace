"""Issue #3378 (§7.5/§7.6): webui-pod orphan reconcile + snapshot history.

Reconcile: positive-trigger env + TESTING guard + fail-soft, process-generation
discrimination, export gated on the pod's own restore-done marker (FEAS-R4-1),
export-before-destroy, idempotent re-entry, and the provider.reconcile_orphans
exclusion contract (N7). History: bounded tar round-trip, webui-<user_id> slot
under an independent root, over-ceiling skip keeping the last good snapshot,
and the D6 guard on both decision paths.
"""

from __future__ import annotations

import io
import json
import os
import tarfile
from unittest.mock import patch

import pytest

from app.modules.workspace.autonomous.sandbox.opensandbox.config import parse_backend_config
from app.modules.workspace.autonomous.sandbox.opensandbox.fake_server import FakeOpenSandboxApi
from app.modules.workspace.autonomous.sandbox.opensandbox.policy import PROVIDER_NAME
from app.services import webui_sandbox as ws
from app.services import workspace_isolation_contract as wic

pytestmark = [pytest.mark.issue(3378)]


@pytest.fixture(autouse=True)
def _reset_contract_memo():
    """Isolate the boot-probe contract memo (a launch here can set it)."""
    wic._reset_sandbox_runtime_verification()
    yield
    wic._reset_sandbox_runtime_verification()


_AGENT_IMAGE = "ghcr.io/open-ace/agent@sha256:" + "a" * 64
_WEBUI_IMAGE = "ghcr.io/open-ace/webui@sha256:" + "b" * 64
_INSTALLATION = "openace-test"

_FULL_ATTESTATIONS = {
    "egress_enforced": True,
    "egress_mode_dns_nft": True,
    "metadata_cidr_blocked": True,
    "execd_token_required": True,
    "secure_access_required": True,
    "nonroot_enforced": True,
    "readonly_rootfs": True,
    "seccomp_runtime_default": True,
    "dedicated_service_account": True,
    "pod_pids_limit": 512,
    "ephemeral_storage_enforced": True,
}


def _backend():
    return parse_backend_config(
        {
            "installation_id": _INSTALLATION,
            "default_tier": "kata",
            "endpoints": {
                "kata": {
                    "base_url": "http://osb:8080/v1",
                    "api_key_env": "OSB_KEY",
                    "execd_token_env": "OSB_EXECD_TOKEN",
                    "runtime_class": "kata-qemu",
                    "default_image": _AGENT_IMAGE,
                    "webui_image": _WEBUI_IMAGE,
                    "egress_allow_hosts": ["api.anthropic.com"],
                    "attestations": _FULL_ATTESTATIONS,
                }
            },
            "image_allowlist": [_AGENT_IMAGE, _WEBUI_IMAGE],
            "sandbox_ttl_seconds": 3600,
        }
    )


def _webui_metadata(*, generation: str, owner: str = "7") -> dict:
    return {
        "openace.provider": PROVIDER_NAME,
        "openace.installation": _INSTALLATION,
        "openace.webui.kind": "webui",
        "openace.webui.process_generation": generation,
        "openace.webui.owner": owner,
    }


def _confirm_restore_cp(root, sandbox_id) -> None:
    """Write the control-plane restore-confirmation record (T-E export gate)."""
    record = root / ws.RESTORE_CONFIRMED_DIRNAME / sandbox_id
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(json.dumps({"sandbox_id": sandbox_id}), encoding="utf-8")


def _tar_bytes(name: str = "chats/session.jsonl", payload: bytes = b"[1]") -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        info = tarfile.TarInfo(name)
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


# ── reconcile: generation discrimination + export gating ───────────────


def test_reconcile_spares_own_generation_and_untouched_non_webui(tmp_path):
    fake = FakeOpenSandboxApi()
    mine = fake.create_sandbox(
        {"metadata": _webui_metadata(generation=ws.current_process_generation())}
    )
    stale = fake.create_sandbox({"metadata": _webui_metadata(generation="deadbeef")})
    agent = fake.create_sandbox(
        {
            "metadata": {
                "openace.provider": PROVIDER_NAME,
                "openace.installation": _INSTALLATION,
            }
        }
    )
    destroyed = ws.reconcile_webui_orphans(
        backend_config=_backend(),
        api_factory=lambda endpoint: fake,
        state_root_override=str(tmp_path),
    )
    assert destroyed == [stale["id"]]
    assert mine["id"] not in fake.deleted
    assert agent["id"] not in fake.deleted  # not our namespace
    # The list query carried the full belt-and-braces filter set.
    assert fake.list_filters == [
        {
            "openace.provider": PROVIDER_NAME,
            "openace.installation": _INSTALLATION,
            "openace.webui.kind": "webui",
        }
    ]


def test_reconcile_exports_orphan_with_cp_record_and_persists_by_owner(tmp_path):
    fake = FakeOpenSandboxApi()
    orphan = fake.create_sandbox({"metadata": _webui_metadata(generation="deadbeef", owner="7")})
    sid = orphan["id"]
    # T-E: the export gate is the CP record; the pod has a state tree.
    _confirm_restore_cp(tmp_path, sid)
    fake.uploaded[sid][ws.WEBUI_STATE_TAR_PATH] = _tar_bytes()

    destroyed = ws.reconcile_webui_orphans(
        backend_config=_backend(),
        api_factory=lambda endpoint: fake,
        state_root_override=str(tmp_path),
    )
    assert destroyed == [sid]
    stored = tmp_path / "webui-7.tar"
    assert stored.exists()
    with tarfile.open(stored) as tar:
        assert tar.getnames() == ["chats/session.jsonl"]
    # Export-before-destroy ordering: the tar command ran before the delete.
    commands = [b["command"] for b in fake.command_bodies]
    assert any("tar -cf" in c for c in commands)


def test_reconcile_export_precedes_destroy_per_orphan(tmp_path):
    """m6c: strict ORDERING, not just occurrence — the export (tar + download)
    of an orphan must complete before its delete_sandbox, or the destroy of a
    not-yet-exported pod would lose the user's last session snapshot."""
    fake = FakeOpenSandboxApi()
    orphan = fake.create_sandbox({"metadata": _webui_metadata(generation="deadbeef", owner="7")})
    sid = orphan["id"]
    _confirm_restore_cp(tmp_path, sid)
    fake.uploaded[sid][ws.WEBUI_STATE_TAR_PATH] = _tar_bytes()

    # Record the API calls as a single ordered event stream.
    events: list[tuple[str, str]] = []
    original_run = fake.run_command
    original_download = fake.download_file
    original_delete = fake.delete_sandbox

    def run_recorder(sandbox_id, body):
        events.append(("run_command", str(body.get("command", ""))))
        return original_run(sandbox_id, body)

    def download_recorder(sandbox_id, path, *, max_bytes=0):
        events.append(("download_file", path))
        return original_download(sandbox_id, path, max_bytes=max_bytes)

    def delete_recorder(sandbox_id):
        events.append(("delete_sandbox", sandbox_id))
        return original_delete(sandbox_id)

    fake.run_command = run_recorder
    fake.download_file = download_recorder
    fake.delete_sandbox = delete_recorder

    destroyed = ws.reconcile_webui_orphans(
        backend_config=_backend(),
        api_factory=lambda endpoint: fake,
        state_root_override=str(tmp_path),
    )
    assert destroyed == [sid]
    kinds = [kind for kind, _detail in events]
    # T-E: no in-pod marker probe anymore (the gate reads the CP record from
    # disk) — the ordering that matters is: state tar → state download → delete.
    tar_index = next(
        i
        for i, (kind, detail) in enumerate(events)
        if kind == "run_command" and "tar -cf" in detail
    )
    state_download_index = next(
        i
        for i, (kind, detail) in enumerate(events)
        if kind == "download_file" and detail == ws.WEBUI_STATE_TAR_PATH
    )
    delete_index = kinds.index("delete_sandbox")
    assert tar_index < state_download_index < delete_index


def test_reconcile_skips_export_for_degraded_orphan_without_cp_record(tmp_path):
    fake = FakeOpenSandboxApi()
    orphan = fake.create_sandbox({"metadata": _webui_metadata(generation="deadbeef", owner="7")})
    sid = orphan["id"]
    # NO CP restore record: a degraded launch whose confirmation never
    # persisted. Exporting its tree would overwrite the user's good snapshot
    # with an empty one (FEAS-R4-1) — so no export, but still destroyed.
    pre_existing = tmp_path / "webui-7.tar"
    pre_existing.write_bytes(_tar_bytes("old/keep.tar", b"old"))

    destroyed = ws.reconcile_webui_orphans(
        backend_config=_backend(),
        api_factory=lambda endpoint: fake,
        state_root_override=str(tmp_path),
    )
    assert destroyed == [sid]
    assert not any("tar -cf" in b["command"] for b in fake.command_bodies)
    assert pre_existing.read_bytes() == _tar_bytes("old/keep.tar", b"old")


def test_reconcile_ignores_forged_pod_marker_without_cp_record(tmp_path):
    """T-E: the in-pod /workspace/.openace-restore-done marker is writable by
    the supervised process — a malicious pod can forge it to push an
    attacker-shaped tree over the user's stored snapshot. The export gate is
    the CONTROL-PLANE record, so a forged marker alone exports nothing."""
    fake = FakeOpenSandboxApi()
    orphan = fake.create_sandbox({"metadata": _webui_metadata(generation="deadbeef", owner="7")})
    sid = orphan["id"]
    fake.uploaded[sid][ws.RESTORE_MARKER_PATH] = b"1"  # forged by the pod
    fake.uploaded[sid][ws.WEBUI_STATE_TAR_PATH] = _tar_bytes("evil/x", b"evil")
    pre_existing = tmp_path / "webui-7.tar"
    pre_existing.write_bytes(_tar_bytes("old/keep.tar", b"old"))

    destroyed = ws.reconcile_webui_orphans(
        backend_config=_backend(),
        api_factory=lambda endpoint: fake,
        state_root_override=str(tmp_path),
    )
    assert destroyed == [sid]  # destroyed regardless
    assert not any("tar -cf" in b["command"] for b in fake.command_bodies)  # no export
    assert pre_existing.read_bytes() == _tar_bytes("old/keep.tar", b"old")  # snapshot kept


def test_reconcile_delete_clears_cp_record(tmp_path):
    fake = FakeOpenSandboxApi()
    orphan = fake.create_sandbox({"metadata": _webui_metadata(generation="deadbeef", owner="7")})
    sid = orphan["id"]
    _confirm_restore_cp(tmp_path, sid)
    record = tmp_path / ws.RESTORE_CONFIRMED_DIRNAME / sid

    destroyed = ws.reconcile_webui_orphans(
        backend_config=_backend(),
        api_factory=lambda endpoint: fake,
        state_root_override=str(tmp_path),
    )
    assert destroyed == [sid]
    assert not record.exists()  # the id never returns; the record goes too


def test_reconcile_export_failure_does_not_block_destroy(tmp_path):
    fake = FakeOpenSandboxApi(scripted_exit_code=3)
    orphan = fake.create_sandbox({"metadata": _webui_metadata(generation="deadbeef", owner="7")})
    sid = orphan["id"]
    _confirm_restore_cp(tmp_path, sid)

    destroyed = ws.reconcile_webui_orphans(
        backend_config=_backend(),
        api_factory=lambda endpoint: fake,
        state_root_override=str(tmp_path),
    )
    assert destroyed == [sid]  # destroy went through anyway
    assert not (tmp_path / "webui-7.tar").exists()


def test_reconcile_export_tar_runs_under_exec_identity(tmp_path):
    """m5: the exporter's in-pod tar must carry the tier's uid/gid — the
    launcher gets the reconcile loop's endpoint injected explicitly (an
    endpoint-less launcher silently skips the credential switch)."""
    import dataclasses

    cfg = _backend()
    cfg.endpoints["kata"] = dataclasses.replace(cfg.endpoints["kata"], exec_uid=2000, exec_gid=3000)
    fake = FakeOpenSandboxApi()
    orphan = fake.create_sandbox({"metadata": _webui_metadata(generation="deadbeef", owner="7")})
    sid = orphan["id"]
    _confirm_restore_cp(tmp_path, sid)
    fake.uploaded[sid][ws.WEBUI_STATE_TAR_PATH] = _tar_bytes()

    ws.reconcile_webui_orphans(
        backend_config=cfg,
        api_factory=lambda endpoint: fake,
        state_root_override=str(tmp_path),
    )
    tar_bodies = [b for b in fake.command_bodies if "tar -cf" in b["command"]]
    assert tar_bodies, "the orphan export must run the in-pod tar"
    for body in tar_bodies:
        # execd is not attested to run as the exec identity on this tier, so
        # the uid/gid switch from the ENDPOINT (not the 1000 default) must be
        # applied to the export command.
        assert body["uid"] == 2000
        assert body["gid"] == 3000


def test_reconcile_is_idempotent_on_rerun(tmp_path):
    fake = FakeOpenSandboxApi()
    orphan = fake.create_sandbox({"metadata": _webui_metadata(generation="deadbeef", owner="7")})
    _confirm_restore_cp(tmp_path, orphan["id"])
    fake.uploaded[orphan["id"]][ws.WEBUI_STATE_TAR_PATH] = _tar_bytes()

    first = ws.reconcile_webui_orphans(
        backend_config=_backend(),
        api_factory=lambda endpoint: fake,
        state_root_override=str(tmp_path),
    )
    second = ws.reconcile_webui_orphans(
        backend_config=_backend(),
        api_factory=lambda endpoint: fake,
        state_root_override=str(tmp_path),
    )
    assert first == [orphan["id"]]
    assert second == []


def test_reconcile_fail_soft_on_corrupt_config(monkeypatch):
    import app.modules.workspace.autonomous.sandbox.opensandbox.config as sbcfg

    def boom(explicit=None):
        raise sbcfg.SandboxConfigError("corrupt json")

    monkeypatch.setattr(sbcfg, "load_backend_config", boom)
    assert ws.reconcile_webui_orphans() == []


def test_reconcile_ignores_foreign_installation_client_side(tmp_path):
    fake = FakeOpenSandboxApi()
    # A server that ignored the query filter hands us another installation's
    # pod — the client-side re-check must spare it.
    foreign = fake.create_sandbox(
        {
            "metadata": {
                "openace.provider": PROVIDER_NAME,
                "openace.installation": "other-install",
                "openace.webui.kind": "webui",
                "openace.webui.process_generation": "deadbeef",
            }
        }
    )
    destroyed = ws.reconcile_webui_orphans(
        backend_config=_backend(),
        api_factory=lambda endpoint: fake,
        state_root_override=str(tmp_path),
    )
    assert destroyed == []
    assert foreign["id"] not in fake.deleted


# ── T-D: multi-replica heartbeat mutex ────────────────────────────────


def _write_peer_heartbeat(root, *, ts, pid=12345, boot_id="peer-boot-1"):
    import time as _time

    path = root / f"{ws.HEARTBEAT_FILENAME_PREFIX}{boot_id}-{pid}.json"
    path.write_text(json.dumps({"pid": pid, "boot_id": boot_id, "ts": ts}))
    return path


def test_reconcile_skips_destroy_while_peer_heartbeat_is_fresh(tmp_path):
    """T-D: the k8s manifest ships 3 replicas — generation discrimination
    alone made every newly started replica destroy its siblings' live pods.
    A fresh peer heartbeat means the 'orphan' pods have a live owner."""
    import time

    fake = FakeOpenSandboxApi()
    orphan = fake.create_sandbox({"metadata": _webui_metadata(generation="deadbeef")})
    peer = _write_peer_heartbeat(tmp_path, ts=time.time())

    destroyed = ws.reconcile_webui_orphans(
        backend_config=_backend(),
        api_factory=lambda endpoint: fake,
        state_root_override=str(tmp_path),
    )
    assert destroyed == []
    assert orphan["id"] not in fake.deleted
    # The sweep aborted BEFORE any export/destroy API traffic.
    assert fake.command_bodies == []
    assert peer.exists()  # fresh peer files are never pruned


def test_reconcile_destroys_once_peer_heartbeats_are_stale(tmp_path):
    import time

    fake = FakeOpenSandboxApi()
    orphan = fake.create_sandbox({"metadata": _webui_metadata(generation="deadbeef")})
    sid = orphan["id"]
    _confirm_restore_cp(tmp_path, sid)
    fake.uploaded[sid][ws.WEBUI_STATE_TAR_PATH] = _tar_bytes()
    stale_ts = time.time() - ws.HEARTBEAT_FRESH_WINDOW_SECONDS - 30
    _write_peer_heartbeat(tmp_path, ts=stale_ts)

    destroyed = ws.reconcile_webui_orphans(
        backend_config=_backend(),
        api_factory=lambda endpoint: fake,
        state_root_override=str(tmp_path),
    )
    assert destroyed == [sid]  # every peer expired → the sweep proceeds


def test_reconcile_own_heartbeat_never_blocks_the_sweep(tmp_path):
    fake = FakeOpenSandboxApi()
    orphan = fake.create_sandbox({"metadata": _webui_metadata(generation="deadbeef")})
    # This process's OWN (fresh) heartbeat must not read as a peer.
    own = ws.write_webui_heartbeat(str(tmp_path))
    assert own is not None and own.exists()

    destroyed = ws.reconcile_webui_orphans(
        backend_config=_backend(),
        api_factory=lambda endpoint: fake,
        state_root_override=str(tmp_path),
    )
    assert destroyed == [orphan["id"]]


def test_heartbeat_write_prunes_ancient_files(tmp_path):
    import time

    ancient = _write_peer_heartbeat(
        tmp_path, ts=time.time() - ws.HEARTBEAT_MAX_AGE_SECONDS - 60, pid=999
    )
    fresh = _write_peer_heartbeat(tmp_path, ts=time.time(), pid=888)
    assert ws.write_webui_heartbeat(str(tmp_path)) is not None
    assert not ancient.exists()  # > 24h: pruned
    assert fresh.exists()
    assert ws.fresh_peer_heartbeats(str(tmp_path))  # peer 888 still counts


# ── positive trigger + TESTING guard + separate greenlet ───────────────


def test_maybe_spawn_does_nothing_without_env(monkeypatch):
    spawned = []
    monkeypatch.setenv("PYTEST_VERSION", "")  # even without the guard this must not run
    monkeypatch.delenv("PYTEST_VERSION", raising=False)
    monkeypatch.delenv("TESTING", raising=False)
    monkeypatch.delenv(ws.RECONCILE_ENV, raising=False)
    monkeypatch.setattr("gevent.spawn", lambda fn, *a, **kw: spawned.append(fn))
    assert ws.maybe_spawn_webui_orphan_reconcile() is False
    assert spawned == []


def test_maybe_spawn_blocked_in_test_process(monkeypatch):
    monkeypatch.setenv(ws.RECONCILE_ENV, "1")
    monkeypatch.setenv("PYTEST_VERSION", "1.0")
    assert ws.maybe_spawn_webui_orphan_reconcile() is False
    monkeypatch.delenv("PYTEST_VERSION", raising=False)
    monkeypatch.setenv("TESTING", "1")
    assert ws.maybe_spawn_webui_orphan_reconcile() is False


def test_maybe_spawn_runs_reconcile_on_separate_greenlet_fail_soft(monkeypatch):
    monkeypatch.setenv(ws.RECONCILE_ENV, "1")
    monkeypatch.delenv("PYTEST_VERSION", raising=False)
    monkeypatch.delenv("TESTING", raising=False)
    spawned = []
    monkeypatch.setattr("gevent.spawn", lambda fn, *a, **kw: spawned.append(fn))
    calls = []

    def _fake_reconcile():
        calls.append(1)
        raise RuntimeError("sandbox-backends.json corrupted")  # fail-soft must swallow

    monkeypatch.setattr(ws, "reconcile_webui_orphans", _fake_reconcile)
    assert ws.maybe_spawn_webui_orphan_reconcile() is True
    assert len(spawned) == 1  # spawned, NOT inlined
    spawned[0]()  # the greenlet body swallows the exception
    assert calls == [1]


def test_gunicorn_worker_hook_triggers_gated_reconcile(monkeypatch):
    """The reconcile spawn precedes the serving loop (B1).

    gunicorn's ``Worker.init_process`` ends by calling ``self.run()`` and
    blocks there for the worker's whole life — a hook after
    ``super().init_process()`` would fire only at shutdown. The production
    override therefore lives in ``run()``; this test patches the PARENT's
    ``run`` (the serving loop itself) and asserts the spawn happened before
    it was entered, without masking any of the real control flow above it.
    """
    from gunicorn.workers.ggevent import GeventPyWSGIWorker

    from app.gunicorn_worker import TerminalGeventWorker

    worker = TerminalGeventWorker.__new__(TerminalGeventWorker)
    events: list[str] = []
    monkeypatch.setattr(
        "app.services.webui_sandbox.maybe_spawn_webui_orphan_reconcile",
        lambda: events.append("reconcile-spawned") or True,
    )

    def _serving_loop(self):
        events.append("serving")

    with patch.object(GeventPyWSGIWorker, "run", _serving_loop):
        worker.run()
    assert events == ["reconcile-spawned", "serving"]


# ── provider.reconcile_orphans exclusion contract (N7) ─────────────────


@pytest.mark.security
def test_provider_orphan_sweep_excludes_webui_pods():
    from app.modules.workspace.autonomous.sandbox.opensandbox.provider import OpenSandboxProvider

    fake = FakeOpenSandboxApi()
    webui_pod = fake.create_sandbox(
        {
            "metadata": {
                **_webui_metadata(generation="some-other-process", owner="7"),
                "openace.task_id": "webui-user-7",
                "openace.generation": "1",
            }
        }
    )
    orphan_agent = fake.create_sandbox(
        {
            "metadata": {
                "openace.provider": PROVIDER_NAME,
                "openace.installation": _INSTALLATION,
            }
        }
    )
    provider = OpenSandboxProvider(_backend(), api_factory=lambda endpoint: fake)
    destroyed = provider.reconcile_orphans(live_sandbox_ids=[])
    assert destroyed == [orphan_agent["id"]]
    assert webui_pod["id"] not in fake.deleted


# ── snapshot history (§7.6) ────────────────────────────────────────────


def _launcher_with_pod(fake, tmp_path, *, max_bytes=None):
    launcher = ws.SandboxedWebuiLauncher(
        backend_config=_backend(),
        api_factory=lambda endpoint: fake,
        proxy_service_factory=lambda: None,
        restore_timeout_seconds=2,
        poll_interval_seconds=0.01,
        max_state_bytes=max_bytes,
        state_root_override=str(tmp_path),
    )
    return launcher


class _NoopProxyService:
    def effective_proxy_token_ttl_minutes(self, session_type):
        return 1440

    def generate_proxy_token(self, **kwargs):
        return ""

    def get_tool_model_pool(self, **kwargs):
        return {"models": []}


def test_snapshot_round_trip_via_independent_root(tmp_path):
    fake = FakeOpenSandboxApi(runtime_kernel="Linux version 5.15.0 #1 SMP")
    launcher = _launcher_with_pod(fake, tmp_path)
    launcher._proxy_service_factory = lambda: _NoopProxyService()
    # A previously stored snapshot round-trips into the next launch.
    stored = _tar_bytes("chats/history.jsonl", b"[1,2]")
    (tmp_path / "webui-9.tar").write_bytes(stored)

    result = launcher.launch(
        user_id=9,
        callback_url="http://openace.open-ace.svc.cluster.local:8080",
        snapshot=launcher.load_snapshot(9),
    )
    assert launcher.load_snapshot(9) == stored
    assert fake.uploaded[result.sandbox_id][ws.WEBUI_STATE_TAR_PATH] == stored

    # Export: the pod's tar is downloaded and persisted under webui-<user>.tar.
    fake.uploaded[result.sandbox_id][ws.WEBUI_STATE_TAR_PATH] = _tar_bytes(
        "chats/new.jsonl", b"[3]"
    )
    blob = launcher.export_snapshot(result.sandbox_id, restore_confirmed=True)
    assert blob == _tar_bytes("chats/new.jsonl", b"[3]")
    path = launcher.persist_snapshot(9, blob)
    assert path == tmp_path / "webui-9.tar"
    assert launcher.load_snapshot(9) == blob

    # Independent root (D6): not under the reaper-scanned task roots, and
    # the production default lives under the persistent config directory.
    from app.modules.workspace.autonomous.task_isolation import DEFAULT_TASK_ROOT
    from app.repositories.database import CONFIG_DIR

    assert DEFAULT_TASK_ROOT not in str(path)
    assert str(ws.state_root()) == str(CONFIG_DIR) + "/webui-agent-state"


def test_over_ceiling_export_skipped_and_old_snapshot_kept(tmp_path, caplog):
    fake = FakeOpenSandboxApi()
    launcher = _launcher_with_pod(fake, tmp_path, max_bytes=1024)
    sid = fake.create_sandbox({"metadata": {}})["id"]
    # A tar larger than the ceiling.
    blob = _tar_bytes() + b"\0" * 4096
    fake.uploaded[sid][ws.WEBUI_STATE_TAR_PATH] = blob
    old = _tar_bytes("old/keep.tar", b"old")
    (tmp_path / "webui-5.tar").write_bytes(old)

    with caplog.at_level("WARNING", logger="app.services.webui_sandbox"):
        result = launcher.export_snapshot(sid, restore_confirmed=True, user_id=5)
    assert result is None  # skip, never a truncation
    assert (tmp_path / "webui-5.tar").read_bytes() == old  # last good kept
    # T-I: the freeze is audible as its own sentence naming the subject, the
    # ceiling env var, and the sizes (N > M).
    freeze_logs = [r for r in caplog.records if "frozen until trimmed" in r.message]
    assert freeze_logs, "the over-ceiling skip must log its dedicated warning"
    message = freeze_logs[0].getMessage()
    assert "user 5" in message
    assert ws.STATE_MAX_BYTES_ENV in message  # OPENACE_WEBUI_STATE_MAX_BYTES
    assert str(len(blob)) in message  # N (actual size)
    assert "1024" in message  # M (ceiling)
    assert "keeping previous snapshot" in message


def test_destroy_deletes_pod_even_when_final_export_raises(tmp_path):
    """T-I: an exception escaping the final export must never skip the
    delete — an idle webui pod leaking forever is worse than a lost export."""
    from app.modules.workspace.autonomous.sandbox.opensandbox.client import OpenSandboxApiError

    fake = FakeOpenSandboxApi()
    launcher = _launcher_with_pod(fake, tmp_path)
    sid = fake.create_sandbox({"metadata": {}})["id"]

    def _boom(sandbox_id, *, restore_confirmed, user_id=None):
        raise OpenSandboxApiError("execd exploded mid-export", status_code=500)

    launcher.export_snapshot = _boom  # type: ignore[method-assign]
    launcher.destroy(sid, 5, restore_confirmed=True, final_export=True)
    assert sid in fake.deleted  # delete_sandbox ran regardless


def test_state_max_bytes_env_override(monkeypatch):
    monkeypatch.setenv(ws.STATE_MAX_BYTES_ENV, "2048")
    assert ws.state_max_bytes() == 2048
    monkeypatch.delenv(ws.STATE_MAX_BYTES_ENV)
    assert ws.state_max_bytes() == ws.DEFAULT_STATE_MAX_BYTES == 16 * 1024 * 1024


def test_unconfirmed_restore_never_exports_local_path(tmp_path):
    fake = FakeOpenSandboxApi()
    launcher = _launcher_with_pod(fake, tmp_path)
    sid = fake.create_sandbox({"metadata": {}})["id"]
    fake.uploaded[sid][ws.WEBUI_STATE_TAR_PATH] = _tar_bytes()
    assert launcher.export_snapshot(sid, restore_confirmed=False) is None
    # And destroy under the guard performs no export either.
    launcher.destroy(sid, 5, restore_confirmed=False)
    assert sid in fake.deleted
    assert not (tmp_path / "webui-5.tar").exists()


def test_empty_state_tar_is_a_valid_archive():
    blob = ws.empty_state_tar()
    assert len(blob) >= 1024  # two zero blocks + tar padding
    with tarfile.open(fileobj=io.BytesIO(blob)):
        pass


def test_snapshot_file_permissions_are_tight(tmp_path):
    fake = FakeOpenSandboxApi()
    launcher = _launcher_with_pod(fake, tmp_path)
    blob = _tar_bytes("chats/permissions.tar", b"tight")
    launcher.persist_snapshot(3, blob)
    import os
    import stat

    mode = stat.S_IMODE(os.stat(tmp_path / "webui-3.tar").st_mode)
    assert mode == 0o600
    dir_mode = stat.S_IMODE(os.stat(tmp_path).st_mode)
    assert dir_mode == 0o700
    # T-E: the persisted blob is a VALID tar (validation happens before the
    # replace, so the on-disk file is always parseable).
    with tarfile.open(tmp_path / "webui-3.tar") as tar:
        assert tar.getnames() == ["chats/permissions.tar"]


def test_persist_rejects_non_tar_blob_and_keeps_previous(tmp_path):
    """T-E: a truncated/corrupt transfer must never replace a good snapshot."""
    fake = FakeOpenSandboxApi()
    launcher = _launcher_with_pod(fake, tmp_path)
    good = _tar_bytes("old/good.tar", b"good")
    (tmp_path / "webui-4.tar").write_bytes(good)

    launcher.persist_snapshot(4, b"not-a-tar-at-all")
    assert (tmp_path / "webui-4.tar").read_bytes() == good


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads through mode 000")
def test_persist_refuses_to_overwrite_unreadable_snapshot(tmp_path):
    """T-E: an existing-but-unreadable snapshot is operator-recoverable; a
    fresh (possibly degraded) export must not destroy it."""
    fake = FakeOpenSandboxApi()
    launcher = _launcher_with_pod(fake, tmp_path)
    path = tmp_path / "webui-5.tar"
    path.write_bytes(_tar_bytes("old/unreadable.tar", b"keepme"))
    path.chmod(0o000)

    launcher.persist_snapshot(5, _tar_bytes("new/tree.tar", b"new"))
    path.chmod(0o600)
    with tarfile.open(path) as tar:
        assert tar.getnames() == ["old/unreadable.tar"]  # original preserved


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads through mode 000")
def test_load_snapshot_distinguishes_missing_from_unreadable(tmp_path):
    fake = FakeOpenSandboxApi()
    launcher = _launcher_with_pod(fake, tmp_path)
    # Missing → None (a legitimate first launch).
    assert launcher.load_snapshot(6) is None

    path = tmp_path / "webui-6.tar"
    path.write_bytes(_tar_bytes())
    path.chmod(0o000)
    # Unreadable → SnapshotUnreadableError (NOT a first launch).
    with pytest.raises(ws.SnapshotUnreadableError):
        launcher.load_snapshot(6)
    path.chmod(0o600)


def test_launch_writes_cp_restore_record_and_destroy_clears_it(tmp_path):
    """T-E: restore confirmation persists on the CONTROL PLANE; the record
    dies with the pod (the sandbox id never returns)."""
    fake = FakeOpenSandboxApi()
    launcher = _launcher_with_pod(fake, tmp_path)
    launcher._proxy_service_factory = lambda: _NoopProxyService()
    result = launcher.launch(user_id=8, callback_url="http://openace:8080", snapshot=None)

    assert result.restore_confirmed is True
    record = tmp_path / ws.RESTORE_CONFIRMED_DIRNAME / result.sandbox_id
    assert record.is_file()
    assert launcher.restore_confirmed_on_cp(result.sandbox_id) is True

    launcher.destroy(result.sandbox_id, 8, restore_confirmed=True, final_export=False)
    assert not record.exists()


def test_unwritable_cp_record_leaves_restore_unconfirmed(tmp_path):
    """T-E crash-window guard: touch succeeded but the CP record could not be
    persisted → restore_confirmed stays False (exports skipped — safe)."""
    fake = FakeOpenSandboxApi()
    launcher = _launcher_with_pod(fake, tmp_path)
    launcher._proxy_service_factory = lambda: _NoopProxyService()
    # Point the record root at a path occupied by a regular file → mkdir fails.
    (tmp_path / "restore-confirmed").write_text("occupied")

    result = launcher.launch(user_id=8, callback_url="http://openace:8080", snapshot=None)
    assert result.restore_confirmed is False
