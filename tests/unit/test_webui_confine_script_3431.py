"""Issue #3431 (Option 1): scripts/openace-webui-confine.py unit tests.

The wrapper's root mode (``launch``) is a security boundary, so its decisions
are pinned here without root: allowlist parsing/matching, environment
validation, account/group refusal, the policy-file ownership rules, the
root-controlled executable/PATH checks, the systemd-run / setpriv command
line it execs, and the bubblewrap argv. The reverse tunnel and the egress
proxy run over real sockets. The Linux end-to-end run (real systemd scope +
bubblewrap + the real WebUI) is scripts/webui_confine_acceptance.py, which
needs root and a disposable host.
"""

from __future__ import annotations

import importlib.util
import json
import os
import pwd
import shutil
import socket
import tempfile
import threading
import time
from pathlib import Path

import pytest

pytestmark = [pytest.mark.issue(3431)]

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "openace-webui-confine.py"


@pytest.fixture(scope="module")
def confine():
    spec = importlib.util.spec_from_file_location("openace_webui_confine", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _entry(name="alice", uid=None, home="/wsbase/alice", gid=None):
    uid = os.getuid() if uid is None else uid
    gid = os.getgid() if gid is None else gid
    return pwd.struct_passwd((name, "x", uid, gid, "", home, "/bin/bash"))


# ── allowlist ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("api.example.com:443", ("api.example.com", 443)),
        ("API.Example.com:443", ("api.example.com", 443)),
        ("192.168.1.21:19888", ("192.168.1.21", 19888)),
        ("[::1]:8080", ("::1", 8080)),
        ("*.pythonhosted.org:443", ("*.pythonhosted.org", 443)),
    ],
)
def test_parse_allow_entry_accepts(confine, raw, expected):
    assert confine.parse_allow_entry(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["example.com", ":443", "example.com:0", "example.com:70000", "exa mple.com:80",
     "[not-ip]:80", "example.com:http", "*.*.com:443", "a..b:80", "example.com:4²3"],
)  # fmt: skip
def test_parse_allow_entry_rejects(confine, raw):
    with pytest.raises(confine.ConfineError):
        confine.parse_allow_entry(raw)


def test_host_allowed_exact_wildcard_and_port(confine):
    allow = [("api.example.com", 443), ("*.pythonhosted.org", 443), ("10.0.0.5", 19888)]
    assert confine.host_allowed("api.example.com", 443, allow)
    assert confine.host_allowed("API.EXAMPLE.COM.", 443, allow)  # case + trailing dot
    assert not confine.host_allowed("api.example.com", 80, allow)  # port is exact
    assert confine.host_allowed("files.pythonhosted.org", 443, allow)
    # the wildcard covers strict subdomains only, and never a suffix look-alike
    assert not confine.host_allowed("pythonhosted.org", 443, allow)
    assert not confine.host_allowed("evilpythonhosted.org", 443, allow)
    assert confine.host_allowed("10.0.0.5", 19888, allow)
    assert not confine.host_allowed("10.0.0.5", 5432, allow)


# ── environment ─────────────────────────────────────────────────────────────


def test_validate_env_keeps_normal_keys(confine):
    env = {"OPENAI_API_KEY": "tok", "OPENACE_LOG_DIR": "/tmp/x", "ENVIRONMENT": "prod"}
    assert confine.validate_env(env) == env


@pytest.mark.parametrize(
    "env",
    [
        {"LD_PRELOAD": "/tmp/evil.so"},
        {"PYTHONPATH": "/tmp"},
        {"NODE_OPTIONS": "--require /tmp/x"},
        {"PATH": "/tmp"},
        {"HOME": "/root"},
        {"ENV": "/tmp/rc"},
        {"BASH_ENV": "/tmp/rc"},
        {"HTTPS_PROXY": "http://elsewhere:1"},
        {"bad key": "x"},
        {"OK": 1},
        {"OK": "a\0b"},
        ["not", "a", "dict"],
    ],
)
def test_validate_env_rejects(confine, env):
    with pytest.raises(confine.ConfineError):
        confine.validate_env(env)


# ── account + layout + log dir ──────────────────────────────────────────────


def test_resolve_account_refuses_root_and_missing(confine):
    with pytest.raises(confine.ConfineError, match="privileged or reserved"):
        confine.resolve_account("root")
    with pytest.raises(confine.ConfineError, match="does not exist"):
        confine.resolve_account("no-such-account-3431")


