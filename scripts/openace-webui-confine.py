#!/usr/bin/python3 -I
"""openace-webui-confine — confined per-user WebUI launch (Issue #3431, Option 1).

Installed as ``/usr/local/bin/openace-webui-confine``. One file, four modes:

``launch``     root, via ``sudo -n``. The ONLY root code. It validates its
               typed arguments against the passwd database and the root-owned
               policy file, reads the WebUI environment (JSON) from stdin, and
               execs ``systemd-run --scope`` with limits it builds itself — no
               caller-supplied property ever reaches systemd — followed by
               ``setpriv`` to drop to the target account (``--init-groups``:
               ``systemd-run --scope --uid`` keeps the CALLER's supplementary
               groups, i.e. root's group 0). The environment is handed on
               through a pipe on stdin, never on a command line. (The WebUI's
               own ``--token-secret`` argument is still on its command line:
               qwen-code-webui accepts it only as a flag.)
``supervise``  the target account, host network namespace, inside the scope's
               cgroup. Owns every host-side endpoint — the ingress listener on
               ``bind_host:port``, the reverse-tunnel socket and the egress
               proxy socket — and runs bubblewrap.
``inner``      the target account inside bubblewrap: private network namespace
               (loopback only), read-only host root, empty tmpfs over the
               workspace base / ``/tmp`` / ``/var/tmp`` / ``/run``. Keeps a small
               pool of outbound tunnel connections for ingress, bridges a
               loopback proxy port to the egress socket, and runs the WebUI.
``check``      unprivileged readiness probe for the manager.

Issue #3431 Option 2 adds a second backend, ``--backend container``: the
sandbox is a Docker container on a gVisor (runsc) runtime instead of
bubblewrap. ``launch`` then prepares a root-owned run directory, forks the
same ``supervise`` endpoints (as the account, via setpriv) and execs a fixed
``docker run`` whose image is digest-pinned in the policy; the WebUI
environment reaches ``inner`` on the container's stdin. ``launch --probe``
(root) verifies the runtime, the image, a gVisor kernel and host UNIX-socket
access (runsc needs ``--host-uds=open``) for the manager's readiness check.

Nothing on the host side ever follows a path the sandbox can write: the
socket directory is bound READ-ONLY into the sandbox, the sandbox only
connects out to it (ingress uses a reverse tunnel instead of a listener the
host would connect to), and the egress log is opened by ROOT in the
root-owned /var/log/openace-webui/ and handed down as a descriptor — the
account can append through it but can neither open, replace nor truncate the
file.

The sandbox has no route out except the egress proxy, so egress control is
structural: anything that ignores the proxy variables gets no network at all.
The proxy decides on the client-supplied host name and port and does not
inspect TLS (the same residual Claude Code documents for its sandbox proxy).
"""

from __future__ import annotations

import argparse
import contextlib
import grp
import ipaddress
import json
import os
import pwd
import queue
import re
import shutil
import signal
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Sequence
from typing import NamedTuple

# ── Shared constants ────────────────────────────────────────────────────────

MIN_ACCOUNT_UID = 1000
INNER_SOCKET_DIR = "/run/openace"
TUNNEL_SOCKET = "tunnel.sock"
EGRESS_SOCKET = "egress.sock"
EGRESS_LOG_ROOT = "/var/log/openace-webui"
# Container backend (Option 2): root-owned parent of the per-instance socket
# directories that are bind-mounted into containers (the account must not be
# able to swap a bind-mount source for a symlink).
RUN_ROOT = "/run/openace-webui"
CONTAINER_SCRIPT = "/opt/openace/confine.py"
IMAGE_RE = re.compile(r"^(?:[a-z0-9][a-z0-9._/:-]*@)?sha256:[0-9a-f]{64}$")
RUNTIME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
MOUNT_UNSAFE_RE = re.compile(r"[,\n\r\0=]")
SUPERVISOR_WATCH_SECONDS = 30.0
CONTAINER_MIN_HOST_PIDS = 256  # gVisor sentry host-thread floor (--pids-limit)
CONTAINER_REMOVE_WAIT_SECONDS = 30.0
PROTECTED_SYMLINKS = "/proc/sys/fs/protected_symlinks"
KVM_DEVICE = "/dev/kvm"


def symlinks_protected(path: str = PROTECTED_SYMLINKS) -> bool:
    """fs.protected_symlinks=1: docker (root) cannot be steered through a
    symlink the account plants in sticky /tmp — the log dir is bind-mounted
    by path. Default on Debian/Ubuntu/RHEL; required by the container backend."""
    try:
        with open(path, encoding="ascii") as handle:
            return handle.read().strip() == "1"
    except OSError:
        return False


SAFE_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
# Root-owned policy file written by the installer. ``launch`` refuses to run
# without it: it pins which executables may be started as a user (a free
# ``--webui`` would let the service account run anything as any account) and
# the PATH the WebUI sees inside the sandbox (node may live outside SAFE_PATH).
CONFIG_PATH = "/etc/openace/webui-confine.json"
LOG_DIR_RE = re.compile(r"^/tmp/qwen-code-webui-[0-9]+$")
ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
DIGITS_RE = re.compile(r"^[0-9]{1,9}$")
# Keys the caller may never set: they steer the dynamic loader / interpreters
# or the proxy wiring this script owns.
ENV_KEY_DENY_PREFIXES = ("LD_", "PYTHON", "NODE_OPTIONS")
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
        "ENV",
        "BASH_ENV",
    }
)
# Supplementary groups that carry host privileges the sandbox would inherit
# (file access checks inside the user namespace still use the real groups).
# The policy file can extend this list with "denied_groups".
PRIVILEGED_GROUPS = frozenset(
    {
        "root",
        "sudo",
        "wheel",
        "admin",
        "adm",
        "shadow",
        "disk",
        "docker",
        "lxd",
        "libvirt",
        "kvm",
        "systemd-journal",
        "staff",
        "incus",
        "incus-admin",
        "lpadmin",
    }
)
MEMORY_RE = re.compile(r"^[1-9][0-9]{0,12}[KMGT]?$")
HOST_RE = re.compile(
    r"^(\*\.)?[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$"
)
MAX_HEAD_BYTES = 64 * 1024
HEAD_TIMEOUT_SECONDS = 30.0
CONNECT_TIMEOUT_SECONDS = 15.0
PIPE_BUFFER = 65536
TUNNEL_POOL = 4  # idle reverse-tunnel connections kept by ``inner``
TUNNEL_POOL_MAX = 64  # the supervisor never holds more than this many
TUNNEL_GO = b"\x01"
TUNNEL_WAIT_SECONDS = 10.0
MAX_INGRESS_CLIENTS = 256  # upper bound; the real limit derives from TasksMax
# Every ingress client costs ~4 tasks in the scope (supervisor handler +
# splice, inner tunnel worker + splice); keep most of TasksMax for the WebUI.
TASKS_PER_INGRESS_CLIENT = 8
INGRESS_IDLE_SECONDS = 300.0  # both directions silent this long: close
LOG_FIELD_MAX = 256  # characters kept of each client-supplied log field
LOG_MAX_BYTES = 8 * 1024 * 1024  # per launch; then one "suppressed" line
LOG_ROTATE_BYTES = 8 * 1024 * 1024  # an older log this big is rotated at launch
LOG_LINES_PER_SECOND = 50
SUMMARY_SLOW_SECONDS = 60  # summary cadence once a class is past its ceiling
MAX_PACKAGE_ENTRIES = 200_000  # npm tree ownership walk bound


class ConfineError(Exception):
    """A refused launch (exit status 64, message on stderr)."""


class ContainerPolicy(NamedTuple):
    """The policy's ``container`` section (Option 2 backend)."""

    image: str  # name@sha256:<64 hex> or a local image id sha256:<64 hex>
    runtime: str  # gVisor: a Docker runtime registered for runsc --host-uds=open ("" = none)
    docker: str  # absolute path of the docker CLI
    kata_runtime: str = ""  # Kata (#3438): a registered name or io.containerd.kata.v2


class Policy(NamedTuple):
    """The root-owned policy file's content."""

    webuis: frozenset[str]
    path: str
    bases: frozenset[str]  # empty = any workspace base
    denied_groups: frozenset[str]
    container: ContainerPolicy | None = None


# ── Validation helpers (pure; unit-tested) ──────────────────────────────────


def _digits(raw: str, what: str) -> int:
    # str.isdigit() accepts non-ASCII digits that int() then rejects.
    if not DIGITS_RE.fullmatch(raw):
        raise ConfineError(f"{what} must be an ASCII integer")
    return int(raw)


def _parse_port(raw: str, what: str) -> int:
    port = _digits(raw, what)
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
    host = host.lower()
    return (canonical_ip(host) if _is_ip(host) else host), port


METHOD_RE = re.compile(r"^[A-Z]{1,16}$")


def normalize_host(host: str) -> str | None:
    """Canonical form of a request host, or None when it is not a host name/IP.

    Lower-case, without IPv6 brackets or a trailing dot. Anything else (spaces,
    control characters, empty labels) is refused — so one allowlisted host has
    exactly one spelling, in matching and in the log.
    """
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    host = host.lower()
    if host.endswith("."):
        host = host[:-1]
    if _is_ip(host):
        return canonical_ip(host)
    if HOST_RE.fullmatch(host) and not host.startswith("*."):
        return host
    return None


def canonical_ip(value: str) -> str:
    """One spelling per address (``0:0::1`` -> ``::1``)."""
    return ipaddress.ip_address(value).compressed


def match_allow(host: str, port: int, allow: Sequence[tuple[str, int]]) -> str | None:
    """The allowlist entry (``host:port`` as configured) matching a NORMALIZED
    host, or None. Exact match, or ``*.domain`` for strict subdomains."""
    for allowed_host, allowed_port in allow:
        if port != allowed_port:
            continue
        entry = f"[{allowed_host}]:{port}" if ":" in allowed_host else f"{allowed_host}:{port}"
        if allowed_host.startswith("*."):
            if host.endswith(allowed_host[1:]) and host != allowed_host[2:]:
                return entry
        elif host == allowed_host:
            return entry
    return None


def host_allowed(host: str, port: int, allow: Sequence[tuple[str, int]]) -> bool:
    """Whether *host* (any accepted spelling) matches an allowlist entry."""
    normalized = normalize_host(host.strip())
    return normalized is not None and match_allow(normalized, port, allow) is not None


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


def account_groups(entry: pwd.struct_passwd) -> dict[int, str]:
    """gid -> group name for every group the account would hold after --init-groups."""
    groups: dict[int, str] = {}
    for gid in os.getgrouplist(entry.pw_name, entry.pw_gid):
        try:
            groups[gid] = grp.getgrgid(gid).gr_name
        except KeyError:
            groups[gid] = str(gid)
    return groups


