"""Issue #3431 (Option 1): scripts/openace-webui-confine.py unit tests.

The wrapper's root mode (``launch``) is a security boundary, so its decisions
are pinned here without root: allowlist parsing/matching, environment
validation, the policy-file ownership rules, the systemd-run / setpriv command
line it execs, and the bubblewrap argv. The egress proxy is exercised over
real sockets. The Linux end-to-end run (real systemd scope + bubblewrap) lives
in tests/integration/subprocess/test_webui_confine_linux_3431.py.
"""

from __future__ import annotations

import importlib.util
import json
import os
import pwd
import socket
import threading
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
     "[not-ip]:80", "example.com:http", "*.*.com:443", "a..b:80"],
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
    env = {"OPENAI_API_KEY": "tok", "OPENACE_LOG_DIR": "/tmp/x", "LANG": "C.UTF-8"}
    assert confine.validate_env(env) == env


@pytest.mark.parametrize(
    "env",
    [
        {"LD_PRELOAD": "/tmp/evil.so"},
        {"PYTHONPATH": "/tmp"},
        {"NODE_OPTIONS": "--require /tmp/x"},
        {"PATH": "/tmp"},
        {"HOME": "/root"},
        {"HTTPS_PROXY": "http://attacker:1"},
        {"bad key": "x"},
        {"OK": 1},
        {"OK": "a\0b"},
        ["not", "a", "dict"],
    ],
)
def test_validate_env_rejects(confine, env):
    with pytest.raises(confine.ConfineError):
        confine.validate_env(env)


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
    # tmp_path's ancestors are not root-owned on a dev box; patching lstat to
    # uid 0 covers the directory walk too.
    path = _policy_file(
        tmp_path, {"webui": ["/usr/bin/qwen-code-webui"], "path": "/opt/node/bin:/usr/bin"}
    )
    for parent in [path.parent, *path.parent.parents]:
        if parent.stat().st_mode & 0o022:
            pytest.skip(f"{parent} is group/world-writable on this machine")
    _as_root_owned(monkeypatch, confine)
    webuis, sandbox_path = confine.load_policy(str(path))
    assert webuis == frozenset({"/usr/bin/qwen-code-webui"})
    assert sandbox_path == "/opt/node/bin:/usr/bin"


@pytest.mark.parametrize(
    "data",
    [
        {"webui": "/usr/bin/x"},
        {"webui": ["relative/x"]},
        {"webui": ["/x"], "path": "rel:/usr/bin"},
        "not json",
    ],
)
def test_policy_rejects_bad_content(confine, tmp_path, monkeypatch, data):
    path = _policy_file(tmp_path, data)
    for parent in [path.parent, *path.parent.parents]:
        if parent.stat().st_mode & 0o022:
            pytest.skip(f"{parent} is group/world-writable on this machine")
    _as_root_owned(monkeypatch, confine)
    with pytest.raises(confine.ConfineError):
        confine.load_policy(str(path))


# ── launch planning (the root decision) ─────────────────────────────────────


@pytest.fixture
def planned(confine, monkeypatch, tmp_path):
    """plan_launch with passwd / filesystem / tool lookups stubbed out."""
    entry = pwd.struct_passwd(("alice", "x", 3001, 3001, "", "/wsbase/alice", "/bin/bash"))
    monkeypatch.setattr(confine, "resolve_account", lambda name: entry)
    monkeypatch.setattr(
        confine, "workspace_layout", lambda e: ("/wsbase", "/wsbase/alice", "/wsbase/shared")
    )
    monkeypatch.setattr(confine, "validate_log_dir", lambda path, e: path)
    webui = tmp_path / "qwen-code-webui"
    webui.write_text("#!/bin/sh\n")
    webui.chmod(0o755)
    monkeypatch.setattr(
        confine, "load_policy", lambda path=None: (frozenset({str(webui)}), "/usr/bin:/bin")
    )
    monkeypatch.setattr(confine.shutil, "which", lambda tool, path=None: f"/usr/bin/{tool}")

    def _plan(*extra, env=None, argv=None):
        base = [
            "--account", "alice", "--port", "3150", "--memory-max", "4G",
            "--cpu-quota", "200", "--tasks-max", "512", "--allow", "10.0.0.5:19888",
            "--log-dir", "/tmp/qwen-code-webui-7", "--webui", str(webui),
        ]  # fmt: skip
        args = argv if argv is not None else [*base, *extra, "--", "--port", "3150"]
        return confine.plan_launch(
            args, json.dumps(env if env is not None else {"OPENAI_API_KEY": "tok"})
        )

    _plan.webui = str(webui)
    return _plan