@pytest.mark.parametrize(
    ("groups", "refused"),
    [({2001: "alice"}, None), ({2001: "alice", 27: "sudo"}, r"sudo\(27\)"),
     ({2001: "alice", 0: "wheel0"}, r"wheel0\(0\)"), ({2001: "alice", 42: "shadow"}, "shadow"),
     ({2001: "alice", 999: "docker"}, "docker")],
)  # fmt: skip
def test_resolve_account_refuses_privileged_groups(confine, monkeypatch, groups, refused):
    monkeypatch.setattr(confine.pwd, "getpwnam", lambda name: _entry(name, 2001, gid=2001))
    monkeypatch.setattr(confine, "account_groups", lambda entry: groups)
    if refused is None:
        assert confine.resolve_account("alice").pw_uid == 2001
    else:
        with pytest.raises(confine.ConfineError, match=f"privileged group.*{refused}"):
            confine.resolve_account("alice")


def test_resolve_account_honours_policy_denied_groups(confine, monkeypatch):
    monkeypatch.setattr(confine.pwd, "getpwnam", lambda name: _entry(name, 2001, gid=2001))
    monkeypatch.setattr(confine, "account_groups", lambda entry: {2001: "a", 3000: "gpu"})
    with pytest.raises(confine.ConfineError, match="gpu"):
        confine.resolve_account("a", confine.PRIVILEGED_GROUPS | {"gpu"})


def test_workspace_layout_with_real_directories(confine, tmp_path):
    base = tmp_path / "wsbase"
    (base / "alice").mkdir(parents=True)
    entry = _entry(home=str(base / "alice"))
    assert confine.workspace_layout(entry) == (str(base), str(base / "alice"), None)
    (base / "shared").mkdir()
    assert confine.workspace_layout(entry)[2] == str(base / "shared")
    with pytest.raises(confine.ConfineError, match="outside the policy"):
        confine.workspace_layout(entry, frozenset({"/home"}))
    assert confine.workspace_layout(entry, frozenset({str(base)}))[0] == str(base)


def test_workspace_layout_refuses_missing_symlinked_or_foreign_home(confine, tmp_path):
    with pytest.raises(confine.ConfineError, match="not accessible"):
        confine.workspace_layout(_entry(home=str(tmp_path / "gone")))
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "linked"
    link.symlink_to(real)
    with pytest.raises(confine.ConfineError, match="not a directory owned"):
        confine.workspace_layout(_entry(home=str(link)))
    with pytest.raises(confine.ConfineError, match="not a directory owned"):
        confine.workspace_layout(_entry(uid=os.getuid() + 1, home=str(real)))


def test_validate_log_dir_with_real_directories(confine, tmp_path):
    suffix = str(os.getpid()) + str(time.monotonic_ns())[-6:]
    path = f"/tmp/qwen-code-webui-{suffix}"
    os.mkdir(path)
    try:
        assert confine.validate_log_dir(path, _entry()) == path
        with pytest.raises(confine.ConfineError, match="not a directory owned"):
            confine.validate_log_dir(path, _entry(uid=os.getuid() + 1))
    finally:
        os.rmdir(path)
    with pytest.raises(confine.ConfineError, match="not accessible"):
        confine.validate_log_dir(path, _entry())
    with pytest.raises(confine.ConfineError, match="is not /tmp"):
        confine.validate_log_dir("/tmp/qwen-code-webui-1/../../etc", _entry())


# ── root-controlled executable / PATH ──────────────────────────────────────


def test_root_controlled_executable(confine, tmp_path):
    if not confine._root_controlled("/bin"):
        pytest.skip("/bin is not root-controlled on this machine")
    confine.require_root_controlled_executable("/bin/sh")
    own = tmp_path / "webui"
    own.write_text("#!/bin/sh\n")
    own.chmod(0o755)
    with pytest.raises(confine.ConfineError, match="not root-owned"):
        confine.require_root_controlled_executable(str(own))
    with pytest.raises(confine.ConfineError, match="not an executable"):
        confine.require_root_controlled_executable(str(tmp_path / "missing"))


def test_root_controlled_path(confine, tmp_path):
    if not confine._root_controlled("/usr/bin"):
        pytest.skip("/usr/bin is not root-controlled on this machine")
    confine.require_root_controlled_path("/usr/bin:/bin:/nonexistent-3431/bin")
    with pytest.raises(confine.ConfineError, match="not root-controlled"):
        confine.require_root_controlled_path(f"/usr/bin:{tmp_path}")


