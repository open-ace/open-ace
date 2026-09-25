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

    def swap_egress_log():
        # Replace the host-side egress log with a symlink into our home: the
        # supervisor must keep writing to the descriptor it opened, never here.
        log_dir = os.environ.get("OPENACE_LOG_DIR")
        if not log_dir:
            return None
        log = os.path.join(log_dir, "confine-egress.log")
        try:
            os.unlink(log)
            os.symlink(os.path.join(os.environ["HOME"], "log-redirect"), log)
            return True
        except OSError:
            return False

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            base = os.path.dirname(os.environ["HOME"])
            swapped = swap_egress_log()
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
                "log_swapped": swapped,
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


def setup(real_webui: bool) -> None:
    sudo("install", "-o", "root", "-g", "root", "-m", "0755", str(WRAPPER_SRC), WRAPPER)
    probe = Path("/tmp/openace-confine-probe")
    probe.write_text(PROBE_SOURCE)
    sudo("install", "-o", "root", "-g", "root", "-m", "0755", str(probe), PROBE)
    probe.unlink()
    webuis = [PROBE] + ([REAL_WEBUI] if real_webui else [])
    sudo("install", "-d", "-o", "root", "-g", "root", "-m", "0755", "/etc/openace")
    sudo("tee", POLICY, input=json.dumps({"webui": webuis, "path": "/usr/local/bin:/usr/bin:/bin"}))
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
    sudo("rm", "-rf", LOG_DIR)
    sudo("install", "-d", "-o", "cfa", "-g", "cfa", "-m", "0755", LOG_DIR)


def teardown() -> None:
    sudo("pkill", "-u", "cfa", check=False)
    for name in USERS:
        sudo("userdel", "-r", name, check=False)
    sudo("groupdel", SHARED_GROUP, check=False)
    sudo("rm", "-rf", BASE, LOG_DIR, PROBE, check=False)


def launch(
    port: int, webui: str, webui_args: list[str], env: dict, allow: list[str]
) -> subprocess.Popen:
    cmd = [
        "sudo", "-n", WRAPPER, "launch", "--account", "cfa", "--port", str(port),
        "--memory-max", "512M", "--cpu-quota", "100", "--tasks-max", "128",
        "--log-dir", LOG_DIR,
    ]  # fmt: skip
    for entry in allow:
        cmd += ["--allow", entry]
    cmd += ["--webui", webui, "--", *webui_args]
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
        redirected = sudo("test", "-s", f"{BASE}/cfa/log-redirect", check=False).returncode == 0
        record.check(
            "egress log swap cannot redirect supervisor writes",
            report["log_swapped"] is True and not redirected,
            {"swapped": report["log_swapped"], "redirected": redirected},
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
        # The probe unlinked the original log (its inode lives on in the
        # supervisor's descriptor); what matters is checked above: no write
        # was redirected through the planted symlink.
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
    sudo("rm", "-f", f"{LOG_DIR}/confine-egress.log")  # owned by the account
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
        log = sudo("cat", f"{LOG_DIR}/confine-egress.log", check=False).stdout
        record.check("real WebUI upstream call leaves through the egress proxy",
                     f"ALLOW 'POST' '{target}':{ALLOWED_PORT}" in log,
                     [result.stdout, log.strip()])  # fmt: skip
    finally:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=15)


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
    for tool in ("bwrap", "setpriv", "systemd-run", "nsenter", "curl"):
        if shutil.which(tool) is None:
            print(f"refusing: {tool} is not installed", file=sys.stderr)
            return 2
    real_webui = os.path.exists(REAL_WEBUI)
    record = Record()
    try:
        setup(real_webui)
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