def resolve_account(
    name: str, denied_groups: frozenset[str] = PRIVILEGED_GROUPS
) -> pwd.struct_passwd:
    """Resolve a regular account: uid >= 1000, no privileged group, sane home."""
    try:
        entry = pwd.getpwnam(name)
    except KeyError:
        raise ConfineError(f"account {name!r} does not exist") from None
    if entry.pw_uid < MIN_ACCOUNT_UID:
        raise ConfineError(f"account {name!r} is privileged or reserved (uid {entry.pw_uid})")
    privileged = sorted(
        f"{name_}({gid})"
        for gid, name_ in account_groups(entry).items()
        if gid == 0 or name_ in denied_groups
    )
    if privileged:
        raise ConfineError(
            f"account {name!r} is in privileged group(s) {', '.join(privileged)}; "
            "a confined WebUI would carry them into the sandbox"
        )
    home = os.path.normpath(entry.pw_dir or "")
    if not home.startswith("/") or os.path.dirname(home) in ("", "/"):
        raise ConfineError(f"account {name!r} home {home!r} is not under a workspace base")
    return entry


def workspace_layout(
    entry: pwd.struct_passwd, bases: frozenset[str] = frozenset()
) -> tuple[str, str, str | None]:
    """Return (base, home, shared_root_or_None) for the account.

    Derived from the passwd entry, never from the caller: the base is the
    home's parent (``<base>/<account>``), hidden behind an empty tmpfs in the
    sandbox; the home and the shared-project namespace root ``<base>/shared``
    are the only entries bound back. ``bases`` (from the policy) restricts
    which workspace bases are acceptable.
    """
    home = os.path.normpath(entry.pw_dir)
    try:
        home_st = os.lstat(home)
    except OSError as exc:
        raise ConfineError(f"home {home!r} is not accessible ({exc.strerror})") from None
    if not stat.S_ISDIR(home_st.st_mode) or home_st.st_uid != entry.pw_uid:
        raise ConfineError(f"home {home!r} is not a directory owned by {entry.pw_name!r}")
    base = os.path.dirname(home)
    if bases and base not in bases:
        raise ConfineError(f"home {home!r} is outside the policy's workspace bases")
    shared = os.path.join(base, "shared")
    try:
        shared_st = os.lstat(shared)
    except OSError:
        return base, home, None
    if not stat.S_ISDIR(shared_st.st_mode) or shared == home:
        return base, home, None
    return base, home, shared


def _root_controlled(path: str) -> bool:
    """True when *path* and every ancestor are root-owned and not group/world-writable."""
    current = path
    while True:
        try:
            st = os.lstat(current)
        except OSError:
            return False
        if st.st_uid != 0 or st.st_mode & 0o022:
            return False
        if current in ("/", ""):
            return True
        current = os.path.dirname(current)


def require_root_controlled_executable(path: str) -> None:
    """The WebUI executable (after symlinks) must be a root-controlled file."""
    real = os.path.realpath(path)
    if not os.path.isfile(real) or not os.access(real, os.X_OK):
        raise ConfineError(f"--webui {path!r} is not an executable file")
    if not _root_controlled(real):
        raise ConfineError(
            f"--webui {path!r} resolves to {real!r}, which is not root-owned and "
            "protected from group/world writes all the way up"
        )


def require_root_controlled_package(path: str) -> None:
    """The npm tree the WebUI runs from must be root-controlled throughout.

    Checking only the entry file's path would leave its dependencies (and the
    ``qwen`` CLI it spawns, installed in the same global ``node_modules``)
    writable by whoever owns them. When the resolved executable lives under a
    ``node_modules`` directory, the whole directory is walked (symlinks are
    not followed; their targets inside the tree are checked on their own).
    """
    real = os.path.realpath(path)
    root = real
    while os.path.basename(root) != "node_modules":
        parent = os.path.dirname(root)
        if parent == root:
            return  # not an npm install: the executable check covers it
        root = parent
    seen = 0

    def _walk_error(exc: OSError) -> None:
        raise ConfineError(f"cannot verify {exc.filename}: {exc.strerror}")

    for current, dirs, files in os.walk(root, followlinks=False, onerror=_walk_error):
        for name in dirs + files:
            seen += 1
            if seen > MAX_PACKAGE_ENTRIES:
                raise ConfineError(f"{root} is too large to verify")
            full = os.path.join(current, name)
            try:
                st = os.lstat(full)
            except OSError as exc:
                raise ConfineError(f"cannot verify {full}: {exc.strerror}") from None
            if stat.S_ISLNK(st.st_mode):
                # e.g. `npm link`: the target may live outside the tree. A
                # dangling link (a stale .bin entry) is acceptable when only
                # root could create its target.
                target = os.path.realpath(full)
                probe = target
                while not os.path.lexists(probe) and probe != "/":
                    probe = os.path.dirname(probe)
                if not _root_controlled(probe):
                    kind = "dangling symlink" if probe != target else "symlink"
                    raise ConfineError(f"{full} is a {kind} outside root-controlled files")
                continue
            if st.st_uid != 0 or st.st_mode & 0o022:
                raise ConfineError(
                    f"{os.path.join(current, name)} is not root-owned and protected "
                    "from group/world writes (the npm prefix must be root's)"
                )


def require_root_controlled_path(sandbox_path: str) -> None:
    """Every PATH entry the sandbox sees must be root-controlled.

    A ``#!/usr/bin/env node`` WebUI resolves ``node`` through this PATH, so a
    directory the service account can write would let it choose the code the
    account runs. A missing entry is checked through its nearest existing
    ancestor (only its owner could create it later).
    """
    for entry in sandbox_path.split(":"):
        probe = os.path.realpath(entry)
        while not os.path.lexists(probe) and probe != "/":
            probe = os.path.dirname(probe)
        if not _root_controlled(probe):
            raise ConfineError(f"PATH entry {entry!r} is not root-controlled")


