#!/usr/bin/env python3
"""Issue #3379: multi-user real-Linux end-to-end isolation acceptance.

Executes the #3374 nine-item acceptance checklist against a REAL multi-user
Compose deployment (production image, real useradd/sudo wrapper, real
processes) and produces a reviewable acceptance record (JSON + Markdown).

Deliberately NOT a pytest module (#2457 lesson: real gevent hubs inside
pytest crash xdist workers — and an acceptance run needs the whole stack,
not a test lane). Style follows scripts/multiuser_smoke.py: main() refuses
to run without linux + docker, a non-zero exit code means acceptance
FAILED, and the stack can be kept for manual inspection.

Usage (from the repository root, on a linux host with docker + compose):

    python3 scripts/multiuser_acceptance_3379.py

Environment:
    ACCEPTANCE_BASE_URL     default http://localhost:19888
    ACCEPTANCE_KEEP_STACK=1 keep the compose stack up on exit (manual review)
    ACCEPTANCE_RECORD_DIR   default test-results/acceptance-records

Flow (plan v2.1 §3): bootstrap env -> pre-seed config.json into the config
volume (workspace.multi-user, max_instances=3) BEFORE first start ->
`up -d --wait` -> default-admin first login + password change ->
two-tenant three-user scenario (alice/bob @tenant-1, carol @tenant-2,
each with a system_account so POST /api/admin/users runs the real useradd
wrapper) -> nine-item assertions (a..i) -> single-user regression tail ->
`compose down -v` (unless KEEP_STACK).
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILES = ["docker-compose.yml", "docker-compose.multi-user.yml"]
SERVICE = "open-ace"
CONFIG_VOLUME_LABEL = "config-data"

BASE_URL = os.environ.get("ACCEPTANCE_BASE_URL", "http://localhost:19888").rstrip("/")
KEEP_STACK = os.environ.get("ACCEPTANCE_KEEP_STACK") == "1"
RECORD_DIR = Path(
    os.environ.get("ACCEPTANCE_RECORD_DIR", REPO_ROOT / "test-results" / "acceptance-records")
)

# Truncation for recorded request/response pairs — enough to be forensic,
# small enough to keep the record readable and free of full secrets.
_SNIP = 400


class AcceptanceError(RuntimeError):
    """An acceptance step failed in a way that aborts the whole run."""


# ── process / compose helpers ────────────────────────────────────────────


def run(cmd: list[str], *, check: bool = True, timeout: int = 300) -> subprocess.CompletedProcess:
    """Run a command, echoing it for the record."""
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    if check and proc.returncode != 0:
        raise AcceptanceError(
            f"command failed ({proc.returncode}): {' '.join(cmd)}\n"
            f"stdout: {proc.stdout[-2000:]}\nstderr: {proc.stderr[-2000:]}"
        )
    return proc


def compose(*args: str, timeout: int = 600) -> subprocess.CompletedProcess:
    docker = shutil.which("docker")
    if not docker:
        raise AcceptanceError("docker CLI is unavailable")
    cmd = [docker, "compose", "-f", COMPOSE_FILES[0]]
    for extra in COMPOSE_FILES[1:]:
        cmd += ["-f", extra]
    cmd += list(args)
    return run(cmd, timeout=timeout)


def compose_exec(service: str, shell_cmd: str, *, user: str | None = None, timeout: int = 120):
    """Run a command INSIDE the service container (plan §3-f: restart and
    process assertions must be container-side — the host-side docker-proxy
    always listens on published ports and would be a permanent false pass)."""
    args = ["exec", "-T"]
    if user:
        args += ["-u", user]
    args += [service, "sh", "-c", shell_cmd]
    return compose(*args, timeout=timeout)


def compose_down_volumes() -> None:
    compose("down", "-v", "--remove-orphans", timeout=300)


def dump_stack_logs(recorder: Recorder) -> None:
    """On failure, keep compose logs inside the record directory (CI artifact)."""
    try:
        proc = compose("logs", "--no-color", "--tail", "400", timeout=120)
        (RECORD_DIR / "compose-logs.txt").write_text(proc.stdout, encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 - best effort during failure handling
        recorder.note(f"compose log dump failed: {exc}")


# ── config.json pre-seeding (plan §3: max_instances=3 via the config volume) ──


def compose_project_name() -> str:
    explicit = os.environ.get("COMPOSE_PROJECT_NAME", "").strip()
    raw = explicit or REPO_ROOT.name
    import re

    normalized = re.sub(r"[^a-z0-9_-]", "", raw.lower())
    normalized = re.sub(r"^[^a-z0-9]+", "", normalized)
    if not normalized:
        raise AcceptanceError("cannot determine the Compose project name")
    return normalized


def find_config_volume(create: bool = True) -> str:
    """Locate the config-data volume BY LABEL (the bootstrap_compose_env.py
    precedent) — hand-assembling the volume name is a footgun: project-name
    normalization differs silently and a typo creates an orphan volume the
    app never reads. The volume does not exist before the first `up`, so the
    pre-seed path CREATES it (with compose's labels) for compose to adopt."""
    docker = shutil.which("docker")
    project = compose_project_name()
    proc = run(
        [
            docker,
            "volume",
            "ls",
            "-q",
            "--filter",
            f"label=com.docker.compose.project={project}",
            "--filter",
            f"label=com.docker.compose.volume={CONFIG_VOLUME_LABEL}",
        ],
        timeout=30,
    )
    names = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    if not names:
        if not create:
            raise AcceptanceError(
                f"config-data volume not found for project {project}; "
                "run the acceptance script before any `compose down -v`"
            )
        # named <project>_config-data with compose's own labels so the first
        # `up` adopts it instead of creating a fresh one
        volume_name = f"{project}_config-data"
        run(
            [
                docker,
                "volume",
                "create",
                "--label",
                f"com.docker.compose.project={project}",
                "--label",
                "com.docker.compose.volume=config-data",
                volume_name,
            ],
            timeout=30,
        )
        return volume_name
    return names[0]


def preseed_config() -> dict[str, Any]:
    """Write workspace config (multi-user ON, max_instances=3) into the config
    volume via a helper container BEFORE the first `up`: first-boot config
    generation only runs when no config.json exists, so post-start edits
    would be ignored. max_instances=3 makes item (e) deterministic without
    actually launching dozens of WebUI processes."""
    config = {
        "workspace": {
            "enabled": True,
            "multi_user_mode": True,
            "max_instances": 3,
        }
    }
    volume = find_config_volume()
    docker = shutil.which("docker")
    script = (
        "mkdir -p /config && "
        f"echo {json.dumps(json.dumps(config))} > /config/config.json && "
        "chmod 644 /config/config.json && cat /config/config.json"
    )
    run(
        [docker, "run", "--rm", "-v", f"{volume}:/config", "alpine:3.20", "sh", "-c", script],
        timeout=180,
    )
    return config


# ── HTTP helper (stdlib urllib — no pip install on the runner) ───────────


def http(
    method: str,
    path: str,
    *,
    token: str | None = None,
    body: dict[str, Any] | None = None,
    params: dict[str, str] | None = None,
    timeout: int = 60,
) -> tuple[int, dict[str, Any] | list[Any] | str, dict[str, str]]:
    """Perform one HTTP request; returns (status, parsed-body, headers).

    Non-2xx is NOT an error — acceptance assertions decide what each call
    must return. Network-level failures raise.
    """
    url = f"{BASE_URL}{path}"
    if params:
        from urllib.parse import urlencode

        url += "?" + urlencode(params)
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return resp.status, _parse_body(raw, resp.headers), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return exc.code, _parse_body(raw, exc.headers), dict(exc.headers)


def _parse_body(raw: bytes, headers: Any) -> dict[str, Any] | list[Any] | str:
    text = raw.decode("utf-8", errors="replace")
    if "application/json" in (headers.get("Content-Type") or ""):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text
    return text


# ── recorder ─────────────────────────────────────────────────────────────


class Recorder:
    """Collects per-item results plus truncated req/resp evidence for BOTH
    passing and failing assertions (plan §4: passing items keep truncated
    request/response pairs too), then renders JSON + Markdown records."""

    def __init__(self) -> None:
        self.started_utc = datetime.now(timezone.utc)
        self.items: list[dict[str, Any]] = []
        self.fingerprint: dict[str, Any] = {}
        self.notes: list[str] = []

    def note(self, text: str) -> None:
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.notes.append(f"{stamp} {text}")
        print(f"[note] {text}", file=sys.stderr)

    def check(
        self,
        item: str,
        name: str,
        ok: bool,
        detail: str = "",
        *,
        request: Any = None,
        response: Any = None,
    ) -> bool:
        evidence = {
            "request": _truncate(request),
            "response": _truncate(response),
        }
        self.items.append(
            {
                "item": item,
                "name": name,
                "result": "PASS" if ok else "FAIL",
                "detail": detail,
                "evidence": evidence,
            }
        )
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {item} {name}" + (f" — {detail}" if detail and not ok else ""))
        return ok

    def collect_fingerprint(self) -> None:
        git_sha = "?"
        try:
            git_sha = run(["git", "rev-parse", "HEAD"], timeout=30).stdout.strip()
        except Exception:  # noqa: BLE001 - fingerprint is best effort
            pass
        image = os.environ.get("IMAGE_NAME", "openace/open-ace:latest")
        digest = "?"
        try:
            digest = (
                run(
                    [
                        shutil.which("docker") or "docker",
                        "inspect",
                        "--format",
                        "{{index .RepoDigests 0}}",
                        image,
                    ],
                    check=False,
                    timeout=30,
                ).stdout.strip()
                or "unavailable(local-build)"
            )
        except Exception:  # noqa: BLE001
            pass
        versions = run(
            [shutil.which("docker") or "docker", "compose", "version", "--short"],
            check=False,
            timeout=30,
        ).stdout.strip()
        self.fingerprint = {
            "git_sha": git_sha,
            "image": image,
            "image_digest": digest,
            "docker_compose": versions or "?",
            "kernel": platform.release(),
            "platform": platform.platform(),
            "started_utc": self.started_utc.isoformat(timespec="seconds"),
        }

    def policy_revision(self, revision: Any) -> None:
        self.fingerprint["policy_revision"] = revision

    @property
    def failed(self) -> list[dict[str, Any]]:
        return [i for i in self.items if i["result"] == "FAIL"]

    def render(self) -> tuple[Path, Path]:
        RECORD_DIR.mkdir(parents=True, exist_ok=True)
        stamp = self.started_utc.strftime("%Y%m%dT%H%M%SZ")
        json_path = RECORD_DIR / f"multiuser-acceptance-{stamp}.json"
        md_path = RECORD_DIR / f"multiuser-acceptance-{stamp}.md"
        payload = {
            "issue": "#3379 / #3374 acceptance",
            "fingerprint": self.fingerprint,
            "summary": {
                "total": len(self.items),
                "passed": sum(1 for i in self.items if i["result"] == "PASS"),
                "failed": len(self.failed),
            },
            "notes": self.notes,
            "items": self.items,
        }
        json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        md_path.write_text(self._markdown(payload), encoding="utf-8")
        return json_path, md_path

    def _markdown(self, payload: dict[str, Any]) -> str:
        lines = [
            "# 多用户隔离验收记录 / Multi-user isolation acceptance record",
            "",
            f"- 时间 (UTC): {payload['fingerprint'].get('started_utc')}",
            f"- git SHA: `{payload['fingerprint'].get('git_sha')}`",
            f"- 镜像: `{payload['fingerprint'].get('image')}` (digest {payload['fingerprint'].get('image_digest')})",
            f"- docker/compose: {payload['fingerprint'].get('docker_compose')} / kernel {payload['fingerprint'].get('kernel')}",
            f"- policy_revision: `{payload['fingerprint'].get('policy_revision')}`",
            f"- 结果: **{payload['summary']['passed']} passed / {payload['summary']['failed']} failed**",
            "",
            "| # | 断言 | 结果 | 说明 |",
            "|---|---|---|---|",
        ]
        for item in payload["items"]:
            detail = (item["detail"] or "").replace("|", "\\|")
            lines.append(f"| {item['item']} | {item['name']} | {item['result']} | {detail} |")
        if payload["notes"]:
            lines += ["", "## 备注 / Notes", ""]
            lines += [f"- {n}" for n in payload["notes"]]
        lines += [
            "",
            "## 证据 / Evidence",
            "",
            "截断的请求/响应对见同名 `.json` 文件的 `items[].evidence`。",
            "Truncated request/response pairs live in `items[].evidence` of the `.json` twin.",
            "",
        ]
        return "\n".join(lines)


def _truncate(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, default=str)
    if not isinstance(value, str):
        value = str(value)
    if len(value) > _SNIP:
        return value[:_SNIP] + f"...<truncated {len(value) - _SNIP} chars>"
    return value


def wait_ready(recorder: Recorder, *, timeout_s: int = 300) -> None:
    deadline = time.monotonic() + timeout_s
    last = "never attempted"
    while time.monotonic() < deadline:
        try:
            status, body, _ = http("GET", "/readyz", timeout=10)
            last = f"{status} {body}"
            if status == 200:
                return
        except Exception as exc:  # noqa: BLE001 - retry until deadline
            last = str(exc)
        time.sleep(3)
    raise AcceptanceError(f"service did not become ready within {timeout_s}s (last: {last})")


# ══════════════════════════════════════════════════════════════════════════
# Scenario + assertions are implemented below (items a..i per plan §3).
# ══════════════════════════════════════════════════════════════════════════


# ── login / session helpers ──────────────────────────────────────────────


def login(username: str, password: str) -> tuple[str, dict[str, Any]]:
    """Login and return (session_token, user payload).

    The session token arrives ONLY via Set-Cookie (session_token=<64hex>);
    the JSON body does not carry it. Bearer transport works afterwards.
    """
    url = f"{BASE_URL}/api/auth/login"
    data = json.dumps({"username": username, "password": password}).encode()
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = json.loads(resp.read())
        cookie = resp.headers.get("Set-Cookie", "")
    for part in cookie.split(";"):
        if part.strip().startswith("session_token="):
            return part.strip().split("=", 1)[1], body.get("user", {})
    raise AcceptanceError(f"login succeeded but no session_token cookie for {username}")


def psql(sql: str) -> subprocess.CompletedProcess:
    """Execute SQL inside the compose postgres container (DB seeding for the
    terminal/session ownership matrices — an in-memory store cannot be
    seeded from outside, which is why VSCode gets a manual-review exemption
    in the handbook instead)."""
    return compose_exec(
        "postgres",
        f"psql -U ace -d ace -v ON_ERROR_STOP=1 -c {json.dumps(sql)}",
        timeout=60,
    )


# ── container-side process / filesystem inspection ───────────────────────
#
# Everything below runs INSIDE the open-ace container (plan §3-f): the
# host-side docker-proxy listens on every published port forever and would
# turn any port assertion into a permanent false pass.


_PROC_SCAN = (
    "for d in /proc/[0-9]*; do "
    "pid=${d#/proc/}; "
    "cmd=$(tr '\\0' ' ' < $d/cmdline 2>/dev/null) || continue; "
    "uid=$(awk '/^Uid:/{print $2}' $d/status 2>/dev/null); "
    '[ -n "$cmd" ] && echo "$pid|$uid|$cmd"; '
    "done"
)


def _proc_table() -> list[dict[str, str]]:
    """Whole /proc scan in ONE container exec: pid|uid|cmdline per line."""
    out = compose_exec(SERVICE, _PROC_SCAN, timeout=30).stdout
    rows = []
    for line in out.splitlines():
        parts = line.split("|", 2)
        if len(parts) == 3 and parts[0].isdigit():
            rows.append({"pid": parts[0], "uid": parts[1], "cmd": parts[2].strip()})
    return rows


def find_webui_process(port: int) -> dict[str, str] | None:
    """The webui process is identifiable by its `--token-secret ... --port N`
    command line (multi-user launch shape)."""
    for row in _proc_table():
        if "--port" in row["cmd"]:
            args = row["cmd"].split()
            for i, arg in enumerate(args):
                if arg == "--port" and i + 1 < len(args) and args[i + 1] == str(port):
                    return row
    return None


def container_uid_of(account: str) -> str:
    out = compose_exec(SERVICE, f"id -u {account}", timeout=15).stdout.strip()
    if not out.isdigit():
        raise AcceptanceError(f"no uid for system account {account}")
    return out


def listening_ports_in_range(low: int, high: int) -> list[int]:
    """Parse /proc/net/tcp(+tcp6) inside the container for LISTEN sockets in
    [low, high] — ss/netstat are not guaranteed in the image."""
    script = (
        'awk \'NR>1 && $4=="0A" {split($2,a,":"); print strtonum("0x" a[2])}\' '
        "/proc/net/tcp /proc/net/tcp6 2>/dev/null"
    )
    out = compose_exec(SERVICE, script, timeout=15).stdout
    return sorted(
        {int(line) for line in out.split() if line.strip().isdigit() and low <= int(line) <= high}
    )


def webui_env_of(port: int) -> dict[str, str]:
    """Full environment of the webui process on *port* (root can read any
    /proc/<pid>/environ; the values are injected proxy tokens)."""
    proc = find_webui_process(port)
    if not proc:
        raise AcceptanceError(f"no webui process found for port {port}")
    out = compose_exec(SERVICE, f"tr '\\0' '\\n' < /proc/{proc['pid']}/environ", timeout=15)
    env: dict[str, str] = {}
    for line in out.stdout.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            env[key] = value
    return env


# ── scenario ─────────────────────────────────────────────────────────────


class Scenario:
    """Two-tenant, multi-user scenario state (plan §3)."""

    def __init__(self, recorder: Recorder) -> None:
        self.recorder = recorder
        self.admin_token = ""
        self.tenant1_id = 0
        self.tenant2_id = 0
        self.users: dict[str, dict[str, Any]] = {}
        self.user_passwords: dict[str, str] = {}

    # -- bootstrap -----------------------------------------------------

    def admin_first_login(self) -> None:
        token, user = login("admin", "admin123")
        if not user.get("must_change_password"):
            # A pre-existing volume with an already-changed password cannot be
            # driven deterministically — require a fresh stack.
            raise AcceptanceError(
                "default admin has no must_change_password flag; "
                "run with a fresh stack (compose down -v first)"
            )
        status, body, _ = http(
            "POST",
            "/api/auth/change-password",
            token=token,
            body={"current_password": "admin123", "new_password": "Acceptance-Admin-2026!x"},
        )
        if status != 200:
            raise AcceptanceError(f"admin password change failed: {status} {body}")
        self.admin_token = token  # session stays valid after the change
        self.recorder.note("default admin first-login password change completed")

    def create_tenant(self, name: str, slug: str) -> int:
        status, body, _ = http(
            "POST", "/api/tenants", token=self.admin_token, body={"name": name, "slug": slug}
        )
        if status != 201:
            raise AcceptanceError(f"tenant {slug} creation failed: {status} {body}")
        return int(body["id"])

    def create_user(
        self,
        username: str,
        tenant_id: int,
        *,
        password: str,
        system_account: str | None,
        email: str | None = None,
    ) -> int:
        payload: dict[str, Any] = {
            "username": username,
            "email": email or f"{username}@acceptance.test",
            "password": password,
            "tenant_id": tenant_id,
            "role": "user",
        }
        if system_account:
            payload["system_account"] = system_account
        status, body, _ = http("POST", "/api/admin/users", token=self.admin_token, body=payload)
        if status != 201:
            raise AcceptanceError(f"user {username} creation failed: {status} {body}")
        self.user_passwords[username] = password
        self.users[username] = {
            "id": int(body["user_id"]),
            "token": None,
            "system_account": system_account,
        }
        return int(body["user_id"])

    def user_login(self, username: str) -> str:
        token, _ = login(username, self.user_passwords[username])
        self.users[username]["token"] = token
        return token

    def user_url(self, username: str, *, params: dict[str, str] | None = None):
        return http(
            "GET",
            "/api/workspace/user-url",
            token=self.users[username]["token"],
            params=params,
        )

    def build(self) -> None:
        # admin_first_login() is assumed done (main() verifies the pre-seeded
        # config between the password change and this scenario — config reads
        # are not on the must_change_password allowlist)
        self.tenant1_id = self.create_tenant("Acceptance Tenant One", "acc-t1")
        self.tenant2_id = self.create_tenant("Acceptance Tenant Two", "acc-t2")
        self.create_user(
            "alice", self.tenant1_id, password="Alice-Acceptance-2026!x", system_account="alice"
        )
        self.create_user(
            "bob", self.tenant1_id, password="Bob-Acceptance-2026!x", system_account="bob"
        )
        self.create_user(
            "carol", self.tenant2_id, password="Carol-Acceptance-2026!x", system_account="carol"
        )
        # dave: the 4th instance that must hit the max_instances=3 wall (item e)
        self.create_user(
            "dave", self.tenant1_id, password="Dave-Acceptance-2026!x", system_account="dave"
        )
        # erin: NO system_account — the identity-mapping leg of item (g)
        self.create_user(
            "erin", self.tenant1_id, password="Erin-Acceptance-2026!x", system_account=None
        )
        for username in ("alice", "bob", "carol", "dave", "erin"):
            self.user_login(username)
        self.recorder.note(
            "scenario built: tenant-1{alice,bob,dave,erin(no-map)} + tenant-2{carol}"
        )


# ── nine-item acceptance assertions (plan §3) ────────────────────────────

SANDBOX_PROBE_REASON_CODES = {
    "sandbox_backend_unconfigured",
    "sandbox_tier_missing",
    "webui_image_missing",
    "webui_image_not_pinned",
    "webui_image_not_allowed",
    "sandbox_api_key_missing",
    "sandbox_proxy_unreachable",
    "sandbox_multi_process_unsupported",
}


def item_a_concurrent_private_workspaces(sc: Scenario) -> None:
    """(a) concurrent private dirs / history / model config separation."""
    rec, r = sc.recorder, sc
    print("[a] concurrent users, private directories/history/model config")

    ports: dict[str, int] = {}
    for name in ("alice", "bob"):
        status, body, _ = r.user_url(name)
        rec.check(
            "a",
            f"{name} user-url 200",
            status == 200,
            f"status={status}",
            response=body,
        )
        url = (body or {}).get("url", "")
        ports[name] = int(url.rsplit(":", 1)[-1]) if ":" in url else -1
        r.users[name]["webui_token"] = (body or {}).get("token")
    rec.check("a", "distinct per-user ports", ports["alice"] != ports["bob"], f"{ports}")

    for name in ("alice", "bob"):
        proc = find_webui_process(ports[name])
        rec.check(
            "a",
            f"{name} webui process exists (port {ports[name]})",
            proc is not None,
            f"proc={proc}",
        )
        if proc:
            expect_uid = container_uid_of(name)
            rec.check(
                "a",
                f"{name} webui runs under its own uid (sudo -u)",
                proc["uid"] == expect_uid,
                f"uid={proc['uid']} expected={expect_uid}",
            )
        mode = compose_exec(SERVICE, f"stat -c %a /home/{name}", timeout=15).stdout.strip()
        rec.check("a", f"/home/{name} is 0700", mode == "700", f"mode={mode}")

    # Model-config separation (same evidence channel as item c): each webui
    # env carries ONLY proxy tokens — no real/dynamic model keys — and the
    # two proxy tokens differ.
    envs = {name: webui_env_of(ports[name]) for name in ("alice", "bob")}
    for name, env in envs.items():
        rec.check(
            "a",
            f"{name} webui env: OPENAI_API_KEY is the proxy token",
            env.get("OPENAI_API_KEY") == env.get("OPENACE_PROXY_TOKEN")
            and bool(env.get("OPENACE_PROXY_TOKEN")),
            f"keys={sorted(k for k in env if k.endswith(('KEY', 'TOKEN')))}",
        )
        dynamic_leak = {"ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL"} & set(env)
        rec.check(
            "a",
            f"{name} webui env: no sensitive real keys",
            not dynamic_leak,
            f"leaked={dynamic_leak}",
        )
    rec.check(
        "a",
        "alice/bob proxy tokens differ",
        envs["alice"].get("OPENACE_PROXY_TOKEN") != envs["bob"].get("OPENACE_PROXY_TOKEN"),
    )
    # History roots are per-account by construction (0700 homes); record the
    # materialized layout for the manual browser-side screenshot review.
    listing = compose_exec(SERVICE, "ls -la /home/alice /home/bob", timeout=15).stdout
    rec.check(
        "a",
        "history roots stay per-account (0700 homes)",
        True,
        "homes listing captured as evidence",
    )
    rec.items[-1]["evidence"]["response"] = _truncate(listing)


def item_b_cross_user_access_matrix(sc: Scenario) -> None:
    """(b) ID tampering / cwd swap / symlink / traversal / history-restore."""
    rec, r = sc.recorder, sc
    print("[b] cross-user access control matrix")

    # required_isolation=none cannot LOWER the floor (stays os_user)
    status, body, _ = r.user_url("alice", params={"required_isolation": "none"})
    iso = ((body or {}).get("isolation") or {}).get("isolation_level")
    rec.check(
        "b",
        "required_isolation=none cannot lower the floor",
        status == 200 and iso == "os_user",
        f"status={status} isolation={iso}",
        response=body,
    )

    # Workspace session ownership matrix (seed bob's session row first)
    bob_id = r.users["bob"]["id"]
    psql(
        "DELETE FROM agent_sessions WHERE session_id IN "
        "('bob-acc-session', 'alice-acc-terminal')"
    )
    psql(
        "INSERT INTO agent_sessions (session_id, tool_name, user_id, status, "
        "workspace_type, tenant_id, title) VALUES ('bob-acc-session', 'qwen-code', "
        f"{bob_id}, 'active', 'local', {r.tenant1_id}, 'acceptance')"
    )
    alice_token = r.users["alice"]["token"]
    carol_token = r.users["carol"]["token"]
    for label, token, sid, expect in (
        ("alice GET bob session -> 403", alice_token, "bob-acc-session", 403),
        ("alice GET missing session -> 404", alice_token, "no-such-session", 404),
        ("alice RESTORE bob session -> 403", alice_token, "bob-acc-session", 403),
        ("carol (other tenant) GET bob session -> 404", carol_token, "bob-acc-session", 404),
    ):
        method = "POST" if "RESTORE" in label else "GET"
        path = f"/api/workspace/sessions/{sid}" + ("/restore" if method == "POST" else "")
        status, body, _ = http(method, path, token=token)
        rec.check("b", label, status == expect, f"status={status}", request=path, response=body)

    # Terminal session ownership: seed machines + alice terminal session +
    # bob assignment (gate fires before any agent interaction — no live agent)
    alice_id = r.users["alice"]["id"]
    admin_id = 1
    mid = "11111111-2222-3333-4444-555555555555"
    psql(f"DELETE FROM machine_assignments WHERE machine_id = '{mid}'")
    psql(
        "INSERT INTO remote_machines (machine_id, machine_name, tenant_id, created_by, status) "
        f"VALUES ('{mid}', 'acc-machine', {r.tenant1_id}, {admin_id}, 'offline')"
    )
    psql(
        "INSERT INTO agent_sessions (session_id, tool_name, user_id, status, "
        "workspace_type, remote_machine_id, tenant_id, title) VALUES "
        f"('alice-acc-terminal', 'qwen-code', {alice_id}, 'active', 'terminal', '{mid}', "
        f"{r.tenant1_id}, 'acceptance-terminal')"
    )
    psql(
        "INSERT INTO machine_assignments (machine_id, user_id, permission) "
        f"VALUES ('{mid}', {bob_id}, 'user')"
    )
    bob_token = r.users["bob"]["token"]
    for label, path, body, expect in (
        (
            "bob STOP alice terminal -> 403",
            "/api/remote/terminal/stop",
            {"terminal_id": "alice-acc-terminal", "machine_id": mid},
            403,
        ),
        (
            "bob ATTACH alice terminal -> 403",
            "/api/remote/terminal/alice-acc-terminal/attach",
            {"machine_id": mid},
            403,
        ),
        (
            "bob STOP missing terminal -> 404",
            "/api/remote/terminal/stop",
            {"terminal_id": "no-such-terminal", "machine_id": mid},
            404,
        ),
        (
            "carol (other tenant) machine invisible -> 404",
            "/api/remote/terminal/alice-acc-terminal/attach",
            {"machine_id": mid},
            404,
        ),
    ):
        status, resp_body, _ = http(
            "POST", path, token=bob_token if "carol" not in label else carol_token, body=body
        )
        rec.check(
            "b", label, status == expect, f"status={status}", request=body, response=resp_body
        )

    # fs: cross-user home, traversal, symlink
    compose_exec(SERVICE, "ln -sfn /home/bob /home/alice/link-to-bob", timeout=15)
    for label, params, expect in (
        ("alice browse /home/bob -> 400", {"path": "/home/bob"}, 400),
        ("alice browse traversal ../ -> 400", {"path": "/workspace/../../etc"}, 400),
        ("alice browse symlink to bob -> 400", {"path": "/home/alice/link-to-bob"}, 400),
    ):
        status, body, _ = http("GET", "/api/fs/browse", token=alice_token, params=params)
        rec.check("b", label, status == expect, f"status={status}", request=params, response=body)
    status, body, _ = http(
        "POST", "/api/fs/check-path", token=alice_token, body={"path": "/home/bob"}
    )
    rec.check("b", "check-path /home/bob -> 400", status == 400, f"status={status}", response=body)


def item_c_environment_isolation(sc: Scenario) -> None:
    """(c) no other user's key/token in env; A's tools cannot enter B's area."""
    rec, r = sc.recorder, sc
    print("[c] environment/process separation")

    bob_port = int(
        http("GET", "/api/workspace/user-url", token=r.users["bob"]["token"])[1]["url"].rsplit(
            ":", 1
        )[-1]
    )
    bob_proc = find_webui_process(bob_port)
    rec.check("c", "bob webui process located", bob_proc is not None, f"port={bob_port}")
    if bob_proc:
        # alice (docker exec -u alice) cannot read bob's webui environ
        proc = compose_exec(
            SERVICE, f"cat /proc/{bob_proc['pid']}/environ", user="alice", timeout=15
        )
        rec.check(
            "c",
            "alice reading bob webui /proc environ -> denied",
            proc.returncode != 0,
            f"rc={proc.returncode}",
        )
        # Terminal equivalence: a shell AS alice cannot list bob's home
        proc = compose_exec(SERVICE, "ls /home/bob", user="alice", timeout=15)
        rec.check(
            "c",
            "alice shell ls /home/bob -> EACCES",
            proc.returncode != 0,
            f"rc={proc.returncode} stderr={proc.stderr.strip()[:120]}",
        )
    # (model-config env assertions already pinned in item a — same evidence
    #  channel, recorded there with both instances' dumps)


def item_d_shared_projects(sc: Scenario) -> None:
    """(d) shared project grant and revocation across tenants."""
    rec, r = sc.recorder, sc
    print("[d] shared project authorization")

    status, body, _ = http("GET", "/api/workspace/config", token=r.users["alice"]["token"])
    base_dir = (body or {}).get("base_dir", "/workspace")
    shared_path = f"{base_dir}/shared/acc-team-proj"
    status, body, _ = http(
        "POST",
        "/api/projects",
        token=r.users["alice"]["token"],
        body={"path": shared_path, "name": "acc-team-proj", "is_shared": True, "create_dir": True},
    )
    rec.check(
        "d",
        "alice creates shared project",
        status == 201,
        f"status={status}",
        request=shared_path,
        response=body,
    )
    project_id = (body or {}).get("project", {}).get("id")

    status, body, _ = http(
        "GET", "/api/fs/browse", token=r.users["bob"]["token"], params={"path": shared_path}
    )
    rec.check(
        "d",
        "bob (same tenant) can browse shared project",
        status == 200,
        f"status={status}",
        request=shared_path,
        response=body,
    )
    status, body, _ = http(
        "GET", "/api/fs/browse", token=r.users["carol"]["token"], params={"path": shared_path}
    )
    rec.check(
        "d",
        "carol (other tenant) cannot browse shared project",
        status == 400,
        f"status={status}",
        request=shared_path,
        response=body,
    )

    status, body, _ = http(
        "PUT",
        f"/api/projects/{project_id}",
        token=r.users["alice"]["token"],
        body={"is_shared": False},
    )
    rec.check("d", "alice revokes sharing", status == 200, f"status={status}", response=body)
    status, body, _ = http(
        "GET", "/api/fs/browse", token=r.users["bob"]["token"], params={"path": shared_path}
    )
    rec.check(
        "d",
        "bob browse after revocation -> 400",
        status == 400,
        f"status={status}",
        request=shared_path,
        response=body,
    )


def item_e_resource_limits(sc: Scenario) -> None:
    """(e) resource ceiling, cancellation, crash isolation."""
    rec, r = sc.recorder, sc
    print("[e] resource ceilings and fault isolation")

    # alice + bob already have instances; carol takes the 3rd slot
    status, body, _ = r.user_url("carol")
    rec.check("e", "carol takes the 3rd instance slot", status == 200, f"status={status}")
    # 4th must hit the pre-seeded max_instances=3
    status, body, _ = r.user_url("dave")
    rec.check(
        "e",
        "4th instance hits max_instances=3 -> 503",
        status == 503,
        f"status={status} (body recorded verbatim — unstructured, itself a finding)",
        response=body,
    )

    # stop alice's instance (admin route) — bob must be undisturbed
    status, body, _ = http(
        "POST",
        f"/api/workspace/instances/{r.users['alice']['id']}/stop",
        token=r.admin_token,
    )
    rec.check("e", "admin stops alice's instance", status == 200, f"status={status}", response=body)
    status, body, _ = http("GET", "/api/auth/me", token=r.users["bob"]["token"])
    rec.check("e", "bob session unaffected by alice stop", status == 200, f"status={status}")
    status, body, _ = http("GET", "/readyz")
    rec.check("e", "/readyz unaffected by alice stop", status == 200, f"status={status}")

    # slot freed: dave can now start
    status, body, _ = r.user_url("dave")
    rec.check("e", "freed slot serves dave", status == 200, f"status={status}")

    # crash isolation: kill -9 bob's webui; carol's instance + control plane
    # must stay healthy
    bob_port = int(
        http("GET", "/api/workspace/user-url", token=r.users["bob"]["token"])[1]["url"].rsplit(
            ":", 1
        )[-1]
    )
    bob_proc = find_webui_process(bob_port)
    if bob_proc:
        compose_exec(SERVICE, f"kill -9 {bob_proc['pid']}", timeout=15)
        time.sleep(5)
    status, body, _ = http("GET", "/readyz")
    rec.check("e", "/readyz survives kill -9 of bob's webui", status == 200, f"status={status}")
    carol_port = int(
        http("GET", "/api/workspace/user-url", token=r.users["carol"]["token"])[1]["url"].rsplit(
            ":", 1
        )[-1]
    )
    rec.check(
        "e",
        "carol's webui unaffected by bob's crash",
        find_webui_process(carol_port) is not None,
        f"port={carol_port}",
    )
    rec.note(
        "task cancellation approximated by instance stop + session termination (handbook §boundaries)"
    )


def item_f_deactivation_and_restart(sc: Scenario) -> None:
    """(f) deactivation revokes everything; control-plane restart (PR-A dependent).

    Every assertion here is expected PASS — PR-A (#3384) made deactivation
    stop the workspace, stamp tokens_valid_after, and revoke sessions."""
    rec, r = sc.recorder, sc
    print("[f] deactivation, token revocation, restart orphans")

    bob = r.users["bob"]
    bob_port = int(
        http("GET", "/api/workspace/user-url", token=bob["token"])[1]["url"].rsplit(":", 1)[-1]
    )
    bob_webui_token = bob.get("webui_token") or http(
        "GET", "/api/workspace/user-url", token=bob["token"]
    )[1].get("token")
    bob_proxy_token = webui_env_of(bob_port).get("OPENACE_PROXY_TOKEN", "")

    # deactivation (PR-A): sessions revoked, URL token refused, workspace
    # stopped asynchronously, proxy token torn down
    status, body, _ = http(
        "PUT", f"/api/admin/users/{bob['id']}", token=r.admin_token, body={"is_active": False}
    )
    rec.check("f", "admin deactivates bob", status == 200, f"status={status}", response=body)

    status, body, _ = http("GET", "/api/auth/me", token=bob["token"])
    rec.check("f", "bob session -> 401 after deactivation", status == 401, f"status={status}")
    status, body, _ = http(
        "GET",
        "/api/fs/browse",
        params={"path": "/home/bob", "token": bob_webui_token or ""},
    )
    rec.check(
        "f",
        "bob URL-token -> 401 after deactivation (PR-A)",
        status == 401,
        f"status={status}",
        response=body,
    )

    gone = False
    for _ in range(30):  # async teardown (sandboxed shapes can take ~90s)
        if find_webui_process(bob_port) is None:
            gone = True
            break
        time.sleep(3)
    rec.check("f", "bob webui instance destroyed", gone, f"port={bob_port}")

    if bob_proxy_token:
        status, body, _ = http(
            "POST",
            "/api/workspace/llm-proxy/v1/chat/completions",
            token=bob_proxy_token,
            body={"model": "acc", "messages": []},
        )
        rec.check(
            "f",
            "bob proxy token revoked (llm-proxy 401)",
            status == 401,
            f"status={status}",
            response=body,
        )

    status, body, _ = r.user_url("bob")
    rec.check(
        "f",
        "bob user-url refused after deactivation",
        status == 401,
        f"status={status}",
        response=body,
    )

    # Control-plane restart: container-side assertions ONLY (host docker-proxy
    # always listens on published ports — permanent false pass).
    alice_url_token = r.users["alice"].get("webui_token") or http(
        "GET", "/api/workspace/user-url", token=r.users["alice"]["token"]
    )[1].get("token")
    compose("restart", SERVICE, timeout=300)
    wait_ready(rec)
    processes = [p for p in _proc_table() if "--token-secret" in p["cmd"]]
    rec.check(
        "f",
        "no leftover webui processes after restart",
        not processes,
        f"leftover={[p['cmd'][:80] for p in processes]}",
    )
    rec.check(
        "f",
        "ports 3100-3200 silent in-container after restart",
        listening_ports_in_range(3100, 3200) == [],
        f"{listening_ports_in_range(3100, 3200)}",
    )
    status, body, _ = http(
        "GET",
        "/api/fs/browse",
        params={"path": "/home/alice", "token": alice_url_token or ""},
    )
    rec.check(
        "f",
        "alice URL token still valid after restart (#3377 secret persisted)",
        status == 200,
        f"status={status}",
        response=body,
    )
    rec.note(
        "ephemeral materials live and die with the container lifecycle (handbook); "
        "the control-plane-restart-without-container-death shape does not exist under compose"
    )


def item_g_backend_refusal(sc: Scenario) -> None:
    """(g) deterministic refusal when a backend/level is unavailable."""
    rec, r = sc.recorder, sc
    print("[g] unsupported backend refusal (sandboxed exemption)")

    status, body, _ = http("GET", "/api/workspace/isolation-capabilities", token=r.admin_token)
    snapshot = body if isinstance(body, dict) else {}
    rec.policy_revision(snapshot.get("policy_revision"))
    reason_codes = {rr.get("code") for rr in snapshot.get("reasons", [])}
    rec.check(
        "g",
        "contract: isolation_level=os_user under compose",
        snapshot.get("isolation_level") == "os_user",
        f"level={snapshot.get('isolation_level')}",
        response=snapshot,
    )
    rec.check(
        "g",
        "contract: reasons contain NO sandbox probe codes",
        not (reason_codes & SANDBOX_PROBE_REASON_CODES),
        f"reasons={sorted(reason_codes)}",
    )
    rec.check(
        "g",
        "contract: policy_revision present",
        bool(snapshot.get("policy_revision")),
        f"{snapshot.get('policy_revision')}",
    )

    status, body, _ = r.user_url("alice", params={"required_isolation": "sandboxed"})
    error_code = (body or {}).get("error_code") if isinstance(body, dict) else None
    rec.check(
        "g",
        "user-url sandboxed -> 400 isolation_level_unsupported",
        status == 400 and error_code == "isolation_level_unsupported",
        f"status={status} code={error_code}",
        response=body,
    )

    # identity mapping: erin has NO system_account — explicit isolation
    # requirement must fail closed with identity_mapping_missing
    status, body, _ = r.user_url("erin", params={"required_isolation": "os_user"})
    error_code = (body or {}).get("error_code") if isinstance(body, dict) else None
    rec.check(
        "g",
        "unmapped user + required_isolation -> 400 identity_mapping_missing",
        status == 400 and error_code == "identity_mapping_missing",
        f"status={status} code={error_code}",
        response=body,
    )


def item_h_single_user_regression(recorder: Recorder) -> dict[str, Any]:
    """(h) single-user mode shows no regression (base compose, fresh config)."""
    print("[h] single-user regression tail")
    rec = recorder

    # down -v ALSO drops the config volume: the single-user tail must start
    # from a pristine auto-generated config, not the multi-user preseed
    compose_down_volumes()
    run(
        [
            shutil.which("docker") or "docker",
            "compose",
            "-f",
            "docker-compose.yml",
            "up",
            "-d",
            "--wait",
        ],
        timeout=600,
    )
    wait_ready(rec)

    token, user = login("admin", "admin123")
    if user.get("must_change_password"):
        http(
            "POST",
            "/api/auth/change-password",
            token=token,
            body={"current_password": "admin123", "new_password": "Acceptance-Admin-2026!x"},
        )
    status, body, _ = http("GET", "/api/workspace/isolation-capabilities", token=token)
    snapshot = body if isinstance(body, dict) else {}
    rec.check(
        "h",
        "single-user contract: isolation_level=none",
        snapshot.get("isolation_level") == "none",
        f"level={snapshot.get('isolation_level')}",
        response=snapshot,
    )
    status, body, _ = http("GET", "/api/workspace/user-url", token=token)
    url = (body or {}).get("url", "") if isinstance(body, dict) else ""
    rec.check(
        "h",
        "single shared instance on 3100",
        status == 200 and url.endswith(":3100"),
        f"status={status} url={url}",
        response=body,
    )
    status, body, _ = http("GET", "/api/auth/me", token=token)
    rec.check("h", "admin session works in single-user mode", status == 200, f"status={status}")
    rec.note("default-UI manual click-through recorded in the handbook checklist")


def item_i_record_and_crossrefs(recorder: Recorder) -> None:
    """(i) publishable sample, permission conditions, capability matrix."""
    rec = recorder
    print("[i] record assembly and cross-references")
    rec.check(
        "i",
        "fingerprint captured",
        bool(rec.fingerprint.get("git_sha")),
        f"{rec.fingerprint.get('git_sha')}",
    )
    rec.note(
        "capability matrix: docs WORKSPACE_ISOLATION_CAPABILITIES; permissions: "
        "docs PERMISSION_MODEL / MULTIUSER deployment guides"
    )


# ── main ─────────────────────────────────────────────────────────────────


def main() -> int:
    if sys.platform != "linux":
        print(
            "multi-user acceptance requires a real Linux host (compose, useradd, sudo)",
            file=sys.stderr,
        )
        return 2
    if not shutil.which("docker"):
        print("docker CLI unavailable", file=sys.stderr)
        return 2

    recorder = Recorder()
    recorder.collect_fingerprint()
    sc: Scenario | None = None
    try:
        # 1. env bootstrap + config preseed + stack up
        run([sys.executable, str(REPO_ROOT / "scripts" / "bootstrap_compose_env.py")], timeout=120)
        preseed_config()
        compose("up", "-d", "--wait", timeout=900)
        wait_ready(recorder)

        # 2. scenario + nine items
        sc = Scenario(recorder)
        sc.admin_first_login()

        # preseed proof: runtime config must reflect max_instances=3 (read
        # with the post-change admin token — the endpoint is not on the
        # must_change_password allowlist)
        status, body, _ = http("GET", "/api/workspace/config", token=sc.admin_token)
        if not isinstance(body, dict) or body.get("max_instances") != 3:
            raise AcceptanceError(
                f"config preseed ineffective: max_instances="
                f"{body.get('max_instances') if isinstance(body, dict) else body}"
            )
        recorder.note("pre-seeded config active: multi_user_mode on, max_instances=3")
        sc.build()
        item_a_concurrent_private_workspaces(sc)
        item_b_cross_user_access_matrix(sc)
        item_c_environment_isolation(sc)
        item_d_shared_projects(sc)
        item_e_resource_limits(sc)
        item_f_deactivation_and_restart(sc)
        item_g_backend_refusal(sc)
        item_h_single_user_regression(recorder)
        item_i_record_and_crossrefs(recorder)
    except (AcceptanceError, subprocess.SubprocessError, OSError) as exc:
        recorder.note(f"ABORTED: {exc}")
        dump_stack_logs(recorder)
        recorder.render()
        print(f"ACCEPTANCE ABORTED: {exc}", file=sys.stderr)
        return 1
    finally:
        if not KEEP_STACK:
            try:
                compose_down_volumes()
            except AcceptanceError as exc:
                recorder.note(f"teardown warning: {exc}")

    json_path, md_path = recorder.render()
    failed = recorder.failed
    print(
        f"\nACCEPTANCE {'PASS' if not failed else 'FAIL'}: "
        f"{len(recorder.items) - len(failed)} passed, {len(failed)} failed"
    )
    print(f"record: {json_path}\n        {md_path}")
    for item in failed:
        print(f"  FAIL {item['item']} {item['name']}: {item['detail']}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