def test_plan_launch_builds_fixed_scope_and_setpriv(confine, planned):
    systemd_argv, payload = planned()
    assert systemd_argv[:5] == [
        "/usr/bin/systemd-run", "--scope", "--quiet", "--collect", "--unit=openace-webui-alice-3150",
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


@pytest.mark.parametrize(
    ("override", "match"),
    [
        (["--port", "80"], ">= 1024"),
        (["--memory-max", "4G;x"], "memory-max"),
        (["--cpu-quota", "0"], "cpu-quota"),
        (["--cpu-quota", "9999"], "cpu-quota"),
        (["--tasks-max", "5"], "tasks-max"),
        (["--bind-host", "evil"], "bind-host"),
        (["--webui", "/bin/sh"], "not listed"),
    ],
)
def test_plan_launch_rejects_bad_arguments(confine, planned, override, match):
    base = {
        "--account": "alice", "--port": "3150", "--memory-max": "4G", "--cpu-quota": "200",
        "--tasks-max": "512", "--log-dir": "/tmp/qwen-code-webui-7", "--webui": planned.webui,
    }  # fmt: skip
    base[override[0]] = override[1]
    argv = [item for pair in base.items() for item in pair] + ["--allow", "10.0.0.5:19888"]
    with pytest.raises(confine.ConfineError, match=match):
        planned(argv=argv)


def test_plan_launch_requires_an_allow_entry(confine, planned):
    argv = [
        "--account", "alice", "--port", "3150", "--memory-max", "4G", "--cpu-quota", "200",
        "--tasks-max", "512", "--log-dir", "/tmp/qwen-code-webui-7", "--webui", planned.webui,
    ]  # fmt: skip
    with pytest.raises(confine.ConfineError, match="--allow"):
        planned(argv=argv)


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


def test_resolve_account_refuses_root_and_reserved(confine):
    with pytest.raises(confine.ConfineError, match="privileged or reserved"):
        confine.resolve_account("root")
    with pytest.raises(confine.ConfineError, match="does not exist"):
        confine.resolve_account("no-such-account-3431")


# ── bubblewrap argv ─────────────────────────────────────────────────────────


def test_bwrap_argv_hides_base_before_binding_home(confine):
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
    assert "--bind /tmp/openace-webui-x /run/openace" in joined
    for flag in ("--unshare-net", "--unshare-pid", "--unshare-user", "--die-with-parent",
                 "--new-session"):  # fmt: skip
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


def _through_proxy(confine, allow, request: bytes) -> bytes:
    proxy = confine.EgressProxy(allow)
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
        confine,
        [("127.0.0.1", port + 1)],
        f"CONNECT 127.0.0.1:{port} HTTP/1.1\r\n\r\n".encode(),
    )
    denied_host = _through_proxy(
        confine,
        [("127.0.0.1", port)],
        b"GET http://example.com/ HTTP/1.1\r\nHost: example.com\r\n\r\n",
    )
    assert denied_port.startswith(b"HTTP/1.1 403")
    assert denied_host.startswith(b"HTTP/1.1 403")
    assert seen == []  # nothing reached the upstream


def test_proxy_refuses_origin_form_requests(confine):
    reply = _through_proxy(confine, [("127.0.0.1", 1)], b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
    assert reply.startswith(b"HTTP/1.1 400")