def load_policy(path: str = CONFIG_PATH) -> Policy:
    """Read the root-owned policy file.

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
    if not isinstance(data, dict):
        raise ConfineError(f"policy file {path} must be a JSON object")

    def _abs_list(key: str, default: list[str]) -> frozenset[str]:
        value = data.get(key, default)
        if not isinstance(value, list) or not all(
            isinstance(item, str) and os.path.isabs(item) for item in value
        ):
            raise ConfineError(f"policy file {path}: {key!r} must be a list of absolute paths")
        return frozenset(os.path.normpath(item) for item in value)

    if "webui" not in data:
        raise ConfineError(f"policy file {path}: 'webui' is required")
    webuis = _abs_list("webui", [])
    bases = _abs_list("bases", [])
    denied = data.get("denied_groups", [])
    if not isinstance(denied, list) or not all(isinstance(item, str) for item in denied):
        raise ConfineError(f"policy file {path}: 'denied_groups' must be a list of names")
    sandbox_path = data.get("path", SAFE_PATH)
    if not isinstance(sandbox_path, str) or not all(
        os.path.isabs(part) for part in sandbox_path.split(":")
    ):
        raise ConfineError(f"policy file {path}: 'path' must be absolute directories joined by ':'")
    container = None
    raw_container = data.get("container")
    if raw_container is not None:
        if not isinstance(raw_container, dict):
            raise ConfineError(f"policy file {path}: 'container' must be an object")
        image = raw_container.get("image", "")
        # ``runtimes`` maps a confinement mode to its Docker runtime; the
        # older single ``runtime`` key is the gVisor one.
        runtimes = raw_container.get("runtimes", {})
        if not isinstance(runtimes, dict) or set(runtimes) - {"runsc", "kata"}:
            raise ConfineError(
                f"policy file {path}: container.runtimes may only map 'runsc' and 'kata'"
            )
        runtime = runtimes.get("runsc", raw_container.get("runtime", ""))
        kata_runtime = runtimes.get("kata", "")
        docker = raw_container.get("docker", "/usr/bin/docker")
        if not isinstance(image, str) or not IMAGE_RE.fullmatch(image):
            raise ConfineError(
                f"policy file {path}: container.image must be pinned by digest "
                "(name@sha256:<64 hex>) or be a local image id (sha256:<64 hex>)"
            )
        for what, value in (("runsc", runtime), ("kata", kata_runtime)):
            if not isinstance(value, str) or (value and not RUNTIME_RE.fullmatch(value)):
                raise ConfineError(f"policy file {path}: the {what} runtime is not a runtime name")
        if not runtime and not kata_runtime:
            raise ConfineError(f"policy file {path}: container names no runtime")
        if not isinstance(docker, str) or not os.path.isabs(docker):
            raise ConfineError(f"policy file {path}: container.docker must be an absolute path")
        container = ContainerPolicy(image, runtime, docker, kata_runtime)
    return Policy(webuis, sandbox_path, bases, PRIVILEGED_GROUPS | frozenset(denied), container)


def validate_log_dir(path: str, entry: pwd.struct_passwd) -> str:
    """The webui log dir: ``/tmp/qwen-code-webui-<n>``, a real dir owned by the account.

    It is bound into the sandbox; nothing on the host side opens a path inside
    it after the sandbox starts (the egress log is opened beforehand).
    """
    if not LOG_DIR_RE.fullmatch(path):
        raise ConfineError(f"log dir {path!r} is not /tmp/qwen-code-webui-<n>")
    try:
        st = os.lstat(path)
    except OSError as exc:
        raise ConfineError(f"log dir {path!r} is not accessible ({exc.strerror})") from None
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
        # READ-ONLY: the sandbox connects to the two host sockets but can
        # never replace them (connect() on a socket works on a read-only mount).
        "--ro-bind", socket_dir, INNER_SOCKET_DIR,
        "--unshare-user",
        # A nested user namespace could not undo the locked mounts, but it is
        # attack surface the WebUI never needs.
        "--disable-userns",
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


def _mount_safe(path: str) -> str:
    """A path usable in ``docker run --mount`` (no field separators)."""
    if MOUNT_UNSAFE_RE.search(path):
        raise ConfineError(f"path {path!r} cannot be bind-mounted safely")
    return path


def build_docker_argv(
    *,
    container: ContainerPolicy,
    uid: int,
    gid: int,
    extra_gids: Sequence[int],
    port: int,
    memory_max: str,
    cpu_quota: int,
    tasks_max: int,
    home: str,
    shared: str | None,
    log_dir: str,
    run_dir: str,
    script: str,
    webui_argv: Sequence[str],
    cidfile: str | None = None,
    kata: bool = False,
) -> list[str]:
    """The fixed ``docker run`` command line for the container backend.

    Every option is built here; nothing from the caller reaches docker except
    validated values. No environment is on the command line: ``inner`` reads
    it from stdin (``-i``), so ``docker inspect`` does not show it either.

    *kata* (#3438): the Kata runtime, whose guest cannot reach host UNIX
    sockets. No socket directory is mounted; ``inner`` speaks the stdio
    channel instead (see :class:`Mux`), and the task bound is the guest's own.
    """
    runtime = container.kata_runtime if kata else container.runtime
    if not runtime:
        raise ConfineError(f"the policy names no {'kata' if kata else 'runsc'} runtime")
    argv = [
        container.docker, "run", "--rm", "-i", "--init",
        "--name", f"openace-webui-{uid}-{port}",
        "--label", "org.openace.confine=1", "--label", f"org.openace.uid={uid}",
        *(["--cidfile", cidfile] if cidfile else []),
        "--runtime", runtime,
        "--network", "none",
        "--user", f"{uid}:{gid}",
    ]  # fmt: skip
    for extra in extra_gids:
        argv += ["--group-add", str(extra)]
    argv += [
        "--read-only", "--tmpfs", "/tmp:rw,nosuid,nodev,size=512m",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--memory", memory_max, "--memory-swap", memory_max,
        "--cpus", f"{cpu_quota / 100:.2f}",
        # Under gVisor, --pids-limit bounds the SENTRY's host threads (it
        # needs a few hundred); the container's own processes are bounded by
        # RLIMIT_NPROC, which the gVisor kernel enforces per uid. Under Kata
        # both apply inside the guest.
        "--pids-limit", str(tasks_max if kata else max(tasks_max, CONTAINER_MIN_HOST_PIDS)),
        "--ulimit", f"nproc={tasks_max}:{tasks_max}",
        "--mount", f"type=bind,src={_mount_safe(home)},dst={home}",
    ]  # fmt: skip
    if shared:
        argv += ["--mount", f"type=bind,src={_mount_safe(shared)},dst={shared}"]
    argv += ["--mount", f"type=bind,src={_mount_safe(log_dir)},dst={log_dir}"]
    if not kata:
        # READ-ONLY, as with bubblewrap: the container only connects out.
        argv += [
            "--mount", f"type=bind,src={_mount_safe(run_dir)},dst={INNER_SOCKET_DIR},readonly",
        ]  # fmt: skip
    argv += [
        "--mount", f"type=bind,src={_mount_safe(script)},dst={CONTAINER_SCRIPT},readonly",
        "--workdir", home,
        "--entrypoint", "python3",
        container.image,
        "-I", CONTAINER_SCRIPT, "inner", "--env-stdin", "--watch-supervisor",
        *(["--transport", "stdio"] if kata else []),
        "--port", str(port), "--", *webui_argv,
    ]  # fmt: skip
    return argv


# ── Byte pumps (supervise + inner) ──────────────────────────────────────────


class _Activity:
    """Last time either direction of a splice moved a byte."""

    def __init__(self) -> None:
        self.last = time.monotonic()

    def touch(self) -> None:
        self.last = time.monotonic()


def _pump(
    src: socket.socket,
    dst: socket.socket,
    activity: _Activity | None = None,
    idle: float | None = None,
) -> bool:
    """Copy src -> dst. With *idle*, give up once BOTH directions were silent
    that long (one quiet direction alone is normal: an SSE stream)."""
    if idle is not None:
        # (also bounds sendall on this socket: a peer that stops reading for
        # the whole idle window is closed too)
        src.settimeout(idle)
    timed_out = False
    try:
        while True:
            try:
                chunk = src.recv(PIPE_BUFFER)
            except TimeoutError:
                if activity is not None and idle is not None:
                    if time.monotonic() - activity.last < idle:
                        continue
                timed_out = True
                break
            if not chunk:
                break
            if activity is not None:
                activity.touch()
            dst.sendall(chunk)
    except OSError:
        pass
    finally:
        with contextlib.suppress(OSError):
            dst.shutdown(socket.SHUT_WR)
    return timed_out


def splice(
    a: socket.socket, b: socket.socket, initial_to_b: bytes = b"", idle: float | None = None
) -> None:
    """Copy both directions until both sides close; closes both sockets.

    *idle*: close once neither direction moved a byte for that many seconds.
    """
    activity = _Activity()
    try:
        if initial_to_b:
            b.sendall(initial_to_b)
        worker = threading.Thread(target=_pump, args=(b, a, activity, idle), daemon=True)
        worker.start()
        if _pump(a, b, activity, idle):
            # Idle timeout: the other direction may still be blocked in recv —
            # wake it. A normal EOF (half-close) must NOT do this: the peer
            # may still be sending its response.
            for sock in (a, b):
                with contextlib.suppress(OSError):
                    sock.shutdown(socket.SHUT_RDWR)
        worker.join()
    except (OSError, RuntimeError):
        pass
    finally:
        for sock in (a, b):
            with contextlib.suppress(OSError):
                sock.close()


def _serve(
    listener: socket.socket,
    handler,
    name: str,
    limit: int = MAX_INGRESS_CLIENTS,
    per_source: int | None = None,
) -> None:
    """Accept forever; at most *limit* handlers at once (threads count against
    the scope's TasksMax, shared with the WebUI), and at most *per_source*
    from one peer address, so a single remote host cannot hold every slot.
    Over a limit, or when a thread cannot be started, the connection is
    closed — the loop survives."""
    slots = threading.BoundedSemaphore(limit)
    by_source: dict[str, int] = {}
    lock = threading.Lock()

    def _release(source: str) -> None:
        with lock:
            by_source[source] -= 1
            if not by_source[source]:
                del by_source[source]
        slots.release()

    def _run(conn: socket.socket, source: str) -> None:
        try:
            handler(conn)
        finally:
            _release(source)

    while True:
        try:
            conn, peer = listener.accept()
        except OSError:
            return
        source = peer[0] if isinstance(peer, tuple) and peer else ""
        with lock:
            over_source = per_source is not None and by_source.get(source, 0) >= per_source
        if over_source or not slots.acquire(blocking=False):
            with contextlib.suppress(OSError):
                conn.close()
            continue
        with lock:
            by_source[source] = by_source.get(source, 0) + 1
        try:
            threading.Thread(target=_run, args=(conn, source), daemon=True, name=name).start()
        except RuntimeError:
            _release(source)
            with contextlib.suppress(OSError):
                conn.close()
            time.sleep(0.1)


def _unix_listener(path: str) -> socket.socket:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    old = os.umask(0o077)
    try:
        sock.bind(path)
    finally:
        os.umask(old)
    sock.listen(64)
    return sock


# ── Reverse tunnel (ingress) ────────────────────────────────────────────────


class TunnelPool:
    """Supervisor side: idle tunnel connections opened by ``inner``.

    A browser connection is paired with one idle tunnel; the supervisor sends
    ``TUNNEL_GO`` and ``inner`` connects that tunnel to the WebUI. The host
    therefore never connects to anything inside the sandbox.
    """

    def __init__(self, max_idle: int = TUNNEL_POOL_MAX) -> None:
        self._idle: queue.Queue[socket.socket] = queue.Queue(maxsize=max_idle)

    def add(self, conn: socket.socket) -> None:
        try:
            self._idle.put_nowait(conn)
        except queue.Full:
            conn.close()  # the sandbox may not grow the supervisor's state

    def take(self, timeout: float | None = None) -> socket.socket | None:
        wait = TUNNEL_WAIT_SECONDS if timeout is None else timeout
        deadline = time.monotonic() + wait
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                conn = self._idle.get(timeout=remaining)
            except queue.Empty:
                return None
            try:
                conn.sendall(TUNNEL_GO)
                return conn
            except OSError:
                conn.close()  # a tunnel whose inner end died; try the next

    def serve_client(self, client: socket.socket) -> None:
        tunnel = self.take()
        if tunnel is None:
            with contextlib.suppress(OSError):
                client.close()
            return
        splice(client, tunnel, idle=INGRESS_IDLE_SECONDS)


class _TunnelHealth:
    """``inner`` side: whether the supervisor is still there (watchdog input)."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.idle = 0
        self.last_ok = time.monotonic()

    def connected(self) -> None:
        with self.lock:
            self.idle += 1
            self.last_ok = time.monotonic()

    def released(self) -> None:
        with self.lock:
            self.idle -= 1

    def supervisor_gone(self, grace: float) -> bool:
        with self.lock:
            return self.idle <= 0 and time.monotonic() - self.last_ok > grace


TUNNEL_HEALTH = _TunnelHealth()


def _tunnel_worker(tunnel_path: str, port: int, replenish: threading.Semaphore) -> None:
    """``inner`` side: one idle tunnel; on GO, connect it to the WebUI."""
    while True:
        conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            conn.connect(tunnel_path)
        except OSError:
            conn.close()
            time.sleep(0.5)
            continue
        TUNNEL_HEALTH.connected()
        try:
            go = conn.recv(1)
        except OSError:
            go = b""
        TUNNEL_HEALTH.released()
        if go != TUNNEL_GO:
            conn.close()
            time.sleep(0.2)
            continue
        replenish.release()  # a fresh idle tunnel replaces this one
        try:
            upstream = socket.create_connection(("127.0.0.1", port), timeout=10)
            upstream.settimeout(None)
        except OSError:
            conn.close()
            return
        splice(conn, upstream, idle=INGRESS_IDLE_SECONDS)
        return


def _run_tunnel_pool(tunnel_path: str, port: int, size: int = TUNNEL_POOL) -> None:
    replenish = threading.Semaphore(size)
    while True:
        replenish.acquire()
        try:
            threading.Thread(
                target=_tunnel_worker, args=(tunnel_path, port, replenish), daemon=True
            ).start()
        except RuntimeError:
            # TasksMax pressure: give the slot back and retry — never let the
            # replenisher die (ingress would stay dead until the next launch).
            replenish.release()
            time.sleep(1.0)


# ── Egress proxy (supervise) ────────────────────────────────────────────────


def _read_head(conn: socket.socket) -> tuple[bytes, bytes]:
    conn.settimeout(HEAD_TIMEOUT_SECONDS)
    buf = b""
    while b"\r\n\r\n" not in buf:
        if len(buf) > MAX_HEAD_BYTES:
            raise ValueError("request head too large")
        chunk = conn.recv(PIPE_BUFFER)
        if not chunk:
            raise ValueError("connection closed before the request head")
        buf += chunk
    conn.settimeout(None)
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
    if port_s and not DIGITS_RE.fullmatch(port_s):
        raise ValueError(f"bad port in {authority!r}")
    port = int(port_s) if port_s else default_port
    if not host or not 0 < port < 65536:
        raise ValueError(f"bad authority {authority!r}")
    return host, port


class EgressProxy:
    """HTTP proxy on a Unix socket that only reaches allowlisted host:port pairs.

    ``log_fd`` is an already-open append-only descriptor (opened before the
    sandbox starts), never a path: the log directory is writable from inside.
    """

    def __init__(
        self,
        allow: Sequence[tuple[str, int]],
        log_fd: int | None = None,
        max_bytes: int = LOG_MAX_BYTES,
        lines_per_second: int = LOG_LINES_PER_SECOND,
    ) -> None:
        self.allow = tuple(allow)
        self.log_fd = log_fd
        self._log_lock = threading.Lock()
        self._max_bytes = max_bytes  # DENY/BAD lines
        # ALLOW/FAIL: far above normal use; past it only per-entry counters
        self._allow_max_bytes = max(max_bytes * 8, 1)
        self._allow_written = 0
        self._allow_summary_at = 0
        self._deny_summary_at = 0
        self._rate = lines_per_second  # per verdict class, per second
        self._deny_written = 0
        self._deny_capped = False
        self._window = int(time.monotonic())
        self._window_stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
        self._allow_lines = 0
        self._deny_lines = 0
        self._allow_overflow: dict[tuple[str, str, str], int] = {}
        self._deny_overflow = 0

    @staticmethod
    def _field(value: object) -> str:
        # repr(): no CR/LF can forge a line; cut: no 64 KB hosts
        return repr(str(value)[:LOG_FIELD_MAX])

    def _write(self, text: str) -> None:
        with contextlib.suppress(OSError):
            os.write(self.log_fd, (text + "\n").encode("utf-8", "backslashreplace"))  # type: ignore[arg-type]

    def _roll(self, now: int, force: bool = False) -> None:
        """Close the previous second-window. Caller holds the lock.

        Summaries are stamped with the window they describe, so they stay in
        order. They count toward their class's byte ceiling; once a class is
        past it, its counters keep accumulating and are written at most once
        a minute (and at shutdown), so even a lifetime-long flood grows the
        log by a few lines per minute per allowlist entry.
        """
        if now == self._window and not force:
            return
        stamp = self._window_stamp
        allow_due = (
            force
            or self._allow_written < self._allow_max_bytes
            or (now - self._allow_summary_at >= SUMMARY_SLOW_SECONDS)
        )
        deny_due = (
            force
            or self._deny_written < self._max_bytes
            or (now - self._deny_summary_at >= SUMMARY_SLOW_SECONDS)
        )
        if allow_due and self._allow_overflow:
            for (verdict, entry, _), count in sorted(self._allow_overflow.items()):
                text = f"{stamp} {verdict}-SUMMARY {entry} x{count}"
                self._allow_written += len(text) + 1
                self._write(text)
            self._allow_overflow = {}
            self._allow_summary_at = now
        if deny_due and self._deny_overflow:
            text = f"{stamp} # {self._deny_overflow} DENY/BAD line(s) not logged (rate limit)"
            self._deny_written += len(text) + 1
            self._write(text)
            self._deny_overflow = 0
            self._deny_summary_at = now
        self._window = now
        self._window_stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
        self._allow_lines = self._deny_lines = 0

    def flush(self, final: bool = False) -> None:
        """Write the summaries of finished windows (every second from the
        supervisor's flusher; ``final`` at shutdown writes everything)."""
        if self.log_fd is None:
            return
        with self._log_lock:
            self._roll(int(time.monotonic()), force=final)

    def log(
        self,
        verdict: str,
        method: str,
        host: str,
        port: int | str,
        extra: str = "",
        entry: str | None = None,
    ) -> None:
        """Append one bounded decision line.

        ALLOW/FAIL decisions are never dropped — they are the record of what
        was actually reached: beyond the per-second budget (or past their own,
        much larger byte ceiling) they are counted per matched ALLOWLIST ENTRY
        (a finite set: host spellings and ``*.domain`` subdomains all fold
        into the entry they matched).
        DENY/BAD lines have their own budget and a per-launch byte cap, so a
        flood of denied requests can neither hide allowed traffic nor fill
        the host filesystem.
        """
        if self.log_fd is None:
            return
        line = f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {verdict} {self._field(method)} {self._field(host)}:{port}"
        if extra:
            line += f" {self._field(extra)}"
        with self._log_lock:
            self._roll(int(time.monotonic()))
            if verdict in ("ALLOW", "FAIL"):
                self._allow_lines += 1
                data_len = len(line) + 1
                if (
                    self._allow_lines <= self._rate
                    and self._allow_written + data_len <= self._allow_max_bytes
                ):
                    self._allow_written += data_len
                    self._write(line)
                else:
                    key = (verdict, entry or f"{host}:{port}", "")
                    self._allow_overflow[key] = self._allow_overflow.get(key, 0) + 1
                return
            if self._deny_capped:
                self._deny_overflow += 1
                return
            self._deny_lines += 1
            if self._deny_lines > self._rate:
                self._deny_overflow += 1
                return
            data = line + "\n"
            if self._deny_written + len(data) > self._max_bytes:
                self._deny_capped = True
                self._write("# DENY/BAD size cap reached; further denials are not logged "
                            "(ALLOW decisions still are)")  # fmt: skip
                return
            self._deny_written += len(data)
            self._write(line)

    def handle(self, conn: socket.socket) -> None:
        method = "?"
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
            if not METHOD_RE.fullmatch(method):
                raise ValueError("method is not a short upper-case token")
            normalized = normalize_host(host)
            if normalized is None:
                raise ValueError("host is not a host name or IP address")
            entry = match_allow(normalized, port, self.allow)
            if entry is None:
                self.log("DENY", method, normalized, port)
                self._reply(conn, 403, f"egress to {normalized}:{port} is not in the allowlist")
                return
            try:
                upstream = socket.create_connection(
                    (normalized, port), timeout=CONNECT_TIMEOUT_SECONDS
                )
                upstream.settimeout(None)
            except OSError as exc:
                self.log("FAIL", method, normalized, port, type(exc).__name__, entry=entry)
                self._reply(conn, 502, f"cannot reach {normalized}:{port}")
                return
            self.log("ALLOW", method, normalized, port, entry=entry)
            if method.upper() == "CONNECT":
                conn.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                if rest:
                    upstream.sendall(rest)
            splice(conn, upstream, forward)
        except (ValueError, OSError) as exc:
            self.log("BAD", method, "-", "-", str(exc))
            with contextlib.suppress(OSError):
                self._reply(conn, 400, "malformed proxy request")
        finally:
            with contextlib.suppress(OSError):
                conn.close()

    @staticmethod
    def _reply(conn: socket.socket, status: int, message: str) -> None:
        reason = {400: "Bad Request", 403: "Forbidden", 502: "Bad Gateway"}[status]
        body = f"openace-webui-confine: {message}\n".encode("utf-8", "backslashreplace")
        conn.sendall(
            f"HTTP/1.1 {status} {reason}\r\nContent-Type: text/plain\r\n"
            f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body
        )


# ── Stdio multiplexer (Kata backend) ────────────────────────────────────────
#
# Under Kata the container is a virtual machine: bind mounts reach it through
# virtio-fs, where connect() on a host UNIX socket file never reaches the
# host listener. The Kata backend therefore keeps ``--network none`` and
# carries every ingress and egress stream as frames over the container's
# attached stdin/stdout — no bridge, no firewall rules, nothing on the host a
# guest could connect to. Each stream has its own credit window, so a slow
# reader stalls only its own stream, never the pipe.
#
# Frame: type (1 byte), stream id (4 bytes), payload length (2 bytes), payload.
# The host opens INGRESS streams (odd ids), the container EGRESS ones (even).

MUX_HEADER = struct.Struct(">BIH")
MUX_OPEN, MUX_DATA, MUX_CLOSE, MUX_RESET, MUX_CREDIT, MUX_PING = range(1, 7)
_MUX_TYPES = frozenset(range(1, 7))
MUX_KIND_INGRESS = b"I"
MUX_KIND_EGRESS = b"E"
MUX_CHUNK = 32 * 1024  # largest DATA payload
MUX_WINDOW = 256 * 1024  # bytes in flight per stream and direction
MUX_MAX_STREAMS = 256  # concurrent streams opened by the peer
MUX_PING_SECONDS = 5.0
MUX_DEAD_SECONDS = 30.0  # no frame for this long: the other side is gone
_MUX_EOF = b""  # inbox marker: the peer half-closed
_MUX_ABORT = None  # inbox marker: the stream was reset


class MuxError(Exception):
    """The peer broke the framing protocol: the whole channel is dropped."""


class _MuxStream:
    def __init__(self, sid: int, local: socket.socket) -> None:
        self.sid = sid
        self.local = local  # our end of the stream (a socketpair end or a client)
        self.credit = MUX_WINDOW  # bytes we may still send
        self.buffered = 0  # bytes received and not yet delivered to ``local``
        self.inbox: queue.Queue[bytes | None] = queue.Queue()
        self.cond = threading.Condition()
        self.reset = False
        self.peer_closed = False  # the peer half-closed: no DATA may follow


class Mux:
    """One side of the stdio channel.

    *accept* maps a stream kind the PEER may open to a handler that is given
    a connected socket (a socketpair end) in its own thread. The peer can
    never open any other kind, exceed a stream's window, or hold more than
    *max_streams* streams: a framing violation ends the channel, which ends
    the launch (fail closed).
    """

    def __init__(
        self,
        rfd: int,
        wfd: int,
        *,
        initiator: bool,
        accept: dict[bytes, Callable[[socket.socket], None]],
        max_streams: int = MUX_MAX_STREAMS,
    ) -> None:
        self._reader = os.fdopen(rfd, "rb", buffering=PIPE_BUFFER)
        self._wfd = wfd
        self._wlock = threading.Lock()
        self._lock = threading.Lock()
        self._streams: dict[int, _MuxStream] = {}
        self._next_id = 1 if initiator else 2
        self._peer_parity = 0 if initiator else 1
        self._accept = accept
        self._max_streams = max_streams
        self.closed = threading.Event()
        self.last_frame = time.monotonic()

    # -- writing ----------------------------------------------------------

    def _send(self, ftype: int, sid: int, payload: bytes = b"") -> None:
        data = MUX_HEADER.pack(ftype, sid, len(payload)) + payload
        with self._wlock:
            if self.closed.is_set():
                raise OSError("mux closed")
            view = memoryview(data)
            while view:
                try:
                    written = os.write(self._wfd, view)
                except OSError:
                    self.close()
                    raise
                view = view[written:]

    def ping(self) -> None:
        with contextlib.suppress(OSError):
            self._send(MUX_PING, 0)

    # -- streams ----------------------------------------------------------

    def open(self, kind: bytes, local: socket.socket) -> None:
        """Open a stream of *kind* to the peer, bridged to *local*; blocks
        until the stream is over, then closes *local*."""
        with self._lock:
            ours_open = sum(1 for s in self._streams if s % 2 != self._peer_parity)
            if ours_open >= self._max_streams or self.closed.is_set():
                with contextlib.suppress(OSError):
                    local.close()
                return
            sid = self._next_id
            self._next_id += 2
            stream = _MuxStream(sid, local)
            self._streams[sid] = stream
        try:
            self._send(MUX_OPEN, sid, kind)
        except OSError:
            self._forget(stream)
            return
        self._run_stream(stream)

    def _run_stream(self, stream: _MuxStream) -> None:
        deliver = threading.Thread(target=self._deliver, args=(stream,), daemon=True)
        try:
            deliver.start()
        except RuntimeError:
            self._abort(stream)
            self._forget(stream)
            return
        self._pump_out(stream)
        deliver.join()
        self._forget(stream)

    def _pump_out(self, stream: _MuxStream) -> None:
        """local -> peer, within the credit the peer granted."""
        try:
            while True:
                with stream.cond:
                    while stream.credit <= 0 and not stream.reset and not self.closed.is_set():
                        stream.cond.wait(1.0)
                    if stream.reset or self.closed.is_set():
                        return
                    budget = min(MUX_CHUNK, stream.credit)
                chunk = stream.local.recv(budget)
                if stream.reset:
                    return
                if not chunk:
                    self._send(MUX_CLOSE, stream.sid)
                    return
                with stream.cond:
                    stream.credit -= len(chunk)
                self._send(MUX_DATA, stream.sid, chunk)
        except OSError:
            self._abort(stream)

    def _deliver(self, stream: _MuxStream) -> None:
        """peer -> local; each delivered chunk is credited back."""
        try:
            while True:
                item = stream.inbox.get()
                if item is _MUX_ABORT:
                    return
                if item == _MUX_EOF:
                    with contextlib.suppress(OSError):
                        stream.local.shutdown(socket.SHUT_WR)
                    return
                stream.local.sendall(item)
                with stream.cond:
                    stream.buffered -= len(item)
                self._send(MUX_CREDIT, stream.sid, struct.pack(">I", len(item)))
        except OSError:
            self._abort(stream)

    def _abort(self, stream: _MuxStream) -> None:
        with stream.cond:
            first = not stream.reset
            stream.reset = True
            stream.cond.notify_all()
        if first:
            stream.inbox.put(_MUX_ABORT)
            with contextlib.suppress(OSError):
                stream.local.shutdown(socket.SHUT_RDWR)  # wakes a blocked recv
            with contextlib.suppress(OSError):
                self._send(MUX_RESET, stream.sid)

    def _forget(self, stream: _MuxStream) -> None:
        with self._lock:
            self._streams.pop(stream.sid, None)
        with contextlib.suppress(OSError):
            stream.local.close()

    def _accept_stream(self, sid: int, kind: bytes) -> None:
        handler = self._accept.get(kind)
        with self._lock:
            peer_open = sum(1 for s in self._streams if s % 2 == self._peer_parity)
            if sid in self._streams:
                raise MuxError(f"stream {sid} opened twice")
            refused = handler is None or peer_open >= self._max_streams
            if not refused:
                ours, theirs = socket.socketpair()
                stream = _MuxStream(sid, ours)
                self._streams[sid] = stream
        if refused:
            with contextlib.suppress(OSError):
                self._send(MUX_RESET, sid)
            return

        def _handle() -> None:
            try:
                handler(theirs)  # type: ignore[misc]
            finally:
                with contextlib.suppress(OSError):
                    theirs.close()

        try:
            threading.Thread(target=_handle, daemon=True, name="mux-accept").start()
            threading.Thread(
                target=self._run_stream, args=(stream,), daemon=True, name="mux-stream"
            ).start()
        except RuntimeError:
            self._abort(stream)
            self._forget(stream)
            with contextlib.suppress(OSError):
                theirs.close()

    # -- reading ----------------------------------------------------------

    def _read_exact(self, size: int) -> bytes:
        data = self._reader.read(size) if size else b""
        if len(data) != size:
            raise EOFError
        return data

    def run(self) -> None:
        """Read frames until EOF or a protocol violation; then close."""
        try:
            while True:
                ftype, sid, length = MUX_HEADER.unpack(self._read_exact(MUX_HEADER.size))
                if length > MUX_CHUNK:
                    raise MuxError("frame too large")
                payload = self._read_exact(length)
                self.last_frame = time.monotonic()
                if ftype not in _MUX_TYPES:
                    raise MuxError(f"unknown frame type {ftype}")
                if ftype == MUX_PING:
                    continue
                if ftype == MUX_OPEN:
                    if sid % 2 != self._peer_parity or sid == 0:
                        raise MuxError(f"stream id {sid} is not the peer's to open")
                    self._accept_stream(sid, payload)
                    continue
                with self._lock:
                    stream = self._streams.get(sid)
                if stream is None:
                    continue  # a stream that already ended; late frames are harmless
                if ftype == MUX_DATA:
                    if stream.peer_closed:
                        raise MuxError(f"stream {sid} sent data after closing")
                    if not length:
                        continue  # an empty chunk carries nothing (and is not EOF)
                    with stream.cond:
                        stream.buffered += length
                        if stream.buffered > MUX_WINDOW:
                            raise MuxError(f"stream {sid} overran its window")
                    if not stream.reset:
                        stream.inbox.put(payload)
                elif ftype == MUX_CLOSE:
                    if not stream.peer_closed:
                        stream.peer_closed = True
                        stream.inbox.put(_MUX_EOF)
                elif ftype == MUX_RESET:
                    with stream.cond:
                        stream.reset = True
                        stream.cond.notify_all()
                    stream.inbox.put(_MUX_ABORT)
                    with contextlib.suppress(OSError):
                        stream.local.shutdown(socket.SHUT_RDWR)
                elif ftype == MUX_CREDIT:
                    if length != 4:
                        raise MuxError("bad credit frame")
                    (grant,) = struct.unpack(">I", payload)
                    with stream.cond:
                        stream.credit += grant
                        if stream.credit > MUX_WINDOW:
                            raise MuxError(f"stream {sid} was credited beyond its window")
                        stream.cond.notify_all()
        except (EOFError, OSError, ValueError, MuxError, struct.error) as exc:
            if isinstance(exc, MuxError):
                print(f"openace-webui-confine: channel closed: {exc}", file=sys.stderr)
        finally:
            self.close()

    def close(self) -> None:
        if self.closed.is_set():
            return
        self.closed.set()
        with self._lock:
            streams = list(self._streams.values())
        for stream in streams:
            with stream.cond:
                stream.reset = True
                stream.cond.notify_all()
            stream.inbox.put(_MUX_ABORT)
            with contextlib.suppress(OSError):
                stream.local.shutdown(socket.SHUT_RDWR)
        # The write end stays open (a writer may be blocked on it); the
        # process exits right after the channel closes.


# ── Mode: launch (root) ─────────────────────────────────────────────────────


def _launch_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="openace-webui-confine launch", add_help=False, allow_abbrev=False
    )
    parser.add_argument("--account", required=True)
    parser.add_argument("--port", required=True)
    parser.add_argument("--bind-host", default="0.0.0.0")  # noqa: S104 - today's exposure
    parser.add_argument("--memory-max", required=True)
    parser.add_argument("--cpu-quota", required=True)
    parser.add_argument("--tasks-max", required=True)
    parser.add_argument("--allow", action="append", default=[])
    parser.add_argument("--log-dir", required=True)
    parser.add_argument("--webui", required=True)
    # container = Docker on gVisor (#3431 Option 2); kata = Docker on Kata (#3438)
    parser.add_argument("--backend", choices=("bwrap", "container", "kata"), default="bwrap")
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
    policy = load_policy(policy_path)
    entry = resolve_account(args.account, policy.denied_groups)
    port = _parse_port(args.port, "--port")
    if port < 1024:
        raise ConfineError("--port must be >= 1024")
    _require_ip(args.bind_host, "--bind-host")
    if not MEMORY_RE.fullmatch(args.memory_max):
        raise ConfineError("--memory-max must look like 4G / 512M / bytes")
    cpu = _digits(args.cpu_quota, "--cpu-quota")
    if not 1 <= cpu <= 6400:
        raise ConfineError("--cpu-quota must be an integer percentage 1..6400")
    tasks = _digits(args.tasks_max, "--tasks-max")
    if not 16 <= tasks <= 65535:
        raise ConfineError("--tasks-max must be 16..65535")
    allow = [parse_allow_entry(item) for item in args.allow]
    if not allow:
        raise ConfineError("at least one --allow host:port is required (the LLM proxy)")
    webui = args.webui
    if os.path.normpath(webui) not in policy.webuis:
        raise ConfineError(f"--webui {webui!r} is not listed in {policy_path}")
    log_dir = validate_log_dir(args.log_dir, entry)
    base, home, shared = workspace_layout(entry, policy.bases)
    try:
        env = validate_env(json.loads(env_text or "{}"))
    except json.JSONDecodeError as exc:
        raise ConfineError(f"environment on stdin is not JSON: {exc}") from None
    if args.backend in ("container", "kata"):
        return _plan_container(
            args, policy, entry, port, cpu, tasks, allow, webui, webui_args,
            log_dir, base, home, shared, env,
        )  # fmt: skip
    # bwrap: the WebUI and everything it runs come from the HOST filesystem.
    require_root_controlled_executable(webui)
    require_root_controlled_package(webui)
    require_root_controlled_path(policy.path)
    for tool in ("systemd-run", "setpriv", "bwrap"):
        if shutil.which(tool, path=SAFE_PATH) is None:
            raise ConfineError(f"{tool} is not installed")
    python = sys.executable or "/usr/bin/python3"
    script = os.path.realpath(__file__)
    unit = f"openace-webui-{entry.pw_uid}-{port}"
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
        "backend": "bwrap",
        "account": entry.pw_name,
        "uid": entry.pw_uid,
        "tasks_max": tasks,
        "port": port,
        "bind_host": args.bind_host,
        "allow": [f"[{h}]:{p}" if ":" in h else f"{h}:{p}" for h, p in allow],
        "log_dir": log_dir,
        "base": base,
        "home": home,
        "shared": shared,
        "webui_argv": [webui, *webui_args],
        "env": env,
        "path": policy.path,
        "bwrap": shutil.which("bwrap", path=SAFE_PATH),
        "python": python,
        "script": script,
    }
    return systemd_argv, payload


