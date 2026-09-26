#!/usr/bin/env python3
"""Issue #3431 (Option 1): real-Linux acceptance for the confined os_user WebUI.

Runs ``openace-webui-confine`` for real (systemd scope + setpriv + bubblewrap)
against a probe WebUI that reports, from inside the sandbox, what it can see
and reach — and, when ``/usr/bin/qwen-code-webui`` is installed, against the
real WebUI as well. Prints a JSON record; a non-zero exit means acceptance
FAILED.

Deliberately NOT a pytest module: it needs root (sudo), systemd and
bubblewrap, and it creates OS accounts. Run it ONLY on a disposable host:

    CONFINE_ACCEPTANCE_DISPOSABLE=1 python3 scripts/webui_confine_acceptance.py

Option 2 (``--backend container``, a Docker + gVisor host with a registered
``runsc --host-uds=open`` runtime and the webui-sandbox image built locally):

    CONFINE_ACCEPTANCE_DISPOSABLE=1 CONFINE_ACCEPTANCE_BACKEND=container \
    CONFINE_ACCEPTANCE_IMAGE=<image id or name@sha256:...> \
    CONFINE_ACCEPTANCE_RUNTIME=runsc-openace python3 scripts/webui_confine_acceptance.py

Kata (#3438, ``--backend kata``, a Docker host with /dev/kvm and Kata >= 3.32
whose containerd-shim-kata-v2 is on dockerd's PATH):

    CONFINE_ACCEPTANCE_DISPOSABLE=1 CONFINE_ACCEPTANCE_BACKEND=kata \
    CONFINE_ACCEPTANCE_IMAGE=<image id or name@sha256:...> \
    CONFINE_ACCEPTANCE_RUNTIME=io.containerd.kata.v2 python3 scripts/webui_confine_acceptance.py

It installs scripts/openace-webui-confine.py to /usr/local/bin, writes
/etc/openace/webui-confine.json, creates the accounts ``cfa`` and ``cfb`` under
/srv/openace-confine-acceptance, and removes the accounts and that tree on exit.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import textwrap
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WRAPPER_SRC = REPO / "scripts" / "openace-webui-confine.py"
WRAPPER = "/usr/local/bin/openace-webui-confine"
POLICY = "/etc/openace/webui-confine.json"
PROBE = "/usr/local/bin/openace-confine-probe-webui"
REAL_WEBUI = "/usr/bin/qwen-code-webui"
BASE = "/srv/openace-confine-acceptance"
USERS = {"cfa": 3431, "cfb": 3432}
SHARED_GROUP = "cfa-shared"
LOG_DIR = "/tmp/qwen-code-webui-3431"
ALLOWED_PORT, DENIED_PORT = 18431, 18432
SECRET = "proxy-token-3431-acceptance"
AUDIT_LOG = "/var/log/openace-webui/3431.egress.log"  # root-owned, per uid

PROBE_SOURCE = textwrap.dedent('''\
    #!/usr/bin/python3
    """Acceptance probe WebUI: GET / returns what the sandbox can see/reach."""
    import json, os, socket, sys, urllib.error, urllib.request
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    a = sys.argv[1:]
    port, host = int(a[a.index("--port") + 1]), a[a.index("--host") + 1]
    target = os.environ["ACCEPTANCE_TARGET"]

    def via_proxy(url):
        try:
            return urllib.request.urlopen(url, timeout=5).status
        except urllib.error.HTTPError as exc:
            return exc.code
        except Exception as exc:
            return "error:" + type(exc).__name__

    def direct(h, p):
        try:
            socket.create_connection((h, p), timeout=3).close()
            return "connected"
        except Exception as exc:
            return "blocked:" + type(exc).__name__

    def readable(path):
        try:
            with open(path) as handle:
                handle.read(1)
            return True
        except Exception:
            return False

    def sockdir_writable():
        try:
            with open("/run/openace/planted", "w") as handle:
                handle.write("x")
            return True
        except OSError:
            return False

    def audit_log_openable():
        # The egress audit log is root-owned; the account only holds an
        # inherited descriptor outside the sandbox. Nothing here may open it.
        path = "/var/log/openace-webui/%d.egress.log" % os.getuid()
        try:
            open(path, "a").close()
            return True
        except OSError:
            return False

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            base = os.path.dirname(os.environ["HOME"])
            report = {
                "uid": os.getuid(), "groups": sorted(os.getgroups()),
                "base_entries": sorted(os.listdir(base)),
                "run_entries": sorted(os.listdir("/run")),
                "tmp_entries": sorted(os.listdir("/tmp")),
                "shared_readable": readable(os.path.join(base, "shared", "proj", "README")),
                "home_writable": os.access(os.environ["HOME"], os.W_OK),
                "etc_writable": os.access("/etc", os.W_OK),
                "proxy_allowed": via_proxy(f"http://{target}:%d/" % ALLOWED_PORT),
                "proxy_denied_port": via_proxy(f"http://{target}:%d/" % DENIED_PORT),
                "proxy_denied_host": via_proxy("http://example.com/"),
                "direct_target": direct(target, ALLOWED_PORT),
                "secret_in_env": os.environ.get("OPENAI_API_KEY"),
                "pid1": open("/proc/1/comm").read().strip(),
                "sockdir_writable": sockdir_writable(),
                "audit_log_openable": audit_log_openable(),
            }
            body = json.dumps(report).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    ThreadingHTTPServer((host, port), Handler).serve_forever()
    ''').replace("ALLOWED_PORT", str(ALLOWED_PORT)).replace("DENIED_PORT", str(DENIED_PORT))


def sh(*argv: str, check: bool = True, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(argv, check=check, text=True, capture_output=True, **kwargs)


def sudo(*argv: str, check: bool = True, **kwargs) -> subprocess.CompletedProcess:
    return sh("sudo", "-n", *argv, check=check, **kwargs)


class Record:
    def __init__(self) -> None:
        self.items: list[dict] = []

    def check(self, name: str, ok: bool, detail: object = None) -> None:
        self.items.append({"check": name, "ok": bool(ok), "detail": detail})
        print(
            f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail is not None else "")
        )

    @property
    def passed(self) -> bool:
        return all(item["ok"] for item in self.items)


def host_ip() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.connect(("10.255.255.255", 1))
            return probe.getsockname()[0]
        except OSError:
            return sh("hostname", "-I").stdout.split()[0]


def container_backend() -> str:
    """ "container" (gVisor), "kata", or "" for the bwrap backend."""
    backend = os.environ.get("CONFINE_ACCEPTANCE_BACKEND", "")
    return backend if backend in ("container", "kata") else ""


def container_policy() -> dict | None:
    backend = container_backend()
    if not backend:
        return None
    if backend == "kata":
        runtime = os.environ.get("CONFINE_ACCEPTANCE_RUNTIME", "io.containerd.kata.v2")
        runtimes = {"kata": runtime}
    else:
        runtimes = {"runsc": os.environ.get("CONFINE_ACCEPTANCE_RUNTIME", "runsc-openace")}
    return {
        "image": os.environ["CONFINE_ACCEPTANCE_IMAGE"],
        "runtimes": runtimes,
        "docker": shutil.which("docker") or "/usr/bin/docker",
    }


def setup(real_webui: bool) -> None:
    sudo("install", "-o", "root", "-g", "root", "-m", "0755", str(WRAPPER_SRC), WRAPPER)
    probe = Path("/tmp/openace-confine-probe")
    probe.write_text(PROBE_SOURCE)
    sudo("install", "-o", "root", "-g", "root", "-m", "0755", str(probe), PROBE)
    probe.unlink()
    webuis = [PROBE] + ([REAL_WEBUI] if real_webui else [])
    sudo("install", "-d", "-o", "root", "-g", "root", "-m", "0755", "/etc/openace")
    policy = {"webui": webuis, "path": "/usr/local/bin:/usr/bin:/bin"}
    container = container_policy()
    if container is not None:
        policy["container"] = container
    sudo("tee", POLICY, input=json.dumps(policy))
    sudo("chmod", "0644", POLICY)
    sudo("install", "-d", "-o", "root", "-g", "root", "-m", "0755", BASE)
    if sh("getent", "group", SHARED_GROUP, check=False).returncode != 0:
        sudo("groupadd", SHARED_GROUP)
    for name, uid in USERS.items():
        if sh("id", name, check=False).returncode != 0:
            sudo("useradd", "-u", str(uid), "-d", f"{BASE}/{name}", "-m", "-s", "/bin/bash", name)
        sudo("chmod", "0700", f"{BASE}/{name}")
    sudo("usermod", "-aG", SHARED_GROUP, "cfa")
    sudo("install", "-d", "-o", "root", "-g", SHARED_GROUP, "-m", "3770", f"{BASE}/shared")
    sudo("install", "-d", "-o", "cfb", "-g", SHARED_GROUP, "-m", "2770", f"{BASE}/shared/proj")
    sudo("tee", f"{BASE}/shared/proj/README", input="shared\n")
    sudo("chown", f"cfb:{SHARED_GROUP}", f"{BASE}/shared/proj/README")
    sudo("chmod", "0660", f"{BASE}/shared/proj/README")
    sudo("tee", f"{BASE}/cfb/secret", input="secret of cfb\n")
    sudo("rm", "-rf", LOG_DIR, AUDIT_LOG)
    sudo("install", "-d", "-o", "cfa", "-g", "cfa", "-m", "0755", LOG_DIR)


def teardown() -> None:
    sudo("pkill", "-KILL", "-u", "cfa", check=False)
    deadline = time.monotonic() + 15  # userdel refuses while processes remain
    while time.monotonic() < deadline and sh("pgrep", "-u", "cfa", check=False).stdout.strip():
        time.sleep(0.5)
    for name in USERS:
        sudo("userdel", "-r", name, check=False)
    sudo("groupdel", SHARED_GROUP, check=False)
    sudo("rm", "-rf", BASE, LOG_DIR, PROBE, AUDIT_LOG, check=False)


def launch(
    port: int,
    webui: str,
    webui_args: list[str],
    env: dict,
    allow: list[str],
    backend: str = "bwrap",
) -> subprocess.Popen:
    cmd = [
        "sudo", "-n", WRAPPER, "launch", "--account", "cfa", "--port", str(port),
        "--memory-max", "512M", "--cpu-quota", "100", "--tasks-max", "128",
        "--log-dir", LOG_DIR,
    ]  # fmt: skip
    for entry in allow:
        cmd += ["--allow", entry]
    cmd += ["--webui", webui, "--backend", backend, "--", *webui_args]
    process = subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        start_new_session=True,
    )  # fmt: skip
    assert process.stdin is not None
    process.stdin.write(json.dumps(env).encode())
    process.stdin.close()
    return process


# Loopback requests must not follow the harness's own HTTP(S)_PROXY (OrbStack
# and corporate hosts export one), or they never reach the sandbox.
_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def wait_http(url: str, timeout: float = 30.0) -> tuple[int, bytes] | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with _DIRECT.open(url, timeout=15) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()
        except OSError:
            time.sleep(0.5)
    return None


def scope_gone(unit: str, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if sh("systemctl", "is-active", unit, check=False).stdout.strip() != "active":
            if not sh("pgrep", "-u", "cfa", check=False).stdout.strip():
                return True
        time.sleep(0.5)
    return False


def cmdlines_containing(needle: str) -> list[str]:
    hits = []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            cmdline = (proc / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
        except OSError:
            continue
        if needle in cmdline and "cmdlines_containing" not in cmdline:
            hits.append(f"{proc.name}: {cmdline[:120]}")
    return hits


def run(record: Record, real_webui: bool) -> None:
    target = host_ip()
    servers = [
        subprocess.Popen(
            [sys.executable, "-m", "http.server", str(port), "--bind", "0.0.0.0"],  # noqa: S104
            cwd="/tmp", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )  # fmt: skip
        for port in (ALLOWED_PORT, DENIED_PORT)
    ]
    try:
        _run_probe(record, target)
        _run_teardown_paths(record, target)
        _run_policy_refusal(record, target)
        _run_privileged_group_refusal(record, target)
        if real_webui:
            _run_real_webui(record, target)
    finally:
        for server in servers:
            server.terminate()


def _run_probe(record: Record, target: str) -> None:
    record.check(
        "check mode passes", sh(WRAPPER, "check", "--webui", PROBE, check=False).returncode == 0
    )
    env = {"OPENAI_API_KEY": SECRET, "ACCEPTANCE_TARGET": target, "OPENACE_LOG_DIR": LOG_DIR}
    process = launch(
        3431, PROBE, ["--port", "3431", "--host", "127.0.0.1"], env, [f"{target}:{ALLOWED_PORT}"]
    )
    try:
        answer = wait_http("http://127.0.0.1:3431/")
        record.check(
            "probe webui reachable through the host port",
            answer is not None and answer[0] == 200,
            None if answer is None else [answer[0], answer[1][:300].decode(errors="replace")],
        )
        if answer is None or answer[0] != 200:
            return
        report = json.loads(answer[1])
        uid = USERS["cfa"]
        record.check("runs as the account", report["uid"] == uid, report["uid"])
        record.check(
            "no root group (setpriv --init-groups)", 0 not in report["groups"], report["groups"]
        )
        record.check(
            "other homes hidden",
            report["base_entries"] == ["cfa", "shared"],
            report["base_entries"],
        )
        record.check("shared project readable via group", report["shared_readable"] is True)
        record.check("/run and /tmp hidden", report["run_entries"] == ["openace"]
                     and report["tmp_entries"] == [os.path.basename(LOG_DIR)],
                     [report["run_entries"], report["tmp_entries"]])  # fmt: skip
        record.check(
            "home writable, /etc read-only", report["home_writable"] and not report["etc_writable"]
        )
        record.check(
            "allowlisted host:port reachable via proxy",
            report["proxy_allowed"] == 200,
            report["proxy_allowed"],
        )
        record.check(
            "other port denied", report["proxy_denied_port"] == 403, report["proxy_denied_port"]
        )
        record.check(
            "other host denied", report["proxy_denied_host"] == 403, report["proxy_denied_host"]
        )
        record.check(
            "direct connection impossible",
            str(report["direct_target"]).startswith("blocked"),
            report["direct_target"],
        )
        record.check("environment delivered", report["secret_in_env"] == SECRET)
        record.check(
            "secret on no command line",
            not cmdlines_containing(SECRET),
            cmdlines_containing(SECRET),
        )
        record.check("pid namespace", report["pid1"] == "bwrap", report["pid1"])
        record.check("socket directory read-only inside", report["sockdir_writable"] is False)
        record.check(
            "egress audit log not openable from the sandbox",
            report["audit_log_openable"] is False,
        )
        unit = "openace-webui-3431-3431.scope"
        props = dict(
            line.split("=", 1)
            for line in sh("systemctl", "show", unit, "-p", "MemoryMax", "-p", "TasksMax",
                           "-p", "CPUQuotaPerSecUSec").stdout.split()  # fmt: skip
        )
        record.check("cgroup limits applied", props == {
            "MemoryMax": str(512 * 1024 * 1024), "TasksMax": "128", "CPUQuotaPerSecUSec": "1s",
        }, props)  # fmt: skip
        log = sudo("cat", AUDIT_LOG, check=False).stdout
        record.check(
            "egress decisions in the root-owned audit log",
            "ALLOW 'GET'" in log and "DENY 'GET'" in log,
            sudo("stat", "-c", "%U:%G %a", AUDIT_LOG, check=False).stdout.strip(),
        )
    finally:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=15)


def _run_teardown_paths(record: Record, target: str) -> None:
    allow = [f"{target}:{ALLOWED_PORT}"]
    env = {"ACCEPTANCE_TARGET": target}
    process = launch(3432, PROBE, ["--port", "3432", "--host", "127.0.0.1"], env, allow)
    wait_http("http://127.0.0.1:3432/")
    process.terminate()  # what WebUIManager does: SIGTERM to sudo
    process.wait(timeout=15)
    record.check(
        "SIGTERM to sudo tears the sandbox down", scope_gone("openace-webui-3431-3432.scope")
    )

    process = launch(3433, PROBE, ["--port", "3433", "--host", "127.0.0.1"], env, allow)
    wait_http("http://127.0.0.1:3433/")
    # sudo does not forward SIGKILL: the supervisor must notice its parent is
    # gone and stop the sandbox on its own.
    sudo("kill", "-KILL", str(process.pid), check=False)
    process.wait(timeout=15)
    record.check(
        "SIGKILL to sudo still tears the sandbox down", scope_gone("openace-webui-3431-3433.scope")
    )


def _run_privileged_group_refusal(record: Record, target: str) -> None:
    if sh("id", "cfsudo", check=False).returncode != 0:
        sudo("useradd", "-u", "3439", "-d", f"{BASE}/cfsudo", "-m", "-G", "sudo", "cfsudo")
    try:
        process = subprocess.Popen(
            ["sudo", "-n", WRAPPER, "launch", "--account", "cfsudo", "--port", "3439",
             "--memory-max", "512M", "--cpu-quota", "100", "--tasks-max", "128",
             "--allow", f"{target}:{ALLOWED_PORT}", "--log-dir", LOG_DIR, "--webui", PROBE],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )  # fmt: skip
        _, err = process.communicate(b"{}", timeout=15)
        record.check(
            "account in a privileged group refused",
            process.returncode == 64 and b"privileged group" in err,
            err.decode().strip(),
        )
    finally:
        sudo("userdel", "-r", "cfsudo", check=False)


def _run_policy_refusal(record: Record, target: str) -> None:
    process = launch(3434, "/usr/bin/python3", ["-c", "print(1)"], {}, [f"{target}:{ALLOWED_PORT}"])
    process.wait(timeout=15)
    stderr = process.stderr.read().decode() if process.stderr else ""
    record.check("unlisted executable refused before any scope exists",
                 process.returncode == 64 and "not listed" in stderr
                 and sh("systemctl", "is-active", "openace-webui-3431-3434.scope", check=False).stdout.strip() != "active",
                 stderr.strip())  # fmt: skip


def _run_real_webui(record: Record, target: str) -> None:
    upstream = f"http://{target}:{ALLOWED_PORT}"
    env = {
        "OPENAI_API_KEY": SECRET,
        "OPENAI_BASE_URL": f"{upstream}/api/proxy/v1",
        "OPENACE_LOG_DIR": LOG_DIR,
    }
    args = ["--port", "3435", "--host", "127.0.0.1", "--token-secret", "acceptance",
            "--quota-check-enabled", "--openace-api-url", upstream, "--auth-type", "openai"]  # fmt: skip
    sudo("rm", "-f", AUDIT_LOG)
    process = launch(3435, REAL_WEBUI, args, env, [f"{target}:{ALLOWED_PORT}"])
    try:
        answer = wait_http("http://127.0.0.1:3435/", timeout=60)
        record.check("real qwen-code-webui serves its UI through the host port",
                     answer is not None and answer[0] == 200 and b"Qwen Code" in answer[1])  # fmt: skip
        webui_log = Path(LOG_DIR, "webui.log")
        deadline = time.monotonic() + 30
        while (
            time.monotonic() < deadline
            and "LLM proxy started on port" not in webui_log.read_text(errors="replace")
        ):
            time.sleep(0.5)
        text = webui_log.read_text(errors="replace")
        port = text.split("LLM proxy started on port", 1)[1].split()[0].strip(",")
        node = sh("pgrep", "-u", "cfa", "-x", "node").stdout.split()[0]
        # Drive the WebUI's own upstream path from inside its network namespace.
        result = sudo(
            "nsenter", "-t", node, "-n", "curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
            "-m", "20", "-X", "POST", "-H", "content-type: application/json", "-d", "{}",
            f"http://127.0.0.1:{port}/chat/completions", check=False,
        )  # fmt: skip
        # 0600, owned by the account (opened by the supervisor before bwrap)
        log = sudo("cat", AUDIT_LOG, check=False).stdout
        record.check("real WebUI upstream call leaves through the egress proxy",
                     f"ALLOW 'POST' '{target}':{ALLOWED_PORT}" in log,
                     [result.stdout, log.strip()])  # fmt: skip
    finally:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=15)


def run_container(record: Record, backend: str = "container") -> None:
    """The real WebUI from the pinned image: Option 2 on gVisor (``container``)
    or #3438 on Kata (``kata``, ingress/egress over the stdio channel)."""
    kata = backend == "kata"
    runtime_label = "Kata" if kata else "gVisor"
    boot = 300.0 if kata else 90.0  # a nested-virtualization Kata guest boots slowly
    probe_argv = [WRAPPER, "launch", "--probe", *(["--backend", "kata"] if kata else [])]
    target = host_ip()
    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(ALLOWED_PORT), "--bind", "0.0.0.0"],  # noqa: S104
        cwd="/tmp", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )  # fmt: skip
    name = "openace-webui-3431-3441"
    upstream = f"http://{target}:{ALLOWED_PORT}"
    env = {"OPENAI_API_KEY": SECRET, "OPENAI_BASE_URL": f"{upstream}/api/proxy/v1",
           "OPENACE_LOG_DIR": LOG_DIR}  # fmt: skip
    args = ["--port", "3441", "--host", "127.0.0.1", "--token-secret", "acceptance",
            "--quota-check-enabled", "--openace-api-url", upstream, "--auth-type", "openai"]  # fmt: skip

    def _launch() -> subprocess.Popen:
        return launch(3441, REAL_WEBUI, args, env, [f"{target}:{ALLOWED_PORT}"],
                      backend=backend)  # fmt: skip

    try:
        probe = sudo(*probe_argv, check=False, timeout=400)
        record.check("root probe passes", probe.returncode == 0, probe.stdout.strip())
        process = _launch()
        answer = wait_http("http://127.0.0.1:3441/", timeout=boot)
        record.check(f"real WebUI serves its UI from the {runtime_label} container",
                     answer is not None and answer[0] == 200 and b"Qwen Code" in answer[1])  # fmt: skip
        if kata:
            guest = sudo("docker", "exec", name, "uname", "-r", check=False).stdout.strip()
            record.check("guest kernel is not the host kernel",
                         bool(guest) and guest != os.uname().release,
                         {"guest": guest, "host": os.uname().release})  # fmt: skip
            pid = sudo("docker", "inspect", "-f", "{{.State.Pid}}", name, check=False).stdout
            exe = sudo("readlink", f"/proc/{pid.strip()}/exe", check=False).stdout.strip()
            record.check("the host sees a hypervisor, not the WebUI",
                         os.path.basename(exe).startswith("qemu-system-")
                         or os.path.basename(exe) in ("cloud-hypervisor", "firecracker"),
                         exe)  # fmt: skip
            direct = sudo(
                "docker", "exec", name, "python3", "-c",
                "import socket\n"
                f"try: socket.create_connection(('{target}', {ALLOWED_PORT}), timeout=5); print('reached')\n"
                "except OSError as e: print('blocked', type(e).__name__)",
                check=False,
            ).stdout.strip()  # fmt: skip
            record.check("a direct connection from the guest fails", direct.startswith("blocked"),
                         direct)  # fmt: skip
        else:
            kernel = sudo("docker", "exec", name, "cat", "/proc/version", check=False).stdout
            record.check("guest kernel is gVisor", "gvisor" in kernel.lower(), kernel.strip()[:40])
        cfg = sudo("docker", "inspect", "-f",
                   "{{.HostConfig.NetworkMode}} {{.HostConfig.ReadonlyRootfs}} {{.Config.User}} "
                   "{{.HostConfig.CapDrop}}", name, check=False).stdout.split()  # fmt: skip
        record.check("network none, read-only root, account uid, caps dropped",
                     cfg[:3] == ["none", "true", f"{USERS['cfa']}:{USERS['cfa']}"]
                     and "ALL" in " ".join(cfg[3:]), cfg)  # fmt: skip
        env_seen = sudo("docker", "inspect", "-f", "{{json .Config.Env}}", name).stdout
        record.check("environment not visible in docker inspect", SECRET not in env_seen)
        record.check("secret on no command line", not cmdlines_containing(SECRET))
        listing = sudo("docker", "exec", name, "ls", BASE, check=False).stdout.split()
        record.check(
            "other homes hidden inside the container", listing == ["cfa", "shared"], listing
        )
        log_text = Path(LOG_DIR, "webui.log")
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and "LLM proxy started on port" not in (
            log_text.read_text(errors="replace") if log_text.exists() else ""
        ):
            time.sleep(0.5)
        port = (
            log_text.read_text(errors="replace")
            .split("LLM proxy started on port", 1)[1]
            .split()[0]
            .strip(",")
        )
        sudo("docker", "exec", name, "python3", "-c",
             "import urllib.request as u\n"
             f"r=u.Request('http://127.0.0.1:{port}/chat/completions',data=b'{{}}',"
             "headers={'content-type':'application/json'})\n"
             "try: u.urlopen(r,timeout=20)\nexcept Exception: pass", check=False)  # fmt: skip
        audit = sudo("cat", AUDIT_LOG, check=False).stdout
        record.check("upstream call leaves through the egress proxy (audit log)",
                     f"ALLOW 'POST' '{target}':{ALLOWED_PORT}" in audit, audit.strip()[-160:])  # fmt: skip
        process.terminate()
        process.wait(timeout=30)
        gone = not sudo("docker", "ps", "-aq", "--filter", f"name={name}").stdout.strip()
        record.check("SIGTERM to sudo removes the container", gone)
        # (a) the docker CLI is SIGKILLed, then the manager relaunches at once
        # on the same port: the leftover container must not block the launch.
        process = _launch()
        wait_http("http://127.0.0.1:3441/", timeout=boot)
        for pid in sh(
            "pgrep", "-f", f"docker run --rm -i --init --name {name}", check=False
        ).stdout.split():
            sudo("kill", "-KILL", pid, check=False)
        process.wait(timeout=30)
        process = _launch()
        answer = wait_http("http://127.0.0.1:3441/", timeout=boot)
        record.check("immediate relaunch after SIGKILL of the docker CLI serves again",
                     answer is not None and answer[0] == 200)  # fmt: skip

        # (b) the manager's escalation with inner suspended from inside the
        # container: SIGTERM to sudo, wait, SIGKILL to sudo (not forwarded).
        # The image has no pkill/ps: suspend `inner` with python, excluding
        # the suspending process itself, and FAIL if nothing was suspended.
        suspended = sudo(
            "docker", "exec", name, "python3", "-c",
            "import os, signal\n"
            "me = os.getpid(); hits = []\n"
            "for pid in os.listdir('/proc'):\n"
            "    if not pid.isdigit() or int(pid) == me: continue\n"
            "    try: cmd = open(f'/proc/{pid}/cmdline','rb').read()\n"
            "    except OSError: continue\n"
            "    if b'confine.py' in cmd and b'inner' in cmd:\n"
            "        os.kill(int(pid), signal.SIGSTOP); hits.append(pid)\n"
            "print(len(hits)); raise SystemExit(0 if hits else 1)",
            check=False,
        )  # fmt: skip
        record.check("inner suspended inside the container", suspended.returncode == 0,
                     suspended.stdout.strip())  # fmt: skip
        process.terminate()
        escalated = False
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            escalated = True  # SIGTERM alone could not stop it: the case under test
            process.kill()
            process.wait(timeout=10)
        record.check("the stop really needed the kill escalation", escalated)
        deadline = time.monotonic() + 60
        while (
            time.monotonic() < deadline
            and sudo("docker", "ps", "-q", "--filter", f"name={name}").stdout.strip()
        ):
            time.sleep(1)
        container_gone = not sudo("docker", "ps", "-q", "--filter", f"name={name}").stdout.strip()
        port_free = wait_http("http://127.0.0.1:3441/", timeout=3) is None
        record.check("manager stop (terminate, then kill of sudo) removes a suspended container",
                     container_gone and port_free,
                     {"container_gone": container_gone, "port_free": port_free})  # fmt: skip
        process = _launch()  # (c) relaunch at once on the same port
        answer = wait_http("http://127.0.0.1:3441/", timeout=boot)
        record.check("relaunch right after a kill-escalated stop serves again",
                     answer is not None and answer[0] == 200)  # fmt: skip
        process.terminate()
        process.wait(timeout=30)
        leftovers = sh("sudo", "-n", "ls", "/run/openace-webui", check=False).stdout.split()
        record.check("no run directories or cid files left behind",
                     [e for e in leftovers if e.startswith("3431-")] == [], leftovers)  # fmt: skip
        policy_text = sudo("cat", POLICY).stdout
        broken = json.loads(policy_text)
        if kata:
            # runc claimed as Kata: the host-side evidence must refuse it
            broken["container"]["runtimes"]["kata"] = "runc"
            expected, label = "kernel:unverified", "probe refuses runc claimed as Kata"
        else:
            broken["container"]["runtimes"]["runsc"] = "runsc"  # plain runsc: no host UDS
            expected, label = "runtime:no-host-uds", "probe refuses a runtime without host UDS"
        sudo("tee", POLICY, input=json.dumps(broken))
        probe = sudo(*probe_argv, check=False, timeout=400)
        record.check(label, probe.stdout.strip() == expected, probe.stdout.strip())
        sudo("tee", POLICY, input=policy_text)
    finally:
        server.terminate()
        sudo("docker", "rm", "-f", name, check=False)


def main() -> int:
    if sys.platform != "linux" or not os.path.isdir("/run/systemd/system"):
        print("refusing: needs Linux with systemd running", file=sys.stderr)
        return 2
    if os.environ.get("CONFINE_ACCEPTANCE_DISPOSABLE") != "1":
        print(
            "refusing: set CONFINE_ACCEPTANCE_DISPOSABLE=1 (creates/deletes accounts)",
            file=sys.stderr,
        )
        return 2
    tools = (
        ("setpriv", "docker")
        if container_policy()
        else ("bwrap", "setpriv", "systemd-run", "nsenter", "curl")
    )
    for tool in tools:
        if shutil.which(tool) is None:
            print(f"refusing: {tool} is not installed", file=sys.stderr)
            return 2
    container = container_policy() is not None
    real_webui = container or os.path.exists(REAL_WEBUI)
    record = Record()
    try:
        setup(real_webui)
        if container:
            run_container(record, container_backend())
        else:
            run(record, real_webui)
    finally:
        teardown()
    print(
        json.dumps(
            {"passed": record.passed, "real_webui": real_webui, "items": record.items}, indent=2
        )
    )
    return 0 if record.passed else 1


if __name__ == "__main__":
    sys.exit(main())
