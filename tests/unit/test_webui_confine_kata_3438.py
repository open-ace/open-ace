"""Issue #3438: the Kata backend of scripts/openace-webui-confine.py.

A Kata guest is a VM: host UNIX sockets bind-mounted over virtio-fs refuse
connections, so ingress and egress travel over the container's stdio through
:class:`Mux`. Pinned here without root, Docker or KVM: the channel (streams,
half-close, flow control) and its fail-closed handling of a hostile peer, the
host-side Kata evidence, the runtime/policy/argv/planning changes and the
unbuffered environment line. The real Kata run is
scripts/webui_confine_acceptance.py with CONFINE_ACCEPTANCE_BACKEND=kata.
"""

from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import pwd
import queue
import socket
import struct
import threading
import time
from pathlib import Path

import pytest

pytestmark = [pytest.mark.issue(3438)]

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "openace-webui-confine.py"
IMAGE = "sha256:" + "a" * 64
CID = "c" * 64


@pytest.fixture(scope="module")
def confine():
    spec = importlib.util.spec_from_file_location("openace_webui_confine_k", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _wait(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def _echo(sock: socket.socket) -> None:
    try:
        while True:
            data = sock.recv(65536)
            if not data:
                break
            sock.sendall(data)
        sock.shutdown(socket.SHUT_WR)
    except OSError:
        pass


def _recv_all(sock: socket.socket) -> bytes:
    out = bytearray()
    while True:
        data = sock.recv(65536)
        if not data:
            return bytes(out)
        out += data


# ── the channel between two real Mux ends ───────────────────────────────────


@pytest.fixture
def channel(confine):
    """host (initiator) <-> guest over two pipes, as in production."""
    made = []

    def _make(host_accept=None, guest_accept=None, **kwargs):
        h2g_r, h2g_w = os.pipe()
        g2h_r, g2h_w = os.pipe()
        host = confine.Mux(g2h_r, h2g_w, initiator=True, accept=host_accept or {}, **kwargs)
        guest = confine.Mux(h2g_r, g2h_w, initiator=False, accept=guest_accept or {}, **kwargs)
        for mux in (host, guest):
            threading.Thread(target=mux.run, daemon=True).start()
        made.append((h2g_w, g2h_w))
        return host, guest

    yield _make
    for fds in made:
        for fd in fds:
            try:
                os.close(fd)
            except OSError:
                pass


def _open(mux, kind):
    """Open a stream from *mux*; return the client end."""
    client, ours = socket.socketpair()
    threading.Thread(target=mux.open, args=(kind, ours), daemon=True).start()
    return client


def test_ingress_and_egress_streams_round_trip(confine, channel):
    host, guest = channel(
        host_accept={confine.MUX_KIND_EGRESS: _echo},
        guest_accept={confine.MUX_KIND_INGRESS: _echo},
    )
    ingress = _open(host, confine.MUX_KIND_INGRESS)
    egress = _open(guest, confine.MUX_KIND_EGRESS)
    for sock, text in ((ingress, b"browser"), (egress, b"upstream")):
        sock.sendall(text)
        sock.shutdown(socket.SHUT_WR)  # half-close: the reply still arrives
        assert _recv_all(sock) == text
        sock.close()


def test_a_large_transfer_cycles_the_credit_window(confine, channel):
    host, _ = channel(guest_accept={confine.MUX_KIND_INGRESS: _echo})
    blob = os.urandom(confine.MUX_WINDOW * 6 + 123)
    client = _open(host, confine.MUX_KIND_INGRESS)

    def _send():
        client.sendall(blob)
        client.shutdown(socket.SHUT_WR)

    threading.Thread(target=_send, daemon=True).start()
    client.settimeout(20)
    assert _recv_all(client) == blob


def test_a_stalled_stream_does_not_block_the_others(confine, channel):
    stalled = threading.Event()

    def _never_reads(sock):
        stalled.wait(10)

    kinds = {confine.MUX_KIND_INGRESS: _echo}
    host, guest = channel(guest_accept=kinds, host_accept={confine.MUX_KIND_EGRESS: _never_reads})
    slow = _open(guest, confine.MUX_KIND_EGRESS)

    def _flood():
        with contextlib.suppress(OSError):  # the stream is torn down under it
            slow.sendall(os.urandom(confine.MUX_WINDOW * 4))

    threading.Thread(target=_flood, daemon=True).start()
    time.sleep(0.3)  # the slow stream has used up its window by now
    fast = _open(host, confine.MUX_KIND_INGRESS)
    fast.settimeout(5)
    fast.sendall(b"still flowing")
    fast.shutdown(socket.SHUT_WR)
    assert _recv_all(fast) == b"still flowing"
    stalled.set()


def test_the_peer_cannot_open_a_kind_it_was_not_given(confine, channel):
    # the guest may open EGRESS only: an INGRESS from it is refused, the
    # channel itself stays up
    host, guest = channel(host_accept={confine.MUX_KIND_EGRESS: _echo})
    refused = _open(guest, confine.MUX_KIND_INGRESS)
    refused.settimeout(5)
    assert _recv_all(refused) == b""
    assert not host.closed.is_set()


def test_peer_streams_are_bounded(confine, channel):
    hold = threading.Event()
    host, guest = channel(
        host_accept={confine.MUX_KIND_EGRESS: lambda s: hold.wait(10)}, max_streams=2
    )
    open_ = [_open(guest, confine.MUX_KIND_EGRESS) for _ in range(2)]
    assert _wait(lambda: len(host._streams) == 2)
    third = _open(guest, confine.MUX_KIND_EGRESS)
    third.settimeout(5)
    assert _recv_all(third) == b""  # RESET by the host
    hold.set()
    for sock in open_:
        sock.close()


# ── a hostile peer, speaking raw frames ─────────────────────────────────────


@pytest.fixture
def raw_host(confine):
    """A host Mux whose peer is the test, writing raw frames."""
    to_host_r, to_host_w = os.pipe()
    from_host_r, from_host_w = os.pipe()
    hold = threading.Event()
    host = confine.Mux(
        to_host_r,
        from_host_w,
        initiator=True,
        accept={confine.MUX_KIND_EGRESS: lambda s: hold.wait(10)},
    )
    threading.Thread(target=host.run, daemon=True).start()
    reader = os.fdopen(from_host_r, "rb")
    outbox: queue.Queue[bytes] = queue.Queue()

    def _writer():  # one writer: frames stay whole and in order
        while True:
            data = outbox.get()
            try:
                os.write(to_host_w, data)
            except OSError:
                return  # the host dropped the channel

    threading.Thread(target=_writer, daemon=True).start()

    def send(ftype, sid, payload=b"", length=None):
        header = confine.MUX_HEADER.pack(ftype, sid, len(payload) if length is None else length)
        outbox.put(header + payload)

    def frame():
        ftype, sid, length = confine.MUX_HEADER.unpack(reader.read(confine.MUX_HEADER.size))
        return ftype, sid, reader.read(length)

    yield host, send, frame
    hold.set()
    for fd in (to_host_w, from_host_w):
        try:
            os.close(fd)
        except OSError:
            pass


def test_opening_with_the_hosts_parity_drops_the_channel(confine, raw_host):
    host, send, _ = raw_host
    send(confine.MUX_OPEN, 1, confine.MUX_KIND_EGRESS)
    assert host.closed.wait(5)


@pytest.mark.parametrize(
    "frame",
    [
        (99, 2, b""),  # unknown frame type
        (6, 0, b"x" * 10, 40000),  # a length beyond MUX_CHUNK
    ],
)
def test_malformed_frames_drop_the_channel(confine, raw_host, frame):
    host, send, _ = raw_host
    send(*frame)
    assert host.closed.wait(5)


def test_overrunning_the_window_drops_the_channel(confine, raw_host):
    host, send, _ = raw_host
    send(confine.MUX_OPEN, 2, confine.MUX_KIND_EGRESS)
    chunk = b"x" * confine.MUX_CHUNK
    frames = (confine.MUX_WINDOW // confine.MUX_CHUNK) * 4  # far past window + socket buffer
    for _ in range(frames):
        send(confine.MUX_DATA, 2, chunk)
    assert host.closed.wait(10)


def test_crediting_beyond_the_window_drops_the_channel(confine, raw_host):
    host, send, frame = raw_host
    client = _open(host, confine.MUX_KIND_INGRESS)
    ftype, sid, kind = frame()
    assert (ftype, kind) == (confine.MUX_OPEN, confine.MUX_KIND_INGRESS)
    send(confine.MUX_CREDIT, sid, struct.pack(">I", 1))  # the window is already full
    assert host.closed.wait(5)
    client.close()


def test_data_after_close_drops_the_channel(confine, raw_host):
    host, send, _ = raw_host
    send(confine.MUX_OPEN, 2, confine.MUX_KIND_EGRESS)
    send(confine.MUX_DATA, 2, b"")  # empty: ignored, and not an EOF
    send(confine.MUX_CLOSE, 2)
    time.sleep(0.2)
    assert not host.closed.is_set()
    send(confine.MUX_DATA, 2, b"more")
    assert host.closed.wait(5)


def test_frames_for_unknown_streams_are_ignored(confine, raw_host):
    host, send, _ = raw_host
    send(confine.MUX_DATA, 42, b"late")
    send(confine.MUX_PING, 0)
    time.sleep(0.2)
    assert not host.closed.is_set()


def test_the_guest_watchdog_sees_pings(confine, raw_host):
    host, send, _ = raw_host
    before = host.last_frame
    time.sleep(0.05)
    send(confine.MUX_PING, 0)
    assert _wait(lambda: host.last_frame > before)


# ── environment line (no read-ahead into the frames) ───────────────────────


def test_read_line_stops_at_the_newline(confine):
    r, w = os.pipe()
    os.write(w, b'{"A": "1"}\n' + confine.MUX_HEADER.pack(confine.MUX_PING, 0, 0))
    os.close(w)
    assert json.loads(confine._read_line(r)) == {"A": "1"}
    assert os.read(r, 64) == confine.MUX_HEADER.pack(confine.MUX_PING, 0, 0)
    os.close(r)


def test_read_line_is_bounded(confine):
    r, w = os.pipe()
    os.write(w, b"x" * 100)
    os.close(w)
    with pytest.raises(confine.ConfineError):
        confine._read_line(r, limit=10)
    os.close(r)


# ── host-side Kata evidence ─────────────────────────────────────────────────


def _proc(tmp_path, *, exe, shim_exe, shim_args):
    proc = tmp_path / "proc"
    (proc / "100").mkdir(parents=True)
    (proc / "99").mkdir()
    os.symlink(exe, proc / "100" / "exe")
    (proc / "100" / "stat").write_text("100 (qemu-system-x86) S 99 100 100 0 -1")
    os.symlink(shim_exe, proc / "99" / "exe")
    (proc / "99" / "cmdline").write_bytes(b"\0".join(shim_args) + b"\0")
    return str(proc)


SHIM = "/opt/kata/bin/containerd-shim-kata-v2"
SHIM_ARGS = [SHIM.encode(), b"-namespace", b"moby", b"-id", CID.encode(), b"-address", b"/x"]


@pytest.mark.parametrize(
    "exe",
    ["/opt/kata/bin/qemu-system-x86_64", "/opt/kata/bin/cloud-hypervisor",
     "/opt/kata/bin/firecracker"],
)  # fmt: skip
def test_kata_evidence_accepts_a_hypervisor_under_the_kata_shim(confine, tmp_path, exe):
    proc = _proc(tmp_path, exe=exe, shim_exe=SHIM, shim_args=SHIM_ARGS)
    assert confine.kata_evidence(100, CID, proc) is True


@pytest.mark.parametrize(
    ("exe", "shim_exe", "shim_args"),
    [
        # runc: State.Pid is the container's own init, under the runc shim
        ("/usr/local/bin/python3", "/usr/bin/containerd-shim-runc-v2", SHIM_ARGS),
        # a hypervisor, but under another shim
        ("/opt/kata/bin/qemu-system-x86_64", "/usr/bin/containerd-shim-runc-v2", SHIM_ARGS),
        # the Kata shim of ANOTHER container
        ("/opt/kata/bin/qemu-system-x86_64", SHIM,
         [SHIM.encode(), b"-id", b"d" * 64]),
        # a deleted/replaced binary reads back with a suffix
        ("/opt/kata/bin/qemu-system-x86_64 (deleted)", SHIM + " (deleted)", SHIM_ARGS),
    ],
)  # fmt: skip
def test_kata_evidence_refuses_anything_else(confine, tmp_path, exe, shim_exe, shim_args):
    proc = _proc(tmp_path, exe=exe, shim_exe=shim_exe, shim_args=shim_args)
    assert confine.kata_evidence(100, CID, proc) is False


def test_kata_evidence_fails_closed_on_unreadable_proc(confine, tmp_path):
    assert confine.kata_evidence(100, CID, str(tmp_path / "nothing")) is False


# ── runtime availability, policy, argv, planning ───────────────────────────


def test_kata_runtime_by_registered_name_or_by_shim(confine, monkeypatch):
    monkeypatch.setattr(confine, "require_root_controlled_executable", lambda p: None)
    monkeypatch.setattr(confine.shutil, "which", lambda name, path=None: f"/usr/local/bin/{name}")
    assert confine._kata_runtime_available("kata", {"kata": {}}) is True
    assert confine._kata_runtime_available("io.containerd.kata.v2", {}) is True
    assert confine._kata_runtime_available("kata", {}) is False
    monkeypatch.setattr(confine.shutil, "which", lambda name, path=None: None)
    assert confine._kata_runtime_available("io.containerd.kata.v2", {}) is False


def test_kata_runtime_shim_must_be_root_controlled(confine, monkeypatch):
    def _refuse(path):
        raise confine.ConfineError("writable")

    monkeypatch.setattr(confine, "require_root_controlled_executable", _refuse)
    monkeypatch.setattr(confine.shutil, "which", lambda name, path=None: f"/tmp/{name}")
    assert confine._kata_runtime_available("io.containerd.kata.v2", {}) is False


def _load(confine, tmp_path, monkeypatch, data):
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(data))
    real_fstat, real_lstat = os.fstat, os.lstat

    class _Root:
        def __init__(self, st):
            self._st = st

        def __getattr__(self, name):
            return 0 if name == "st_uid" else getattr(self._st, name)

    monkeypatch.setattr(confine.os, "fstat", lambda fd: _Root(real_fstat(fd)))
    monkeypatch.setattr(confine.os, "lstat", lambda p: _Root(real_lstat(p)))
    return confine.load_policy(str(path))


def test_policy_runtimes_map_and_legacy_runtime(confine, tmp_path, monkeypatch):
    policy = _load(
        confine, tmp_path, monkeypatch,
        {"webui": ["/w"], "container": {"image": IMAGE,
                                        "runtimes": {"runsc": "runsc-openace",
                                                     "kata": "io.containerd.kata.v2"}}},
    )  # fmt: skip
    assert policy.container.runtime == "runsc-openace"
    assert policy.container.kata_runtime == "io.containerd.kata.v2"
    legacy = _load(
        confine,
        tmp_path,
        monkeypatch,
        {"webui": ["/w"], "container": {"image": IMAGE, "runtime": "runsc-openace"}},
    )
    assert (legacy.container.runtime, legacy.container.kata_runtime) == ("runsc-openace", "")
    kata_only = _load(
        confine,
        tmp_path,
        monkeypatch,
        {"webui": ["/w"], "container": {"image": IMAGE, "runtimes": {"kata": "kata"}}},
    )
    assert (kata_only.container.runtime, kata_only.container.kata_runtime) == ("", "kata")


@pytest.mark.parametrize(
    "container",
    [{"image": IMAGE},  # no runtime at all
     {"image": IMAGE, "runtimes": {"runc": "runc"}},  # an unknown mode
     {"image": IMAGE, "runtimes": {"kata": "kata --debug"}},
     {"image": IMAGE, "runtimes": ["kata"]}],
)  # fmt: skip
def test_policy_rejects_bad_runtimes(confine, tmp_path, monkeypatch, container):
    with pytest.raises(confine.ConfineError):
        _load(confine, tmp_path, monkeypatch, {"webui": ["/w"], "container": container})


def _kata_policy(confine):
    return confine.ContainerPolicy(IMAGE, "runsc-openace", "/usr/bin/docker", "kata")


def _docker_argv(confine, container, kata):
    return confine.build_docker_argv(
        container=container, uid=3001, gid=3001, extra_gids=[], port=3150,
        memory_max="4G", cpu_quota=150, tasks_max=512, home="/wsbase/alice",
        shared="/wsbase/shared", log_dir="/tmp/qwen-code-webui-7",
        run_dir="/run/openace-webui/3001-3150-1", script="/usr/local/bin/openace-webui-confine",
        webui_argv=["/usr/bin/qwen-code-webui"], kata=kata,
    )  # fmt: skip


def test_kata_docker_argv_uses_the_stdio_channel(confine):
    argv = _docker_argv(confine, _kata_policy(confine), kata=True)
    assert argv[argv.index("--runtime") + 1] == "kata"
    assert argv[argv.index("--network") + 1] == "none"
    assert not any("/run/openace" in item for item in argv)  # no socket mount
    assert argv[argv.index("--pids-limit") + 1] == "512"  # no gVisor host-thread floor
    inner = argv[argv.index("inner") :]
    assert inner[inner.index("--transport") + 1] == "stdio"
    runsc = _docker_argv(confine, _kata_policy(confine), kata=False)
    assert runsc[runsc.index("--runtime") + 1] == "runsc-openace"
    assert "--transport" not in runsc


def test_a_backend_without_its_runtime_is_refused(confine):
    policy = confine.ContainerPolicy(IMAGE, "runsc-openace", "/usr/bin/docker")
    with pytest.raises(confine.ConfineError, match="no kata runtime"):
        _docker_argv(confine, policy, kata=True)


@pytest.fixture
def planned_kata(confine, monkeypatch, tmp_path):
    entry = pwd.struct_passwd(("alice", "x", 3001, 3001, "", "/wsbase/alice", "/bin/bash"))
    policy = confine.Policy(
        frozenset({"/usr/bin/qwen-code-webui"}), "/usr/bin:/bin", frozenset(),
        confine.PRIVILEGED_GROUPS, _kata_policy(confine),
    )  # fmt: skip
    kvm = tmp_path / "kvm"
    kvm.write_text("")
    monkeypatch.setattr(confine, "KVM_DEVICE", str(kvm))
    monkeypatch.setattr(confine, "load_policy", lambda path=None: policy)
    monkeypatch.setattr(confine, "resolve_account", lambda name, denied: entry)
    monkeypatch.setattr(
        confine, "workspace_layout", lambda e, b: ("/wsbase", "/wsbase/alice", "/wsbase/shared")
    )
    monkeypatch.setattr(confine, "validate_log_dir", lambda p, e: p)
    monkeypatch.setattr(confine, "account_groups", lambda e: {3001: "alice"})
    monkeypatch.setattr(confine, "require_root_controlled_executable", lambda p: None)
    monkeypatch.setattr(confine.shutil, "which", lambda tool, path=None: f"/usr/bin/{tool}")
    monkeypatch.setattr(confine, "symlinks_protected", lambda path=None: True)

    def _plan():
        argv = [
            "--account", "alice", "--port", "3150", "--memory-max", "4G", "--cpu-quota", "150",
            "--tasks-max", "512", "--allow", "10.0.0.5:19888",
            "--log-dir", "/tmp/qwen-code-webui-7", "--webui", "/usr/bin/qwen-code-webui",
            "--backend", "kata", "--", "--port", "3150",
        ]  # fmt: skip
        return confine.plan_launch(argv, json.dumps({"OPENAI_API_KEY": "tok"}))

    _plan.kvm = kvm
    return _plan


def test_plan_kata_launch(confine, planned_kata):
    docker_argv, payload = planned_kata()
    assert payload["backend"] == "container"
    assert payload["transport"] == "stdio"
    assert docker_argv[docker_argv.index("--runtime") + 1] == "kata"
    assert "tok" not in " ".join(docker_argv)


def test_plan_kata_requires_kvm(confine, planned_kata):
    planned_kata.kvm.unlink()
    with pytest.raises(confine.ConfineError, match="/kvm"):
        planned_kata()


def test_probe_usage_is_strict(confine, monkeypatch):
    monkeypatch.setattr(confine.os, "geteuid", lambda: 0)
    seen = []
    monkeypatch.setattr(
        confine, "run_container_probe", lambda backend="container": seen.append(backend) or 0
    )
    assert confine.run_launch(["--probe"]) == 0
    assert confine.run_launch(["--probe", "--backend", "kata"]) == 0
    assert seen == ["container", "kata"]
    with pytest.raises(confine.ConfineError):
        confine.run_launch(["--probe", "--backend", "bwrap"])