def _setpriv_argv(entry: pwd.struct_passwd, python: str, script: str) -> list[str]:
    return [
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


def _plan_container(
    args, policy, entry, port, cpu, tasks, allow, webui, webui_args,
    log_dir, base, home, shared, env,
) -> tuple[list[str], dict[str, object]]:  # fmt: skip
    """The container-backend half of :func:`plan_launch`.

    The WebUI runs from the digest-pinned image, so the host-side ownership
    checks of the bwrap backend do not apply; the image pin is the trust
    anchor. Supplementary groups are passed as ``--group-add`` (privileged
    ones were already refused) so shared-project ACLs keep working.
    """
    if policy.container is None:
        raise ConfineError("the policy file has no 'container' section")
    kata = args.backend == "kata"
    require_root_controlled_executable(policy.container.docker)
    if kata and not os.path.exists(KVM_DEVICE):
        raise ConfineError(f"the kata backend needs {KVM_DEVICE}")
    if not symlinks_protected():
        raise ConfineError("fs.protected_symlinks must be 1 for the container backend")
    if shutil.which("setpriv", path=SAFE_PATH) is None:
        raise ConfineError("setpriv is not installed")
    python = sys.executable or "/usr/bin/python3"
    script = os.path.realpath(__file__)
    # One run directory per launch (the launcher's pid): a successor launch
    # on the same port never shares paths with a launch still shutting down.
    launch_id = f"{entry.pw_uid}-{port}-{os.getpid()}"
    run_dir = os.path.join(RUN_ROOT, launch_id)
    cidfile = os.path.join(RUN_ROOT, f"{launch_id}.cid")  # root-owned directory
    extra_gids = sorted(gid for gid in account_groups(entry) if gid != entry.pw_gid)
    docker_argv = build_docker_argv(
        container=policy.container,
        uid=entry.pw_uid,
        gid=entry.pw_gid,
        extra_gids=extra_gids,
        port=port,
        memory_max=args.memory_max,
        cpu_quota=cpu,
        tasks_max=tasks,
        home=home,
        shared=shared,
        log_dir=log_dir,
        run_dir=run_dir,
        cidfile=cidfile,
        script=script,
        webui_argv=[webui, *webui_args],
        kata=kata,
    )
    payload: dict[str, object] = {
        "backend": "container",
        # Kata's guest cannot reach host UNIX sockets: the stdio channel.
        "transport": "stdio" if kata else "uds",
        "account": entry.pw_name,
        "uid": entry.pw_uid,
        "gid": entry.pw_gid,
        "tasks_max": tasks,
        "port": port,
        "bind_host": args.bind_host,
        "allow": [f"[{h}]:{p}" if ":" in h else f"{h}:{p}" for h, p in allow],
        "socket_dir": run_dir,
        "cidfile": cidfile,
        "setpriv_argv": _setpriv_argv(entry, python, script),
        # what ``inner`` receives on the container's stdin
        "inner_env": build_inner_env(env, home=home, sandbox_path=policy.path),
    }
    return docker_argv, payload


def prepare_run_dir(path: str, uid: int, gid: int, run_root: str = RUN_ROOT) -> None:
    """Create a fresh per-instance socket directory under the root-owned run root.

    Root-owned parent: the account owns only the leaf, so it can never swap
    the bind-mount source docker resolves. A leftover leaf is removed without
    following symlinks first.
    """
    me = os.geteuid()
    try:
        os.mkdir(run_root, 0o755)
    except FileExistsError:
        pass
    st = os.lstat(run_root)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != me or st.st_mode & 0o022:
        raise ConfineError(f"{run_root} is not a directory controlled by uid {me}")
    if os.path.lexists(path):
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path)
        else:
            os.unlink(path)
    os.mkdir(path, 0o700)
    os.chown(path, uid, gid)