# ── policy file ─────────────────────────────────────────────────────────────


def _as_root_owned(monkeypatch, confine):
    """Make fstat/lstat report uid 0 so the ownership rule can be exercised."""
    real_fstat, real_lstat = os.fstat, os.lstat

    def _root(result):
        fields = list(result)
        fields[4] = 0  # st_uid
        return os.stat_result(fields)

    monkeypatch.setattr(confine.os, "fstat", lambda fd: _root(real_fstat(fd)))
    monkeypatch.setattr(confine.os, "lstat", lambda p: _root(real_lstat(p)))


def _policy_file(tmp_path: Path, data) -> Path:
    policy_dir = tmp_path / "etc"
    policy_dir.mkdir(mode=0o755)
    policy_dir.chmod(0o755)
    path = policy_dir / "webui-confine.json"
    path.write_text(json.dumps(data) if not isinstance(data, str) else data)
    path.chmod(0o644)
    return path


def _skip_if_writable_ancestors(path: Path) -> None:
    for parent in [path.parent, *path.parent.parents]:
        if parent.stat().st_mode & 0o022:
            pytest.skip(f"{parent} is group/world-writable on this machine")


def test_policy_refuses_non_root_owner(confine, tmp_path):
    path = _policy_file(tmp_path, {"webui": ["/usr/bin/qwen-code-webui"]})
    with pytest.raises(confine.ConfineError, match="root-owned"):
        confine.load_policy(str(path))


def test_policy_refuses_group_writable(confine, tmp_path, monkeypatch):
    path = _policy_file(tmp_path, {"webui": ["/usr/bin/qwen-code-webui"]})
    path.chmod(0o664)
    _as_root_owned(monkeypatch, confine)
    with pytest.raises(confine.ConfineError, match="root-owned"):
        confine.load_policy(str(path))


def test_policy_refuses_symlink(confine, tmp_path):
    target = _policy_file(tmp_path, {"webui": ["/usr/bin/qwen-code-webui"]})
    link = tmp_path / "link.json"
    link.symlink_to(target)
    with pytest.raises(confine.ConfineError, match="unreadable"):
        confine.load_policy(str(link))


def test_policy_parses_when_root_controlled(confine, tmp_path, monkeypatch):
    path = _policy_file(
        tmp_path,
        {"webui": ["/usr/bin/qwen-code-webui"], "path": "/opt/node/bin:/usr/bin",
         "bases": ["/home"], "denied_groups": ["gpu"]},
    )  # fmt: skip
    _skip_if_writable_ancestors(path)
    _as_root_owned(monkeypatch, confine)
    policy = confine.load_policy(str(path))
    assert policy.webuis == frozenset({"/usr/bin/qwen-code-webui"})
    assert policy.path == "/opt/node/bin:/usr/bin"
    assert policy.bases == frozenset({"/home"})
    assert "gpu" in policy.denied_groups and "sudo" in policy.denied_groups


@pytest.mark.parametrize(
    "data",
    [{"webui": "/usr/bin/x"}, {"webui": ["relative/x"]}, {"path": "/usr/bin"},
     {"webui": ["/x"], "path": "rel:/usr/bin"}, {"webui": ["/x"], "bases": ["rel"]},
     {"webui": ["/x"], "denied_groups": "sudo"}, ["not", "an", "object"], "not json"],
)  # fmt: skip
def test_policy_rejects_bad_content(confine, tmp_path, monkeypatch, data):
    path = _policy_file(tmp_path, data)
    _skip_if_writable_ancestors(path)
    _as_root_owned(monkeypatch, confine)
    with pytest.raises(confine.ConfineError):
        confine.load_policy(str(path))


# ── launch planning (the root decision) ─────────────────────────────────────


