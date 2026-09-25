#!/usr/bin/python3 -I
"""openace-webui-confine — confined per-user WebUI launch (Issue #3431, Option 1).

Installed as ``/usr/local/bin/openace-webui-confine``. One file, three modes,
three trust levels:

``launch``     root, via ``sudo -n``. The ONLY root code. It validates its
               typed arguments, reads the WebUI environment (JSON) from stdin,
               and execs ``systemd-run --scope`` with limits it builds itself —
               no caller-supplied property ever reaches systemd — followed by
               ``setpriv`` to drop to the target account (``--init-groups``:
               ``systemd-run --scope --uid`` keeps the CALLER's supplementary
               groups, i.e. root's group 0, which setpriv does not). The
               environment is handed on through a pipe on stdin, so the proxy
               token never appears in a world-readable command line or in the
               (world-readable) transient unit properties.
``supervise``  the target account, host network namespace, inside the scope's
               cgroup. Owns the two host-side endpoints — the ingress forwarder
               (``bind_host:port`` -> ``ingress.sock``) and the egress proxy
               (``egress.sock`` -> allowlisted ``host:port`` only) — and runs
               bubblewrap.
``inner``      the target account inside bubblewrap: private network namespace
               (loopback only), read-only host root, empty tmpfs over the
               workspace base / ``/tmp`` / ``/run``. Bridges the two sockets to
               loopback TCP and runs the WebUI with ``HTTP(S)_PROXY`` set.

The sandbox has no route out except the egress proxy, so egress control is
structural: anything that ignores the proxy variables gets no network at all.
The proxy decides on the client-supplied host name and port and does not
inspect TLS (the same residual Claude Code documents for its sandbox proxy).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import pwd
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Sequence

# ── Shared constants ────────────────────────────────────────────────────────

MIN_ACCOUNT_UID = 1000
INNER_SOCKET_DIR = "/run/openace"
INGRESS_SOCKET = "ingress.sock"
EGRESS_SOCKET = "egress.sock"
SAFE_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
# Root-owned policy file written by the installer. ``launch`` refuses to run
# without it: it pins which executables may be started as a user (a free
# ``--webui`` would let the service account run anything as any account) and
# the PATH the WebUI sees inside the sandbox (node may live outside SAFE_PATH).
CONFIG_PATH = "/etc/openace/webui-confine.json"
LOG_DIR_RE = re.compile(r"^/tmp/qwen-code-webui-[0-9]+$")
ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# Keys the caller may never set: they steer the dynamic loader / interpreters
# or the proxy wiring this script owns.
ENV_KEY_DENY_PREFIXES = ("LD_", "PYTHON", "NODE_OPTIONS", "BASH_ENV", "ENV")
ENV_KEY_OWNED = frozenset(
    {
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "http_proxy",
        "https_proxy",
        "NO_PROXY",
        "no_proxy",
        "ALL_PROXY",
        "all_proxy",
        "NODE_USE_ENV_PROXY",
        "HOME",
        "PATH",
    }
)
MEMORY_RE = re.compile(r"^[1-9][0-9]{0,12}[KMGT]?$")
HOST_RE = re.compile(
    r"^(\*\.)?[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$"
)
MAX_HEAD_BYTES = 64 * 1024
CONNECT_TIMEOUT_SECONDS = 15.0
PIPE_BUFFER = 65536


class ConfineError(Exception):
    """A refused launch (exit status 64, message on stderr)."""


# ── Validation helpers (pure; unit-tested) ──────────────────────────────────


def parse_allow_entry(raw: str) -> tuple[str, int]:
    """Parse ``host:port`` / ``[v6]:port`` / ``*.domain:port`` into (host, port)."""
    raw = raw.strip()
    if raw.startswith("["):
        host, sep, port_s = raw[1:].partition("]:")
        if not sep:
            raise ConfineError(f"allow entry {raw!r}: expected [ipv6]:port")
        _require_ip(host, raw)
    else:
        host, sep, port_s = raw.rpartition(":")
        if not sep or not host:
            raise ConfineError(f"allow entry {raw!r}: expected host:port")
        if not HOST_RE.fullmatch(host) and not _is_ip(host):
            raise ConfineError(f"allow entry {raw!r}: invalid host")
    port = _parse_port(port_s, f"allow entry {raw!r}")
    return host.lower(), port


def host_allowed(host: str, port: int, allow: Sequence[tuple[str, int]]) -> bool:
    """Exact host match, or ``*.domain`` matching strict subdomains; ports exact."""
    host = host.strip().lower().rstrip(".")
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    for allowed_host, allowed_port in allow:
        if port != allowed_port:
            continue
        if allowed_host.startswith("*."):
            if host.endswith(allowed_host[1:]) and host != allowed_host[2:]:
                return True
        elif host == allowed_host:
            return True
    return False


def validate_env(env: object) -> dict[str, str]:
    """Validate the stdin environment object; returns a clean str->str dict."""
    if not isinstance(env, dict):
        raise ConfineError("environment must be a JSON object")
    clean: dict[str, str] = {}
    for key, value in env.items():
        if not isinstance(key, str) or not ENV_KEY_RE.fullmatch(key):
            raise ConfineError(f"environment key {key!r} is not a valid name")
        if key in ENV_KEY_OWNED or key.startswith(ENV_KEY_DENY_PREFIXES):
            raise ConfineError(f"environment key {key!r} is reserved")
        if not isinstance(value, str) or "\0" in value:
            raise ConfineError(f"environment value for {key!r} must be a NUL-free string")
        clean[key] = value
    return clean


def resolve_account(name: str) -> pwd.struct_passwd:
    """Resolve a non-root, non-reserved account with a usable home."""
    try:
        entry = pwd.getpwnam(name)
    except KeyError:
        raise ConfineError(f"account {name!r} does not exist") from None
    if entry.pw_uid < MIN_ACCOUNT_UID or entry.pw_gid == 0:
        raise ConfineError(f"account {name!r} is privileged or reserved (uid {entry.pw_uid})")
    home = os.path.normpath(entry.pw_dir or "")
    if not home.startswith("/") or os.path.dirname(home) in ("", "/"):
        raise ConfineError(f"account {name!r} home {home!r} is not under a workspace base")
    return entry


def workspace_layout(entry: pwd.struct_passwd) -> tuple[str, str, str | None]:
    """Return (base, home, shared_root_or_None) for the account.

    Derived from the passwd entry, never from the caller: the base is the
    home's parent (``<base>/<account>``), hidden behind an empty tmpfs in the
    sandbox; the home and the shared-project namespace root ``<base>/shared``
    are the only entries bound back.
    """
    home = os.path.normpath(entry.pw_dir)
    home_st = os.lstat(home)
    if not stat.S_ISDIR(home_st.st_mode) or home_st.st_uid != entry.pw_uid:
        raise ConfineError(f"home {home!r} is not a directory owned by {entry.pw_name!r}")
    base = os.path.dirname(home)
    shared = os.path.join(base, "shared")
    try:
        shared_st = os.lstat(shared)
    except FileNotFoundError:
        return base, home, None
    if not stat.S_ISDIR(shared_st.st_mode) or shared == home:
        return base, home, None
    return base, home, shared


def load_policy(path: str = CONFIG_PATH) -> tuple[frozenset[str], str]:
    """Read the root-owned policy file: (allowed webui executables, sandbox PATH).

    Refuses a file (or directory chain) that a non-root user could have
    written: the file pins what root will start on the caller's behalf.
    """
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise ConfineError(f"policy file {path} is unreadable ({exc.strerror})") from None
    with os.fdopen(fd, encoding="utf-8") as handle:
        st = os.fstat(handle.fileno())
        if st.st_uid != 0 or st.st_mode & 0o022:
            raise ConfineError(
                f"policy file {path} must be root-owned and not group/world-writable"
            )
        directory = os.path.dirname(path)
        while directory not in ("", "/"):
            dst = os.lstat(directory)
            if dst.st_uid != 0 or dst.st_mode & 0o022 or stat.S_ISLNK(dst.st_mode):
                raise ConfineError(f"policy directory {directory} is not root-controlled")
            directory = os.path.dirname(directory)
        try:
            data = json.load(handle)
        except json.JSONDecodeError as exc:
            raise ConfineError(f"policy file {path} is not JSON: {exc}") from None
    webuis = data.get("webui") if isinstance(data, dict) else None
    if not isinstance(webuis, list) or not all(
        isinstance(w, str) and os.path.isabs(w) for w in webuis
    ):
        raise ConfineError(f"policy file {path}: 'webui' must be a list of absolute paths")
    sandbox_path = data.get("path", SAFE_PATH)
    if not isinstance(sandbox_path, str) or not all(
        os.path.isabs(part) for part in sandbox_path.split(":")
    ):
        raise ConfineError(f"policy file {path}: 'path' must be absolute directories joined by ':'")
    return frozenset(webuis), sandbox_path


def validate_log_dir(path: str, entry: pwd.struct_passwd) -> str:
    """The webui log dir: ``/tmp/qwen-code-webui-<n>``, a real dir owned by the account."""
    if not LOG_DIR_RE.fullmatch(path):
        raise ConfineError(f"log dir {path!r} is not /tmp/qwen-code-webui-<n>")
    st = os.lstat(path)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != entry.pw_uid:
        raise ConfineError(f"log dir {path!r} is not a directory owned by {entry.pw_name!r}")
    return path


def build_bwrap_argv(
    *,
    bwrap: str,
    python: str,
    script: str,
    base: str,
    home: str,
    shared: str | None,
    log_dir: str,
    socket_dir: str,
    port: int,
    webui_argv: Sequence[str],
) -> list[str]:
    """The bubblewrap command line. Order matters: tmpfs mounts precede binds."""
    argv = [
        bwrap,
        "--ro-bind", "/", "/",
        "--dev", "/dev",
        "--proc", "/proc",
        "--tmpfs", "/tmp",
        "--tmpfs", "/var/tmp",
        "--tmpfs", "/run",
        "--tmpfs", base,
        "--bind", home, home,
    ]  # fmt: skip
    if shared:
        argv += ["--bind", shared, shared]
    argv += [
        "--bind", log_dir, log_dir,
        "--bind", socket_dir, INNER_SOCKET_DIR,
        "--unshare-user",
        "--unshare-ipc",
        "--unshare-pid",
        "--unshare-uts",
        "--unshare-cgroup-try",
        "--unshare-net",
        "--die-with-parent",
        "--new-session",
        "--cap-drop", "ALL",
        "--chdir", home,
        "--", python, "-I", script, "inner", "--port", str(port), "--", *webui_argv,
    ]  # fmt: skip
    return argv


def build_inner_env(env: dict[str, str], *, home: str, sandbox_path: str) -> dict[str, str]:
    """The environment bubblewrap is started with (and passes to ``inner``).

    Passed as the process environment, NEVER as ``--setenv`` arguments: a
    command line is world-readable in /proc, an environment is readable only
    by the same uid and root. The proxy variables are not set here either —
    ``inner`` binds its bridge on an ephemeral loopback port and sets them for
    the WebUI itself, so the bridge cannot collide with the WebUI port range.
    """
    inner_env = dict(env)
    inner_env.update({"HOME": home, "PATH": sandbox_path, "LANG": env.get("LANG", "C.UTF-8")})
    return inner_env


# ── Byte pumps (supervise + inner) ──────────────────────────────────────────


def _pump(src: socket.socket, dst: socket.socket) -> None:
    try:
        while True:
            chunk = src.recv(PIPE_BUFFER)
            if not chunk:
                break
            dst.sendall(chunk)
    except OSError:
        pass
    finally:
        with contextlib.suppress(OSError):
            dst.shutdown(socket.SHUT_WR)


def splice(a: socket.socket, b: socket.socket, initial_to_b: bytes = b"") -> None:
    """Copy both directions until both sides close; closes both sockets."""
    try:
        if initial_to_b:
            b.sendall(initial_to_b)
        worker = threading.Thread(target=_pump, args=(b, a), daemon=True)
        worker.start()
        _pump(a, b)
        worker.join()
    except OSError:
        pass
    finally:
        for sock in (a, b):
            with contextlib.suppress(OSError):
                sock.close()


def _serve(listener: socket.socket, handler, name: str) -> None:
    while True:
        try:
            conn, _ = listener.accept()
        except OSError:
            return
        threading.Thread(target=handler, args=(conn,), daemon=True, name=name).start()


def _unix_listener(path: str) -> socket.socket:
    with contextlib.suppress(FileNotFoundError):
        os.unlink(path)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    old = os.umask(0o077)
    try:
        sock.bind(path)
    finally:
        os.umask(old)
    sock.listen(64)
    return sock


# ── Egress proxy (supervise) ────────────────────────────────────────────────


def _read_head(conn: socket.socket) -> tuple[bytes, bytes]:
    buf = b""
    while b"\r\n\r\n" not in buf:
        if len(buf) > MAX_HEAD_BYTES:
            raise ValueError("request head too large")
        chunk = conn.recv(PIPE_BUFFER)
        if not chunk:
            raise ValueError("connection closed before the request head")
        buf += chunk
    head, _, rest = buf.partition(b"\r\n\r\n")
    return head, rest


def _split_authority(authority: str, default_port: int) -> tuple[str, int]:
    if authority.startswith("["):
        host, _, rest = authority[1:].partition("]")
        port_s = rest[1:] if rest.startswith(":") else ""
    else:
        host, sep, port_s = authority.rpartition(":")
        if not sep:
            host, port_s = authority, ""
    port = int(port_s) if port_s else default_port
    if not host or not 0 < port < 65536:
        raise ValueError(f"bad authority {authority!r}")
    return host, port


class EgressProxy:
    """HTTP proxy on a Unix socket that only reaches allowlisted host:port pairs."""

    def __init__(self, allow: Sequence[tuple[str, int]], log_path: str | None = None) -> None:
        self.allow = tuple(allow)
        self.log_path = log_path
        self._log_lock = threading.Lock()

    def log(self, message: str) -> None:
        if not self.log_path:
            return
        line = f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {message}\n"
        with self._log_lock, contextlib.suppress(OSError):
            with open(self.log_path, "a", encoding="utf-8") as handle:
                handle.write(line)

    def handle(self, conn: socket.socket) -> None:
        try:
            head, rest = _read_head(conn)
            request_line, *header_lines = head.decode("iso-8859-1").split("\r\n")
            method, target, version = request_line.split(" ", 2)
            if method.upper() == "CONNECT":
                host, port = _split_authority(target, 443)
                forward = b""
            else:
                if not target.lower().startswith("http://"):
                    self._reply(conn, 400, "only absolute http:// targets or CONNECT are proxied")
                    return
                authority, slash, path = target[len("http://") :].partition("/")
                host, port = _split_authority(authority, 80)
                headers = [
                    line
                    for line in header_lines
                    if line
                    and not line.lower().startswith(("proxy-", "connection:", "keep-alive:"))
                ]
                headers.append("Connection: close")
                new_head = "\r\n".join([f"{method} {slash}{path} {version}", *headers])
                forward = new_head.encode("iso-8859-1") + b"\r\n\r\n" + rest
            if not host_allowed(host, port, self.allow):
                self.log(f"DENY {method} {host}:{port}")
                self._reply(conn, 403, f"egress to {host}:{port} is not in the allowlist")
                return
            try:
                upstream = socket.create_connection((host, port), timeout=CONNECT_TIMEOUT_SECONDS)
                upstream.settimeout(None)
            except OSError as exc:
                self.log(f"FAIL {method} {host}:{port} {exc}")
                self._reply(conn, 502, f"cannot reach {host}:{port}")
                return
            self.log(f"ALLOW {method} {host}:{port}")
            if method.upper() == "CONNECT":
                conn.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                if rest:
                    upstream.sendall(rest)
            splice(conn, upstream, forward)
        except (ValueError, OSError) as exc:
            self.log(f"BAD {exc}")
            with contextlib.suppress(OSError):
                self._reply(conn, 400, "malformed proxy request")
        finally:
            with contextlib.suppress(OSError):
                conn.close()

    @staticmethod
    def _reply(conn: socket.socket, status: int, message: str) -> None:
        reason = {400: "Bad Request", 403: "Forbidden", 502: "Bad Gateway"}[status]
        body = f"openace-webui-confine: {message}\n".encode()
        conn.sendall(
            f"HTTP/1.1 {status} {reason}\r\nContent-Type: text/plain\r\n"
            f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body
        )


# ── Mode: launch (root) ─────────────────────────────────────────────────────


def _parse_port(raw: str, what: str) -> int:
    if not raw.isdigit():
        raise ConfineError(f"{what}: port must be numeric")
    port = int(raw)
    if not 0 < port < 65536:
        raise ConfineError(f"{what}: port out of range")
    return port


def _is_ip(value: str) -> bool:
    for family in (socket.AF_INET, socket.AF_INET6):
        try:
            socket.inet_pton(family, value)
            return True
        except OSError:
            continue
    return False


def _require_ip(value: str, what: str) -> None:
    if not _is_ip(value):
        raise ConfineError(f"{what}: not an IP address")


def _launch_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="openace-webui-confine launch", add_help=False)
    parser.add_argument("--account", required=True)
    parser.add_argument("--port", required=True)
    parser.add_argument("--bind-host", default="0.0.0.0")  # noqa: S104 - today's exposure
    parser.add_argument("--memory-max", required=True)
    parser.add_argument("--cpu-quota", required=True)
    parser.add_argument("--tasks-max", required=True)
    parser.add_argument("--allow", action="append", default=[])
    parser.add_argument("--log-dir", required=True)
    parser.add_argument("--webui", required=True)
    parser.add_argument("webui_args", nargs=argparse.REMAINDER)
    return parser


def plan_launch(
    argv: Sequence[str], env_text: str, policy_path: str = CONFIG_PATH
) -> tuple[list[str], dict[str, object]]:
    """Validate a launch request; return (systemd-run argv, supervisor payload).

    Pure apart from passwd/filesystem lookups, so the whole root decision is
    unit-testable without root.
    """
    args = _launch_parser().parse_args(list(argv))
    webui_args = list(args.webui_args)
    if webui_args[:1] == ["--"]:
        webui_args = webui_args[1:]
    entry = resolve_account(args.account)
    port = _parse_port(args.port, "--port")
    if port < 1024:
        raise ConfineError("--port must be >= 1024")
    _require_ip(args.bind_host, "--bind-host")
    if not MEMORY_RE.fullmatch(args.memory_max):
        raise ConfineError("--memory-max must look like 4G / 512M / bytes")
    cpu = int(args.cpu_quota) if args.cpu_quota.isdigit() else -1
    if not 1 <= cpu <= 6400:
        raise ConfineError("--cpu-quota must be an integer percentage 1..6400")
    tasks = int(args.tasks_max) if args.tasks_max.isdigit() else -1
    if not 16 <= tasks <= 65535:
        raise ConfineError("--tasks-max must be 16..65535")
    allow = [parse_allow_entry(item) for item in args.allow]
    if not allow:
        raise ConfineError("at least one --allow host:port is required (the LLM proxy)")
    allowed_webuis, sandbox_path = load_policy(policy_path)
    webui = args.webui
    if webui not in allowed_webuis:
        raise ConfineError(f"--webui {webui!r} is not listed in {policy_path}")
    if not os.path.isfile(webui) or not os.access(webui, os.X_OK):
        raise ConfineError(f"--webui {webui!r} is not an executable file")
    log_dir = validate_log_dir(args.log_dir, entry)
    base, home, shared = workspace_layout(entry)
    try:
        env = validate_env(json.loads(env_text or "{}"))
    except json.JSONDecodeError as exc:
        raise ConfineError(f"environment on stdin is not JSON: {exc}") from None
    for tool in ("systemd-run", "setpriv", "bwrap"):
        if shutil.which(tool, path=SAFE_PATH) is None:
            raise ConfineError(f"{tool} is not installed")
    python = sys.executable or "/usr/bin/python3"
    script = os.path.realpath(__file__)
    unit = f"openace-webui-{entry.pw_name}-{port}"
    systemd_argv = [
        shutil.which("systemd-run", path=SAFE_PATH) or "systemd-run",
        "--scope",
        "--quiet",
        "--collect",
        f"--unit={unit}",
        "-p", f"MemoryMax={args.memory_max}",
        "-p", "MemorySwapMax=0",
        "-p", f"CPUQuota={cpu}%",
        "-p", f"TasksMax={tasks}",
        "--",
        shutil.which("setpriv", path=SAFE_PATH) or "setpriv",
        f"--reuid={entry.pw_uid}",
        f"--regid={entry.pw_gid}",
        "--init-groups",
        "--no-new-privs",
        "--inh-caps=-all",
        "--bounding-set=-all",
        "--",
        python, "-I", script, "supervise",
    ]  # fmt: skip
    payload: dict[str, object] = {
        "account": entry.pw_name,
        "port": port,
        "bind_host": args.bind_host,
        "allow": [f"[{h}]:{p}" if ":" in h else f"{h}:{p}" for h, p in allow],
        "log_dir": log_dir,
        "base": base,
        "home": home,
        "shared": shared,
        "webui_argv": [webui, *webui_args],
        "env": env,
        "path": sandbox_path,
        "bwrap": shutil.which("bwrap", path=SAFE_PATH),
        "python": python,
        "script": script,
    }
    return systemd_argv, payload


def run_launch(argv: Sequence[str]) -> int:
    if os.geteuid() != 0:
        raise ConfineError("launch must run as root (via sudo)")
    systemd_argv, payload = plan_launch(argv, sys.stdin.read())
    read_fd, write_fd = os.pipe()
    data = json.dumps(payload).encode()
    if len(data) > PIPE_BUFFER - 1:
        raise ConfineError("environment too large for the hand-off pipe")
    os.write(write_fd, data)
    os.close(write_fd)
    os.dup2(read_fd, 0)
    os.close(read_fd)
    os.execve(
        systemd_argv[0],
        systemd_argv,
        {"PATH": SAFE_PATH, "LANG": "C.UTF-8"},
    )
    return 0  # pragma: no cover - execve does not return


# ── Mode: supervise (target account, host side) ────────────────────────────


def run_supervise() -> int:
    payload = json.loads(sys.stdin.read())
    parent = os.getppid()
    allow = [parse_allow_entry(item) for item in payload["allow"]]
    socket_dir = tempfile.mkdtemp(prefix="openace-webui-")
    egress_path = os.path.join(socket_dir, EGRESS_SOCKET)
    ingress_path = os.path.join(socket_dir, INGRESS_SOCKET)
    proxy = EgressProxy(allow, os.path.join(payload["log_dir"], "confine-egress.log"))
    egress_listener = _unix_listener(egress_path)
    threading.Thread(
        target=_serve, args=(egress_listener, proxy.handle, "egress"), daemon=True
    ).start()

    ingress_listener = socket.socket(
        socket.AF_INET6 if ":" in payload["bind_host"] else socket.AF_INET, socket.SOCK_STREAM
    )
    ingress_listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    ingress_listener.bind((payload["bind_host"], int(payload["port"])))
    ingress_listener.listen(128)

    def _ingress(conn: socket.socket) -> None:
        upstream = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            upstream.connect(ingress_path)
        except OSError:
            with contextlib.suppress(OSError):
                conn.close()
            upstream.close()
            return
        splice(conn, upstream)

    threading.Thread(
        target=_serve, args=(ingress_listener, _ingress, "ingress"), daemon=True
    ).start()

    argv = build_bwrap_argv(
        bwrap=payload["bwrap"],
        python=payload["python"],
        script=payload["script"],
        base=payload["base"],
        home=payload["home"],
        shared=payload["shared"],
        log_dir=payload["log_dir"],
        socket_dir=socket_dir,
        port=int(payload["port"]),
        webui_argv=payload["webui_argv"],
    )
    child_env = build_inner_env(payload["env"], home=payload["home"], sandbox_path=payload["path"])
    child = subprocess.Popen(  # noqa: S603 - fixed argv
        argv, stdin=subprocess.DEVNULL, env=child_env
    )

    def _stop(signum, _frame) -> None:
        with contextlib.suppress(ProcessLookupError):
            child.send_signal(signum)

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    try:
        while True:
            try:
                return child.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                # The manager signals sudo; SIGKILL on sudo is not forwarded.
                # Reparenting means our launcher is gone: stop with it.
                if os.getppid() != parent:
                    child.terminate()
    finally:
        shutil.rmtree(socket_dir, ignore_errors=True)


# ── Mode: inner (inside bubblewrap) ─────────────────────────────────────────


def run_inner(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="openace-webui-confine inner", add_help=False)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("webui_argv", nargs=argparse.REMAINDER)
    args = parser.parse_args(list(argv))
    webui_argv = list(args.webui_argv)
    if webui_argv[:1] == ["--"]:
        webui_argv = webui_argv[1:]

    ingress_listener = _unix_listener(os.path.join(INNER_SOCKET_DIR, INGRESS_SOCKET))

    def _to_webui(conn: socket.socket) -> None:
        try:
            upstream = socket.create_connection(("127.0.0.1", args.port), timeout=10)
            upstream.settimeout(None)
        except OSError:
            with contextlib.suppress(OSError):
                conn.close()
            return
        splice(conn, upstream)

    proxy_listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    proxy_listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    proxy_listener.bind(("127.0.0.1", 0))
    proxy_listener.listen(128)
    proxy_url = f"http://127.0.0.1:{proxy_listener.getsockname()[1]}"
    child_env = dict(os.environ)
    child_env.update(
        {
            "HTTP_PROXY": proxy_url,
            "HTTPS_PROXY": proxy_url,
            "http_proxy": proxy_url,
            "https_proxy": proxy_url,
            "NO_PROXY": "localhost,127.0.0.1,::1",
            "no_proxy": "localhost,127.0.0.1,::1",
            # Node's built-in fetch honours the proxy variables only with this
            # flag (Node >= 24); a runtime that ignores them gets no network.
            "NODE_USE_ENV_PROXY": "1",
        }
    )

    def _to_proxy(conn: socket.socket) -> None:
        upstream = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            upstream.connect(os.path.join(INNER_SOCKET_DIR, EGRESS_SOCKET))
        except OSError:
            with contextlib.suppress(OSError):
                conn.close()
            upstream.close()
            return
        splice(conn, upstream)

    threading.Thread(target=_serve, args=(ingress_listener, _to_webui, "in"), daemon=True).start()
    threading.Thread(target=_serve, args=(proxy_listener, _to_proxy, "out"), daemon=True).start()
    child = subprocess.Popen(webui_argv, stdin=subprocess.DEVNULL, env=child_env)  # noqa: S603

    def _stop(signum, _frame) -> None:
        with contextlib.suppress(ProcessLookupError):
            child.send_signal(signum)

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    return child.wait()


# ── Mode: check (readiness probe, unprivileged) ─────────────────────────────


def run_check(argv: Sequence[str], policy_path: str = CONFIG_PATH) -> int:
    """Exit 0 when this host can confine: tools present, systemd running,
    the policy file valid and listing ``--webui``, and unprivileged user
    namespaces usable by bubblewrap (the Ubuntu 24.04+ AppArmor restriction is
    the common failure). Prints one token the manager maps to a reason code."""
    parser = argparse.ArgumentParser(prog="openace-webui-confine check", add_help=False)
    parser.add_argument("--webui", default="")
    args = parser.parse_args(list(argv))
    for tool in ("systemd-run", "setpriv", "bwrap"):
        if shutil.which(tool, path=SAFE_PATH) is None:
            print(f"missing:{tool}")
            return 1
    if not os.path.isdir("/run/systemd/system"):
        print("systemd:not-running")
        return 1
    try:
        allowed_webuis, _ = load_policy(policy_path)
    except ConfineError as exc:
        print(f"# {exc}", file=sys.stderr)
        print("policy:invalid")
        return 1
    if args.webui and args.webui not in allowed_webuis:
        print(f"# {args.webui} is not listed in {policy_path}", file=sys.stderr)
        print("policy:invalid")
        return 1
    bwrap = shutil.which("bwrap", path=SAFE_PATH) or "bwrap"
    probe = subprocess.run(  # noqa: S603 - fixed argv
        [bwrap, "--ro-bind", "/", "/", "--unshare-user", "--unshare-net", "--", "/bin/true"],
        capture_output=True,
        timeout=10,
        check=False,
    )
    if probe.returncode != 0:
        print("userns:unavailable")
        return 1
    print("ok")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    mode, rest = (args[0], args[1:]) if args else ("", [])
    try:
        if mode == "launch":
            return run_launch(rest)
        if mode == "supervise":
            return run_supervise()
        if mode == "inner":
            return run_inner(rest)
        if mode == "check":
            return run_check(rest)
        raise ConfineError("usage: openace-webui-confine {launch|check} ...")
    except ConfineError as exc:
        print(f"openace-webui-confine: {exc}", file=sys.stderr)
        return 64


if __name__ == "__main__":
    sys.exit(main())