def open_root_egress_log(uid: int, log_root: str = EGRESS_LOG_ROOT) -> int:
    """Open ``<log_root>/<uid>.egress.log`` for appending, as root.

    The directory and file are owned by the caller (root in production) and
    closed to others; the descriptor is inherited down the exec chain, so the
    account appends through it but can never open, replace or truncate the
    file. O_NOFOLLOW + O_NONBLOCK + fstat refuse a symlink, a FIFO (which
    would block the open) or a hard-linked file.
    """
    me = os.geteuid()
    try:
        os.mkdir(log_root, 0o755)
    except FileExistsError:
        pass
    st = os.lstat(log_root)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != me or st.st_mode & 0o022:
        raise ConfineError(f"{log_root} is not a directory controlled by uid {me}")
    path = os.path.join(log_root, f"{uid}.egress.log")
    with contextlib.suppress(OSError):
        old = os.lstat(path)
        if stat.S_ISREG(old.st_mode) and old.st_size > LOG_ROTATE_BYTES:
            os.replace(path, path + ".1")  # keep one previous generation
    try:
        fd = os.open(
            path,
            os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
            0o600,
        )
    except OSError as exc:
        raise ConfineError(f"cannot open the egress log ({exc.strerror})") from None
    fst = os.fstat(fd)
    if not stat.S_ISREG(fst.st_mode) or fst.st_nlink != 1 or fst.st_uid != me:
        os.close(fd)
        raise ConfineError("the egress log is not a private regular file")
    os.set_blocking(fd, True)
    os.set_inheritable(fd, True)
    return fd