@pytest.fixture
def planned(confine, monkeypatch, tmp_path):
    """plan_launch with the filesystem/passwd validators stubbed out (they are
    tested for real above), so the argv assembly can be pinned exactly."""
    entry = _entry("alice", 3001, "/wsbase/alice", gid=3001)
    webui = "/usr/bin/qwen-code-webui"
    policy = confine.Policy(
        frozenset({webui}), "/usr/bin:/bin", frozenset(), confine.PRIVILEGED_GROUPS
    )
    seen: dict[str, object] = {}
    monkeypatch.setattr(confine, "load_policy", lambda path=None: policy)

    def _resolve(name, denied):
        seen["denied"] = denied
        return entry

    monkeypatch.setattr(confine, "resolve_account", _resolve)
    monkeypatch.setattr(
        confine, "workspace_layout", lambda e, bases: ("/wsbase", "/wsbase/alice", "/wsbase/shared")
    )
    monkeypatch.setattr(confine, "validate_log_dir", lambda path, e: path)
    monkeypatch.setattr(
        confine, "require_root_controlled_executable", lambda p: seen.setdefault("exe", p)
    )
    monkeypatch.setattr(
        confine, "require_root_controlled_path", lambda p: seen.setdefault("path", p)
    )
    monkeypatch.setattr(confine.shutil, "which", lambda tool, path=None: f"/usr/bin/{tool}")

    base = {
        "--account": "alice", "--port": "3150", "--memory-max": "4G", "--cpu-quota": "200",
        "--tasks-max": "512", "--log-dir": "/tmp/qwen-code-webui-7", "--webui": webui,
    }  # fmt: skip

    def _plan(override=None, *, env=None, allow=("10.0.0.5:19888",), tail=("--", "--port", "3150")):
        opts = dict(base, **(override or {}))
        argv = [item for pair in opts.items() for item in pair]
        for entry_ in allow:
            argv += ["--allow", entry_]
        argv += list(tail)
        return confine.plan_launch(
            argv, json.dumps({"OPENAI_API_KEY": "tok"} if env is None else env)
        )

    _plan.webui = webui
    _plan.seen = seen
    return _plan


def test_plan_launch_builds_fixed_scope_and_setpriv(confine, planned):
    systemd_argv, payload = planned()
    assert systemd_argv[:5] == [
        "/usr/bin/systemd-run", "--scope", "--quiet", "--collect", "--unit=openace-webui-3001-3150",
    ]  # fmt: skip
    props = [systemd_argv[i + 1] for i, arg in enumerate(systemd_argv) if arg == "-p"]
    assert props == ["MemoryMax=4G", "MemorySwapMax=0", "CPUQuota=200%", "TasksMax=512"]
    setpriv = systemd_argv[systemd_argv.index("/usr/bin/setpriv") :]
    # --init-groups: systemd-run --scope --uid would keep root's group 0.
    for flag in ("--reuid=3001", "--regid=3001", "--init-groups", "--no-new-privs",
                 "--inh-caps=-all", "--bounding-set=-all"):  # fmt: skip
        assert flag in setpriv
    assert setpriv[-1] == "supervise"
    # The secret travels in the payload (stdin pipe), never on a command line.
    assert "tok" not in " ".join(systemd_argv)
    assert payload["env"] == {"OPENAI_API_KEY": "tok"}
    assert payload["webui_argv"] == [planned.webui, "--port", "3150"]
    assert payload["allow"] == ["10.0.0.5:19888"]
    assert payload["path"] == "/usr/bin:/bin"
    # the ownership checks and the group denylist are applied on the way
    assert planned.seen["exe"] == planned.webui
    assert planned.seen["path"] == "/usr/bin:/bin"
    assert "sudo" in planned.seen["denied"]


@pytest.mark.parametrize(
    ("override", "match"),
    [
        ({"--port": "80"}, ">= 1024"),
        ({"--port": "3²50"}, "ASCII"),
        ({"--memory-max": "4G;x"}, "memory-max"),
        ({"--cpu-quota": "0"}, "cpu-quota"),
        ({"--cpu-quota": "9999"}, "cpu-quota"),
        ({"--cpu-quota": "1²"}, "ASCII"),
        ({"--tasks-max": "5"}, "tasks-max"),
        ({"--bind-host": "evil"}, "bind-host"),
        ({"--webui": "/bin/sh"}, "not listed"),
    ],
)
def test_plan_launch_rejects_bad_arguments(confine, planned, override, match):
    with pytest.raises(confine.ConfineError, match=match):
        planned(override)


def test_plan_launch_requires_an_allow_entry(confine, planned):
    with pytest.raises(confine.ConfineError, match="--allow"):
        planned(allow=())


def test_plan_launch_rejects_abbreviated_options(confine, planned):
    # allow_abbrev=False: "--acc" must not silently mean "--account"
    with pytest.raises(SystemExit):
        confine.plan_launch(["--acc", "alice"], "{}")