def _exec_with_stdin(argv: list[str], data: bytes) -> None:
    """execve *argv* with *data* waiting on its stdin (a pipe, never a file)."""
    if len(data) > PIPE_BUFFER - 1:
        raise ConfineError("environment too large for the hand-off pipe")
    read_fd, write_fd = os.pipe()
    os.write(write_fd, data)
    os.close(write_fd)
    os.dup2(read_fd, 0)
    os.close(read_fd)
    os.execve(argv[0], argv, {"PATH": SAFE_PATH, "LANG": "C.UTF-8"})


def _docker_env() -> dict[str, str]:
    return {"PATH": SAFE_PATH, "LANG": "C.UTF-8"}


def _docker_remove(docker: str, name: str, wait: float = CONTAINER_REMOVE_WAIT_SECONDS) -> None:
    """Force-remove our container *name* (only if it carries our label) and
    wait until the name is free, so a relaunch cannot race a removal still
    in progress (docker run would fail with a name conflict)."""
    env = _docker_env()
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        label = subprocess.run(  # noqa: S603 - fixed argv
            [docker, "inspect", "-f", '{{index .Config.Labels "org.openace.confine"}}', name],
            capture_output=True, text=True, timeout=30, check=False, env=env,
        )  # fmt: skip
        if label.returncode != 0 or label.stdout.strip() != "1":
            return
        subprocess.run(  # noqa: S603 - fixed argv
            [docker, "rm", "-f", name], capture_output=True, timeout=60, check=False, env=env
        )
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            gone = subprocess.run(  # noqa: S603 - fixed argv
                [docker, "inspect", "-f", "{{.Id}}", name],
                capture_output=True, timeout=30, check=False, env=env,
            )  # fmt: skip
            if gone.returncode != 0:
                return
            time.sleep(0.5)


def _docker_remove_id(docker: str, cidfile: str) -> bool:
    """Force-remove THIS launch's container by the id docker wrote to *cidfile*
    (never by name: a successor launch may already hold the name)."""
    try:
        with open(cidfile, encoding="ascii") as handle:
            cid = handle.read().strip()
    except (OSError, ValueError):  # ValueError: undecodable contents
        return False
    if not re.fullmatch(r"[0-9a-f]{64}", cid):
        return False
    try:
        result = subprocess.run(  # noqa: S603 - fixed argv
            [docker, "rm", "-f", cid], capture_output=True, text=True, timeout=60,
            check=False, env=_docker_env(),
        )  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        return False
    # Only a confirmed removal (or a container already gone) ends the retries.
    return result.returncode == 0 or "no such container" in (result.stderr or "").lower()


def _reap_supervisor(pid: int, grace: float = 10.0) -> None:
    """SIGTERM the supervisor, wait up to *grace* seconds, then SIGKILL.

    Stops at once when the child is already reaped (ChildProcessError): a
    SIGKILL after that could hit a recycled pid.
    """
    with contextlib.suppress(ProcessLookupError):
        os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        try:
            if os.waitpid(pid, os.WNOHANG)[0]:
                return
        except ChildProcessError:
            return
        time.sleep(0.2)
    with contextlib.suppress(ProcessLookupError):
        os.kill(pid, signal.SIGKILL)
    deadline = time.monotonic() + 2  # bounded: a child stuck in D state
    while time.monotonic() < deadline:
        try:
            if os.waitpid(pid, os.WNOHANG)[0]:
                return
        except ChildProcessError:
            return
        time.sleep(0.1)


def sweep_stale_run_dirs(prefix: str, run_root: str = RUN_ROOT) -> None:
    """Remove run dirs / cid files of earlier launches on the same uid+port
    whose launcher process is gone (a crashed or SIGKILLed launcher)."""
    with contextlib.suppress(OSError):
        for entry in os.listdir(run_root):
            if not entry.startswith(prefix + "-"):
                continue
            pid_part = entry[len(prefix) + 1 :].split(".", 1)[0]
            if not pid_part.isdigit():
                continue
            try:
                os.kill(int(pid_part), 0)
                continue  # that launcher is still alive: not ours to touch
            except ProcessLookupError:
                pass
            except PermissionError:
                continue
            path = os.path.join(run_root, entry)
            if os.path.isdir(path) and not os.path.islink(path):
                shutil.rmtree(path, ignore_errors=True)
            else:
                with contextlib.suppress(OSError):
                    os.unlink(path)


def _run_launch_container(docker_argv: list[str], payload: dict[str, object]) -> int:
    """Run the supervisor (as the account) and ``docker run``; stay their root parent.

    Staying alive (instead of exec'ing docker) is what makes teardown
    reliable: sudo forwards SIGTERM to this process, which forwards it to the
    docker CLI; when sudo itself is SIGKILLed (the manager's escalation, not
    forwarded) this process notices it was reparented and force-removes ITS
    container — by the id docker wrote to a root-owned cid file, so a
    successor launch that already took the name is never touched. Each
    launch has its own run directory. A container left over from a previous
    launch on the same port is removed (and waited for) before starting.

    With the stdio transport (Kata, #3438) the launcher creates the two pipes
    of the channel: the docker CLI gets the container's ends, the supervisor
    inherits the host's ends, and the environment line is queued in the
    stdin pipe before either starts.
    """
    uid, gid = int(payload["uid"]), int(payload["gid"])
    stdio = payload.get("transport") == "stdio"
    docker = docker_argv[0]
    name = docker_argv[docker_argv.index("--name") + 1]
    run_dir = str(payload["socket_dir"])
    cidfile = str(payload["cidfile"])
    supervisor_payload = dict(payload, parent_pid=os.getpid())
    inner_env = json.dumps(supervisor_payload.pop("inner_env")).encode() + b"\n"
    setpriv_argv = list(supervisor_payload.pop("setpriv_argv"))  # type: ignore[call-overload]
    if len(inner_env) > PIPE_BUFFER - 1:
        raise ConfineError("environment too large for the hand-off pipe")
    parent = os.getppid()
    container: subprocess.Popen | None = None
    pending: list[int] = []

    def _forward(signum, _frame) -> None:
        if container is None:
            pending.append(signum)  # delivered once docker is running
            return
        with contextlib.suppress(ProcessLookupError):
            container.send_signal(signum)

    # Before anything is started, so no signal can skip the cleanup below.
    signal.signal(signal.SIGTERM, _forward)
    signal.signal(signal.SIGINT, _forward)
    supervisor = 0
    removed = False
    held: set[int] = set()  # channel pipe ends still open in this process

    def _release(*fds: int) -> None:
        for fd in fds:
            if fd in held:
                held.discard(fd)
                with contextlib.suppress(OSError):
                    os.close(fd)

    try:
        sweep_stale_run_dirs(os.path.basename(run_dir).rsplit("-", 1)[0])
        _docker_remove(docker, name)
        prepare_run_dir(run_dir, uid, gid)
        with contextlib.suppress(FileNotFoundError):
            os.unlink(cidfile)  # root-owned directory; docker refuses an existing file
        log_fd = open_root_egress_log(uid)
        supervisor_payload["log_fd"] = log_fd
        if stdio:
            to_guest_r, to_guest_w = os.pipe()  # supervisor -> container stdin
            from_guest_r, from_guest_w = os.pipe()  # container stdout -> supervisor
            held.update((to_guest_r, to_guest_w, from_guest_r, from_guest_w))
            os.write(to_guest_w, inner_env)  # the first line ``inner`` reads
            for fd in (from_guest_r, to_guest_w):
                os.set_inheritable(fd, True)
            supervisor_payload["mux_fds"] = [from_guest_r, to_guest_w]
        data = json.dumps(supervisor_payload).encode()
        supervisor = os.fork()
        if supervisor == 0:  # pragma: no cover - exercised by the acceptance run
            try:
                _exec_with_stdin(setpriv_argv, data)
            finally:
                os._exit(70)
        os.close(log_fd)  # only the supervisor writes the audit log
        if stdio:
            # Only the supervisor may hold the host ends: its exit is then
            # the container's stdin EOF, and the CLI's exit its channel EOF.
            _release(from_guest_r, to_guest_w)
        if pending:
            return 0  # stopped before docker started
        if stdio:
            container = subprocess.Popen(  # noqa: S603 - fixed argv
                docker_argv, stdin=to_guest_r, stdout=from_guest_w, env=_docker_env()
            )
            _release(to_guest_r, from_guest_w)
        else:
            container = subprocess.Popen(  # noqa: S603 - fixed argv
                docker_argv, stdin=subprocess.PIPE, env=_docker_env()
            )
            with contextlib.suppress(BrokenPipeError):  # docker may exit at once
                assert container.stdin is not None
                container.stdin.write(inner_env)
                container.stdin.close()
        # A stop that raced Popen is still forwarded, but the docker CLI may
        # not have installed its signal proxy yet and die without stopping the
        # container: the id-based removal in ``finally`` covers that.
        for signum in pending:
            _forward(signum, None)
        while True:
            try:
                return container.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                if os.getppid() != parent and not removed:
                    # sudo was killed: nothing will signal us again.
                    removed = _docker_remove_id(docker, cidfile)
    finally:
        _release(*held)
        if container is not None and not removed:
            # Idempotent: a container that exited under --rm is already gone.
            # Covers a docker CLI that died before proxying a stop signal.
            if container.poll() is None:
                with contextlib.suppress(ProcessLookupError):
                    container.kill()
            _docker_remove_id(docker, cidfile)
        if supervisor:
            _reap_supervisor(supervisor)
        shutil.rmtree(run_dir, ignore_errors=True)
        with contextlib.suppress(OSError):
            os.unlink(cidfile)