def test_plan_launch_rejects_reserved_env_and_bad_json(confine, planned):
    with pytest.raises(confine.ConfineError, match="reserved"):
        planned(env={"LD_PRELOAD": "/x.so"})
    with pytest.raises(confine.ConfineError, match="JSON"):
        confine.plan_launch(
            ["--account", "alice", "--port", "3150", "--memory-max", "4G", "--cpu-quota", "1",
             "--tasks-max", "16", "--allow", "h:1", "--log-dir", "/tmp/qwen-code-webui-1",
             "--webui", planned.webui],
            "{not json",
        )  # fmt: skip


# ── bubblewrap argv ─────────────────────────────────────────────────────────


def test_bwrap_argv_hides_base_and_binds_sockets_read_only(confine):
    argv = confine.build_bwrap_argv(
        bwrap="/usr/bin/bwrap", python="/usr/bin/python3", script="/usr/local/bin/c",
        base="/wsbase", home="/wsbase/alice", shared="/wsbase/shared",
        log_dir="/tmp/qwen-code-webui-7", socket_dir="/tmp/openace-webui-x", port=3150,
        webui_argv=["/usr/bin/qwen-code-webui", "--port", "3150"],
    )  # fmt: skip
    joined = " ".join(argv)
    assert argv[:3] == ["/usr/bin/bwrap", "--ro-bind", "/"]
    # tmpfs over the base (hides other homes) precedes the home bind
    assert joined.index("--tmpfs /wsbase") < joined.index("--bind /wsbase/alice /wsbase/alice")
    assert "--bind /wsbase/shared /wsbase/shared" in joined
    assert joined.index("--tmpfs /tmp") < joined.index("--bind /tmp/qwen-code-webui-7")
    # the socket directory is READ-ONLY inside: the sandbox cannot swap sockets
    assert "--ro-bind /tmp/openace-webui-x /run/openace" in joined
    assert "--bind /tmp/openace-webui-x" not in joined
    for flag in ("--unshare-net", "--unshare-pid", "--unshare-user", "--disable-userns",
                 "--die-with-parent", "--new-session"):  # fmt: skip
        assert flag in argv
    assert "--setenv" not in argv  # env goes through the process environment
    assert argv[argv.index("--") + 1 :] == [
        "/usr/bin/python3", "-I", "/usr/local/bin/c", "inner", "--port", "3150", "--",
        "/usr/bin/qwen-code-webui", "--port", "3150",
    ]  # fmt: skip


def test_bwrap_argv_without_shared_root(confine):
    argv = confine.build_bwrap_argv(
        bwrap="b", python="p", script="s", base="/home", home="/home/alice", shared=None,
        log_dir="/tmp/qwen-code-webui-1", socket_dir="/tmp/d", port=3101, webui_argv=["w"],
    )  # fmt: skip
    assert "/home/shared" not in argv


def test_inner_env_sets_home_and_path(confine):
    env = confine.build_inner_env(
        {"OPENAI_API_KEY": "tok"}, home="/wsbase/alice", sandbox_path="/usr/bin"
    )
    assert env == {
        "OPENAI_API_KEY": "tok",
        "HOME": "/wsbase/alice",
        "PATH": "/usr/bin",
        "LANG": "C.UTF-8",
    }


# ── reverse tunnel ──────────────────────────────────────────────────────────


@pytest.fixture
def echo_server():
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(8)

    def _serve():
        while True:
            try:
                conn, _ = server.accept()
            except OSError:
                return
            data = conn.recv(1024)
            conn.sendall(b"echo:" + data)
            conn.close()

    threading.Thread(target=_serve, daemon=True).start()
    yield server.getsockname()[1]
    server.close()


def test_tunnel_pool_pairs_client_with_idle_tunnel(confine):
    pool = confine.TunnelPool()
    tunnel_host, tunnel_inner = socket.socketpair()
    pool.add(tunnel_host)
    client, client_served = socket.socketpair()
    worker = threading.Thread(target=pool.serve_client, args=(client_served,), daemon=True)
    worker.start()
    assert tunnel_inner.recv(1) == confine.TUNNEL_GO
    client.sendall(b"hello")
    assert tunnel_inner.recv(5) == b"hello"
    tunnel_inner.sendall(b"world")
    tunnel_inner.close()
    assert client.recv(5) == b"world"
    client.close()
    worker.join(timeout=5)


def test_tunnel_pool_closes_client_without_tunnel_and_caps_idle(confine, monkeypatch):
    monkeypatch.setattr(confine, "TUNNEL_WAIT_SECONDS", 0.2)
    pool = confine.TunnelPool(max_idle=1)
    assert pool.take(timeout=0.1) is None  # nothing idle
    client, served = socket.socketpair()
    pool.serve_client(served)  # no tunnel arrives: the client is closed
    client.settimeout(1)
    assert client.recv(1) == b""
    first, _keep1 = socket.socketpair()
    second, keep2 = socket.socketpair()
    pool.add(first)
    pool.add(second)  # over the cap: closed, never queued
    keep2.settimeout(1)
    assert keep2.recv(1) == b""


def test_reverse_tunnel_end_to_end_over_unix_socket(confine, echo_server):
    # AF_UNIX paths are capped (~104 bytes on macOS); pytest's tmp_path is longer.
    short_dir = tempfile.mkdtemp(prefix="oc3431-", dir="/tmp")
    path = os.path.join(short_dir, "tunnel.sock")
    pool = confine.TunnelPool()
    listener = confine._unix_listener(path)

    def _accept():
        while True:
            try:
                conn, _ = listener.accept()
            except OSError:
                return
            pool.add(conn)

    threading.Thread(target=_accept, daemon=True).start()
    threading.Thread(
        target=confine._run_tunnel_pool, args=(path, echo_server, 2), daemon=True
    ).start()
    for _ in range(3):  # more clients than the pool size: tunnels are replenished
        client, served = socket.socketpair()
        threading.Thread(target=pool.serve_client, args=(served,), daemon=True).start()
        client.settimeout(10)
        client.sendall(b"ping")
        assert client.recv(64) == b"echo:ping"
        client.close()
    listener.close()
    shutil.rmtree(short_dir, ignore_errors=True)


# ── egress proxy over real sockets ──────────────────────────────────────────