def run_launch(argv: Sequence[str]) -> int:
    if os.geteuid() != 0:
        raise ConfineError("launch must run as root (via sudo)")
    if list(argv[:1]) == ["--probe"]:
        rest = list(argv[1:])
        if rest not in ([], ["--backend", "container"], ["--backend", "kata"]):
            raise ConfineError("usage: launch --probe [--backend container|kata]")
        return run_container_probe(backend=rest[1] if rest else "container")
    systemd_argv, payload = plan_launch(argv, sys.stdin.read())
    if payload.get("backend") == "container":
        return _run_launch_container(systemd_argv, payload)
    payload["log_fd"] = open_root_egress_log(int(payload["uid"]))
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


def _ingress_limit(payload: dict) -> int:
    """Concurrent ingress clients: a slice of TasksMax, never the whole scope."""
    tasks = int(payload.get("tasks_max") or 512)
    return max(4, min(MAX_INGRESS_CLIENTS, tasks // TASKS_PER_INGRESS_CLIENT))


def run_supervise() -> int:
    payload = json.loads(sys.stdin.read())
    # The launcher's pid travels in the payload: if it already exited (docker
    # failed fast), os.getppid() here would already be 1 and never change.
    parent = int(payload.get("parent_pid") or os.getppid())
    allow = [parse_allow_entry(item) for item in payload["allow"]]
    container_mode = payload.get("backend") == "container"
    # The container backend gets a root-prepared directory (see prepare_run_dir).
    socket_dir = (
        str(payload["socket_dir"]) if container_mode else tempfile.mkdtemp(prefix="openace-webui-")
    )
    log_fd = payload.get("log_fd")
    proxy = EgressProxy(allow, int(log_fd) if log_fd is not None else None)

    def _flusher() -> None:
        while True:
            time.sleep(1.0)
            proxy.flush()

    threading.Thread(target=_flusher, daemon=True, name="log-flush").start()
    serve_client: Callable[[socket.socket], None]
    if payload.get("transport") == "stdio":
        # Kata (#3438): both directions travel over the container's stdio.
        mux_in, mux_out = (int(fd) for fd in payload["mux_fds"])
        mux = Mux(mux_in, mux_out, initiator=True, accept={MUX_KIND_EGRESS: proxy.handle})
        threading.Thread(target=mux.run, daemon=True, name="mux").start()

        def _pinger() -> None:
            while not mux.closed.wait(MUX_PING_SECONDS):
                mux.ping()

        threading.Thread(target=_pinger, daemon=True, name="mux-ping").start()

        def serve_client(client: socket.socket) -> None:
            ours, theirs = socket.socketpair()
            try:
                threading.Thread(
                    target=mux.open, args=(MUX_KIND_INGRESS, ours), daemon=True
                ).start()
            except RuntimeError:
                for sock in (ours, theirs, client):
                    with contextlib.suppress(OSError):
                        sock.close()
                return
            splice(client, theirs, idle=INGRESS_IDLE_SECONDS)

    else:
        egress_listener = _unix_listener(os.path.join(socket_dir, EGRESS_SOCKET))
        threading.Thread(
            target=_serve, args=(egress_listener, proxy.handle, "egress"), daemon=True
        ).start()

        tunnels = TunnelPool()
        tunnel_listener = _unix_listener(os.path.join(socket_dir, TUNNEL_SOCKET))

        def _accept_tunnels() -> None:
            while True:
                try:
                    conn, _ = tunnel_listener.accept()
                except OSError:
                    return
                tunnels.add(conn)

        threading.Thread(target=_accept_tunnels, daemon=True, name="tunnels").start()
        serve_client = tunnels.serve_client

    ingress_listener = socket.socket(
        socket.AF_INET6 if ":" in payload["bind_host"] else socket.AF_INET, socket.SOCK_STREAM
    )
    ingress_listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    ingress_listener.bind((payload["bind_host"], int(payload["port"])))
    ingress_listener.listen(128)
    threading.Thread(
        target=_serve,
        args=(
            ingress_listener,
            serve_client,
            "ingress",
            _ingress_limit(payload),
            max(2, _ingress_limit(payload) // 2),  # one host: at most half
        ),
        daemon=True,
    ).start()

    if container_mode:
        return _supervise_until_parent_exits(parent, socket_dir, proxy)

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
        proxy.flush(final=True)
        shutil.rmtree(socket_dir, ignore_errors=True)


def _supervise_until_parent_exits(parent: int, socket_dir: str, proxy: EgressProxy) -> int:
    """Container backend: keep the endpoints up while the root launcher runs."""
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    try:
        while os.getppid() == parent and not stop.wait(1.0):
            pass
        return 0
    finally:
        proxy.flush(final=True)
        # The directory itself sits in the root-owned run root (the launcher
        # removes it); remove what we created in it.
        for name in (TUNNEL_SOCKET, EGRESS_SOCKET):
            with contextlib.suppress(OSError):
                os.unlink(os.path.join(socket_dir, name))


# ── Mode: inner (inside the sandbox) ────────────────────────────────────────


def _read_line(fd: int, limit: int = PIPE_BUFFER) -> str:
    """One newline-terminated line from *fd*, byte by byte (no read-ahead)."""
    data = bytearray()
    while len(data) < limit:
        byte = os.read(fd, 1)
        if not byte or byte == b"\n":
            return data.decode("utf-8")
        data += byte
    raise ConfineError("environment line too long")


def run_inner(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="openace-webui-confine inner", add_help=False, allow_abbrev=False
    )
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--env-stdin", action="store_true")
    parser.add_argument("--watch-supervisor", action="store_true")
    parser.add_argument("--transport", choices=("uds", "stdio"), default="uds")
    parser.add_argument("webui_argv", nargs=argparse.REMAINDER)
    args = parser.parse_args(list(argv))
    webui_argv = list(args.webui_argv)
    if webui_argv[:1] == ["--"]:
        webui_argv = webui_argv[1:]
    stdio = args.transport == "stdio"
    if args.env_stdin:
        # Container backend: the environment arrives on stdin, never on a
        # command line or in the container's (inspectable) configuration.
        # With the stdio channel the frames follow on the same descriptor:
        # read exactly one line, unbuffered.
        line = _read_line(0) if stdio else sys.stdin.readline()
        env = json.loads(line or "{}")
        if not isinstance(env, dict):
            raise ConfineError("environment on stdin must be a JSON object")
        os.environ.update({str(k): str(v) for k, v in env.items()})

    mux: Mux | None = None
    if stdio:
        # fd 1 becomes the channel; anything else writing to stdout (the
        # WebUI, a stray print) goes to stderr instead of corrupting frames.
        channel_out = os.dup(1)
        os.dup2(2, 1)

        def _to_webui(conn: socket.socket) -> None:
            try:
                upstream = socket.create_connection(("127.0.0.1", args.port), timeout=10)
                upstream.settimeout(None)
            except OSError:
                with contextlib.suppress(OSError):
                    conn.close()
                return
            splice(conn, upstream, idle=INGRESS_IDLE_SECONDS)

        mux = Mux(0, channel_out, initiator=False, accept={MUX_KIND_INGRESS: _to_webui})
        threading.Thread(target=mux.run, daemon=True, name="mux").start()
    else:
        threading.Thread(
            target=_run_tunnel_pool,
            args=(os.path.join(INNER_SOCKET_DIR, TUNNEL_SOCKET), args.port),
            daemon=True,
            name="tunnel-pool",
        ).start()

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
            # flag (Node >= 22.21 / 24); a runtime that ignores them gets no network.
            "NODE_USE_ENV_PROXY": "1",
        }
    )

    def _to_proxy(conn: socket.socket) -> None:
        if mux is not None:
            mux.open(MUX_KIND_EGRESS, conn)
            return
        upstream = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            upstream.connect(os.path.join(INNER_SOCKET_DIR, EGRESS_SOCKET))
        except OSError:
            with contextlib.suppress(OSError):
                conn.close()
            upstream.close()
            return
        splice(conn, upstream)

    threading.Thread(target=_serve, args=(proxy_listener, _to_proxy, "out"), daemon=True).start()
    child = subprocess.Popen(webui_argv, stdin=subprocess.DEVNULL, env=child_env)  # noqa: S603

    def _stop(signum, _frame) -> None:
        with contextlib.suppress(ProcessLookupError):
            child.send_signal(signum)

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    if args.watch_supervisor:
        # Container backend: nothing kills the container if the docker CLI
        # is SIGKILLed; with the supervisor gone there is no ingress or
        # egress anyway, so stop the WebUI and let --rm remove the container.
        def _supervisor_gone() -> bool:
            if mux is not None:  # the host pings every MUX_PING_SECONDS
                return mux.closed.is_set() or (time.monotonic() - mux.last_frame > MUX_DEAD_SECONDS)
            return TUNNEL_HEALTH.supervisor_gone(SUPERVISOR_WATCH_SECONDS)

        def _watch() -> None:
            while child.poll() is None:
                time.sleep(2.0)
                if _supervisor_gone():
                    child.terminate()
                    return

        threading.Thread(target=_watch, daemon=True, name="watchdog").start()
    return child.wait()


# ── Mode: launch --probe (root; container backend readiness) ───────────────

PROBE_PROGRAM = (
    "import socket\n"
    "print(open('/proc/version').read().strip(), flush=True)\n"
    "s = socket.socket(socket.AF_UNIX)\n"
    "s.settimeout(5)\n"
    "s.connect('/run/openace/probe.sock')\n"
    "s.sendall(b'ok')\n"
)


KATA_PROBE_PROGRAM = (
    "import os, sys\n"
    "sys.stdout.write(os.uname().release + '\\n'); sys.stdout.flush()\n"
    "line = sys.stdin.readline()\n"
    "sys.stdout.write('echo:' + line); sys.stdout.flush()\n"
)
KATA_PROBE_BOOT_SECONDS = 180.0  # a nested-virtualization guest boots in ~70 s
KATA_SHIM = "containerd-shim-kata-v2"
HYPERVISORS = ("cloud-hypervisor", "firecracker", "stratovirt")
SHIM_RUNTIME_RE = re.compile(r"^io\.containerd\.([a-z0-9]+)\.v2$")


def kata_evidence(pid: int, cid: str, proc: str = "/proc") -> bool:
    """Host-side proof that container *cid* runs under Kata.

    Docker reports a Kata container's ``State.Pid`` as the HYPERVISOR process
    (under runc it is the container's own init), and that process's parent is
    the Kata shim started for exactly this container id. Both are read from
    /proc; anything unreadable is "no evidence" (fail closed).
    """
    try:
        exe = os.path.basename(os.readlink(f"{proc}/{pid}/exe"))
        with open(f"{proc}/{pid}/stat", encoding="utf-8") as handle:
            ppid = int(handle.read().rsplit(")", 1)[1].split()[1])
        shim_exe = os.path.basename(os.readlink(f"{proc}/{ppid}/exe"))
        with open(f"{proc}/{ppid}/cmdline", "rb") as handle:
            shim_args = handle.read().split(b"\0")
    except (OSError, ValueError, IndexError):
        return False
    hypervisor = exe.startswith("qemu-system-") or exe in HYPERVISORS
    target = cid.encode()
    for_this_container = any(
        shim_args[i] == b"-id" and shim_args[i + 1] == target for i in range(len(shim_args) - 1)
    )
    return hypervisor and shim_exe == KATA_SHIM and for_this_container


def _kata_runtime_available(runtime: str, registered: dict) -> bool:
    """A registered Docker runtime name, or a containerd shim name whose
    binary is on the daemon's PATH (``--runtime io.containerd.kata.v2``
    needs no daemon.json entry)."""
    if runtime in registered:
        return True
    match = SHIM_RUNTIME_RE.fullmatch(runtime)
    if match is None:
        return False
    shim = shutil.which(f"containerd-shim-{match.group(1)}-v2", path=SAFE_PATH)
    if shim is None:
        return False
    try:
        require_root_controlled_executable(shim)
    except ConfineError:
        return False
    return True


def _run_kata_probe(container: ContainerPolicy, docker, run_root: str = RUN_ROOT) -> str:
    """Boot one probe container on the Kata runtime; return a probe token."""
    probe_dir = os.path.join(run_root, f"probe-{os.getpid()}")
    cidfile = os.path.join(probe_dir, "probe.cid")
    prepare_run_dir(probe_dir, 0, 0)  # root only: docker writes the cid file
    proc: subprocess.Popen | None = None
    try:
        proc = subprocess.Popen(  # noqa: S603 - fixed argv
            [container.docker, "run", "--rm", "-i", "--cidfile", cidfile,
             "--runtime", container.kata_runtime, "--network", "none",
             "--user", "65534:65534", "--read-only", "--cap-drop", "ALL",
             "--security-opt", "no-new-privileges",
             "--entrypoint", "python3", container.image, "-I", "-c", KATA_PROBE_PROGRAM],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=_docker_env(),
        )  # fmt: skip
        lines: queue.Queue[bytes] = queue.Queue()

        def _read() -> None:
            assert proc is not None and proc.stdout is not None
            for raw in proc.stdout:
                lines.put(raw)
            lines.put(b"")

        threading.Thread(target=_read, daemon=True).start()
        try:
            release = lines.get(timeout=KATA_PROBE_BOOT_SECONDS).decode().strip()
        except queue.Empty:
            return "probe:failed"
        if not release:
            return "probe:failed"
        try:
            with open(cidfile, encoding="ascii") as handle:
                cid = handle.read().strip()
            inspect = docker("inspect", "--format", "{{.State.Pid}}", cid)
            pid = int(inspect.stdout.strip())
        except (OSError, ValueError, subprocess.SubprocessError):
            return "probe:failed"
        if not re.fullmatch(r"[0-9a-f]{64}", cid) or not kata_evidence(pid, cid):
            return "kernel:unverified"
        if release == os.uname().release:
            return "kernel:unverified"  # the guest must not run the host kernel
        assert proc.stdin is not None
        try:
            proc.stdin.write(b"ping\n")
            proc.stdin.flush()
            echo = lines.get(timeout=30).decode().strip()
        except (OSError, queue.Empty):
            echo = ""
        if echo != "echo:ping":
            return "channel:failed"
        return "ok"
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"# {exc}", file=sys.stderr)
        return "probe:failed"
    finally:
        if proc is not None:
            with contextlib.suppress(OSError):
                assert proc.stdin is not None
                proc.stdin.close()
            try:
                proc.wait(timeout=60)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            _docker_remove_id(container.docker, cidfile)
        shutil.rmtree(probe_dir, ignore_errors=True)