@pytest.fixture
def upstream():
    """A loopback server that records the first request head and answers 200."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(8)
    seen: list[bytes] = []

    def _serve():
        while True:
            try:
                conn, _ = server.accept()
            except OSError:
                return
            data = b""
            while b"\r\n\r\n" not in data:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                data += chunk
            seen.append(data)
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
            conn.close()

    threading.Thread(target=_serve, daemon=True).start()
    yield server.getsockname()[1], seen
    server.close()


def _through_proxy(confine, allow, request: bytes, log_fd=None) -> bytes:
    proxy = confine.EgressProxy(allow, log_fd)
    client, served = socket.socketpair()
    worker = threading.Thread(target=proxy.handle, args=(served,), daemon=True)
    worker.start()
    client.sendall(request)
    client.settimeout(5)
    chunks = []
    while True:
        try:
            chunk = client.recv(4096)
        except TimeoutError:
            break
        if not chunk:
            break
        chunks.append(chunk)
    client.close()
    worker.join(timeout=5)
    return b"".join(chunks)


def test_proxy_forwards_allowed_absolute_http_in_origin_form(confine, upstream):
    port, seen = upstream
    reply = _through_proxy(
        confine,
        [("127.0.0.1", port)],
        f"GET http://127.0.0.1:{port}/v1/models?x=1 HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
        "Proxy-Authorization: Basic c2VjcmV0\r\nConnection: keep-alive\r\n\r\n".encode(),
    )
    assert reply.startswith(b"HTTP/1.1 200 OK") and reply.endswith(b"ok")
    head = seen[0].decode()
    assert head.startswith("GET /v1/models?x=1 HTTP/1.1\r\n")
    assert "Proxy-Authorization" not in head
    assert "Connection: close" in head


def test_proxy_tunnels_allowed_connect(confine, upstream):
    port, seen = upstream
    reply = _through_proxy(
        confine,
        [("127.0.0.1", port)],
        f"CONNECT 127.0.0.1:{port} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n\r\n".encode()
        + b"GET /inside HTTP/1.1\r\nHost: x\r\n\r\n",
    )
    assert reply.startswith(b"HTTP/1.1 200 Connection Established\r\n\r\nHTTP/1.1 200 OK")
    assert seen[0].startswith(b"GET /inside HTTP/1.1")


def test_proxy_denies_unlisted_port_and_host(confine, upstream):
    port, seen = upstream
    denied_port = _through_proxy(
        confine, [("127.0.0.1", port + 1)], f"CONNECT 127.0.0.1:{port} HTTP/1.1\r\n\r\n".encode()
    )
    denied_host = _through_proxy(
        confine,
        [("127.0.0.1", port)],
        b"GET http://example.com/ HTTP/1.1\r\nHost: example.com\r\n\r\n",
    )
    assert denied_port.startswith(b"HTTP/1.1 403")
    assert denied_host.startswith(b"HTTP/1.1 403")
    assert seen == []  # nothing reached the upstream


def test_proxy_refuses_origin_form_and_bad_ports(confine):
    assert _through_proxy(confine, [("h", 1)], b"GET / HTTP/1.1\r\nHost: x\r\n\r\n").startswith(
        b"HTTP/1.1 400"
    )
    assert _through_proxy(confine, [("h", 1)], "CONNECT h:1² HTTP/1.1\r\n\r\n".encode()).startswith(
        b"HTTP/1.1 400"
    )


def test_proxy_log_goes_to_the_descriptor_escaped(confine, tmp_path):
    log = tmp_path / "egress.log"
    fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        _through_proxy(
            confine,
            [("127.0.0.1", 1)],
            b"CONNECT evil.example\r\nFAKE ALLOW:443 HTTP/1.1\r\n\r\n",
            log_fd=fd,
        )
        _through_proxy(
            confine, [("127.0.0.1", 1)], b"CONNECT deny.example:443 HTTP/1.1\r\n\r\n", fd
        )
    finally:
        os.close(fd)
    lines = log.read_text().splitlines()
    assert len(lines) == 2  # no forged line from the CR/LF inside the target
    assert "DENY 'CONNECT' 'deny.example':443" in lines[1]


def test_root_egress_log_is_private_and_refuses_tampered_files(confine, tmp_path):
    root = tmp_path / "log"
    fd = confine.open_root_egress_log(3001, str(root))
    try:
        path = root / "3001.egress.log"
        assert (path.stat().st_mode & 0o777) == 0o600
        assert os.get_inheritable(fd)  # handed down the exec chain
        os.write(fd, b"line\n")
        assert path.read_text() == "line\n"
    finally:
        os.close(fd)
    # a symlink, a FIFO (which would block a plain open) and a hard link are refused
    (root / "3002.egress.log").symlink_to(tmp_path / "elsewhere")
    os.mkfifo(root / "3003.egress.log")
    os.link(root / "3001.egress.log", tmp_path / "second-link")
    for uid in (3002, 3003, 3001):
        with pytest.raises(confine.ConfineError):
            confine.open_root_egress_log(uid, str(root))


def test_root_egress_log_refuses_a_foreign_or_writable_directory(confine, tmp_path):
    root = tmp_path / "log"
    root.mkdir(mode=0o777)
    root.chmod(0o777)
    with pytest.raises(confine.ConfineError, match="not a directory controlled"):
        confine.open_root_egress_log(3001, str(root))


def test_serve_bounds_concurrent_handlers(confine):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(16)
    release = threading.Event()
    started = []

    def _handler(conn):
        started.append(conn)
        release.wait(5)
        conn.close()

    threading.Thread(target=confine._serve, args=(listener, _handler, "t", 2), daemon=True).start()
    clients = [socket.create_connection(listener.getsockname()) for _ in range(3)]
    deadline = time.monotonic() + 5
    while len(started) < 2 and time.monotonic() < deadline:
        time.sleep(0.05)
    clients[2].settimeout(2)
    assert clients[2].recv(1) == b""  # the third connection is closed, not queued
    assert len(started) == 2
    release.set()
    for client in clients:
        client.close()
    listener.close()


def test_package_tree_must_be_root_controlled(confine, tmp_path):
    pkg = tmp_path / "node_modules" / "qwen-code-webui" / "dist"
    pkg.mkdir(parents=True)
    entry = pkg / "cli.js"
    entry.write_text("x")
    # owned by the test user, not root: refused
    with pytest.raises(confine.ConfineError, match="not root-owned"):
        confine.require_root_controlled_package(str(entry))
    # outside any node_modules tree the executable check alone applies
    plain = tmp_path / "plain-webui"
    plain.write_text("x")
    confine.require_root_controlled_package(str(plain))