def run_container_probe(policy_path: str = CONFIG_PATH, backend: str = "container") -> int:
    """Print ``ok`` or one reason token; used by the manager's readiness check.

    Verifies what the container backend's guarantees rest on: the docker CLI
    is root-controlled, the runtime is registered, the pinned image exists
    locally (no pull at launch), the runtime is gVisor (positively, from the
    guest's /proc/version) and the container can connect to a host UNIX
    socket in a read-only bind mount (runsc needs --host-uds=open).
    """
    try:
        policy = load_policy(policy_path)
        if policy.container is None:
            raise ConfineError("the policy file has no 'container' section")
        require_root_controlled_executable(policy.container.docker)
    except ConfineError as exc:
        print(f"# {exc}", file=sys.stderr)
        print("policy:invalid")
        return 1
    container = policy.container
    kata = backend == "kata"
    if not (container.kata_runtime if kata else container.runtime):
        print("# the policy names no runtime for this backend", file=sys.stderr)
        print("policy:invalid")
        return 1
    if not symlinks_protected():
        print("symlinks:unprotected")
        return 1
    if kata and not os.path.exists(KVM_DEVICE):
        print("kvm:unavailable")
        return 1
    env = {"PATH": SAFE_PATH, "LANG": "C.UTF-8"}

    def _docker(*argv: str, timeout: float = 30) -> subprocess.CompletedProcess:
        return subprocess.run(  # noqa: S603 - fixed argv
            [container.docker, *argv], capture_output=True, text=True, timeout=timeout,
            check=False, env=env,
        )  # fmt: skip

    try:
        info = _docker("info", "--format", "{{json .Runtimes}}")
    except (OSError, subprocess.SubprocessError):
        print("docker:unavailable")
        return 1
    if info.returncode != 0:
        print("docker:unavailable")
        return 1
    try:
        runtimes = json.loads(info.stdout or "{}")
    except json.JSONDecodeError:
        runtimes = {}
    if not (
        _kata_runtime_available(container.kata_runtime, runtimes)
        if kata
        else container.runtime in runtimes
    ):
        print("runtime:missing")
        return 1
    if _docker("image", "inspect", "--format", "{{.Id}}", container.image).returncode != 0:
        print("image:missing")
        return 1
    if kata:
        token = _run_kata_probe(container, _docker)
        print(token)
        return 0 if token == "ok" else 1
    probe_dir = os.path.join(RUN_ROOT, f"probe-{os.getpid()}")
    received: list[bytes] = []
    try:
        prepare_run_dir(probe_dir, 65534, 65534)
        listener = _unix_listener(os.path.join(probe_dir, "probe.sock"))
        # only the probe container's account (nobody) may connect
        os.chown(os.path.join(probe_dir, "probe.sock"), 65534, 65534)
        listener.settimeout(40)

        def _accept() -> None:
            with contextlib.suppress(OSError):
                conn, _ = listener.accept()
                conn.settimeout(5)
                received.append(conn.recv(2))
                conn.close()

        acceptor = threading.Thread(target=_accept, daemon=True)
        acceptor.start()
        result = _docker(
            "run", "--rm", "--runtime", container.runtime, "--network", "none",
            "--user", "65534:65534", "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--mount", f"type=bind,src={probe_dir},dst={INNER_SOCKET_DIR},readonly",
            "--entrypoint", "python3", container.image, "-I", "-c", PROBE_PROGRAM,
            timeout=90,
        )  # fmt: skip
        # docker run has returned: the container either connected already or
        # never will — close the listener so the accept thread ends now.
        listener.close()
        acceptor.join(timeout=2)
    except (OSError, subprocess.SubprocessError, ConfineError) as exc:
        print(f"# {exc}", file=sys.stderr)
        print("probe:failed")
        return 1
    finally:
        shutil.rmtree(probe_dir, ignore_errors=True)
    if "gvisor" not in (result.stdout or "").lower():
        print(f"# guest kernel: {(result.stdout or result.stderr).strip()[:200]}", file=sys.stderr)
        print("kernel:unverified")
        return 1
    if received != [b"ok"]:
        print("runtime:no-host-uds")
        return 1
    print("ok")
    return 0


# ── Mode: check (readiness probe, unprivileged) ─────────────────────────────


def run_check(argv: Sequence[str], policy_path: str = CONFIG_PATH) -> int:
    """Exit 0 when this host can confine, else print one reason token.

    Checks: tools present, systemd running, the policy file valid and listing
    ``--webui`` (a root-controlled executable), and unprivileged user
    namespaces usable by bubblewrap (the Ubuntu 24.04+ AppArmor restriction is
    the common failure). The manager maps the token to a reason code.
    """
    parser = argparse.ArgumentParser(
        prog="openace-webui-confine check", add_help=False, allow_abbrev=False
    )
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
        policy = load_policy(policy_path)
        if args.webui:
            if os.path.normpath(args.webui) not in policy.webuis:
                raise ConfineError(f"{args.webui} is not listed in {policy_path}")
            require_root_controlled_executable(args.webui)
            require_root_controlled_package(args.webui)
        require_root_controlled_path(policy.path)
    except ConfineError as exc:
        print(f"# {exc}", file=sys.stderr)
        print("policy:invalid")
        return 1
    bwrap = shutil.which("bwrap", path=SAFE_PATH) or "bwrap"

    def _bwrap_ok(*extra: str) -> bool:
        result = subprocess.run(  # noqa: S603 - fixed argv
            [bwrap, "--ro-bind", "/", "/", "--unshare-user", *extra, "--unshare-net",
             "--", "/bin/true"],
            capture_output=True,
            timeout=10,
            check=False,
        )  # fmt: skip
        return result.returncode == 0

    if not _bwrap_ok():
        print("userns:unavailable")
        return 1
    if not _bwrap_ok("--disable-userns"):
        print("bwrap:too-old")  # --disable-userns needs bubblewrap >= 0.8
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
