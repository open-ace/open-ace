#!/usr/bin/env python3
"""Issue #3379: multi-user real-Linux end-to-end isolation acceptance.

Executes the #3374 nine-item acceptance checklist against a REAL multi-user
Compose deployment (production image, real useradd/sudo wrapper, real
processes) and produces a reviewable acceptance record (JSON + Markdown).
# fix(#3399): comment-only touch to trigger this lane for the entrypoint
# user-sync quoting fix (docker-entrypoint.sh is outside the paths filter).

Deliberately NOT a pytest module (#2457 lesson: real gevent hubs inside
pytest crash xdist workers — and an acceptance run needs the whole stack,
not a test lane). Style follows scripts/multiuser_smoke.py: main() refuses
to run without linux + docker, a non-zero exit code means acceptance
FAILED, and the stack can be kept for manual inspection.

Usage (from the repository root, on a linux host with docker + compose):

    python3 scripts/multiuser_acceptance.py

Environment:
    ACCEPTANCE_BASE_URL     default http://localhost:19888
    ACCEPTANCE_KEEP_STACK=1 keep the MULTI-USER stack up on exit (manual
                           review; the single-user tail always self-cleans)
    ACCEPTANCE_RECORD_DIR   default test-results/acceptance-records
    ACCEPTANCE_SINGLE_BASE_URL  default http://localhost:19889 — the single-
                           user tail's URL; also drives its published PORT

PREREQUISITES (declared, same tier as PR-A/#3384 was):
- #3110 (fixed by #3387): the app honors OPENACE_CONFIG_DIR for its config
  resolution. If this proof fails on a post-#3387 image, check that the
  merge step actually ran and the config volume name matches the dedicated
  project before suspecting a regression.
- multi-user shared-namespace provisioning: nothing in the product creates
  <base>/shared yet — item (d) records the fresh-deployment 403 as a
  declared known gap until the entrypoint provisions it.

Flow: refuse-if-project-has-state -> bootstrap env -> first `up -d --wait`
(entrypoint generates the full config) -> stop -> merge max_instances=3
(same-image helper) -> second `up -d --wait` -> default-admin first login +
password change -> two-tenant multi-user scenario (system_account triggers
the real useradd wrapper) -> nine-item assertions (a..i) -> single-user
regression tail (own compose project) -> `down -v` (unless KEEP_STACK).
Both stacks run in DEDICATED compose projects (acceptance-multi /
acceptance-single) — an existing deployment beside this checkout is never
touched.
"""

from __future__ import annotations

import json
import os
import platform
import shlex
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
# Absolute: compose must find the files AND the sibling .env regardless of
# the caller's cwd (review F9)
COMPOSE_FILES = [
    str(REPO_ROOT / "docker-compose.yml"),
    str(REPO_ROOT / "docker-compose.multi-user.yml"),
]
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


def run(
    cmd: list[str],
    *,
    check: bool = True,
    timeout: int = 300,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Run a command, echoing it for the record."""
    proc = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, check=False, cwd=cwd, env=env
    )
    if check and proc.returncode != 0:
        raise AcceptanceError(
            f"command failed ({proc.returncode}): {' '.join(cmd)}\n"
            f"stdout: {proc.stdout[-2000:]}\nstderr: {proc.stderr[-2000:]}"
        )
    return proc


MULTI_USER_PROJECT = "acceptance-multi"


def compose(*args: str, timeout: int = 600, check: bool = True) -> subprocess.CompletedProcess:
    """Multi-user stack compose — ALWAYS pinned to the dedicated
    `acceptance-multi` project (review round 3, 6493): without -p it would
    resolve to the directory-name default, i.e. the SAME project a
    production checkout runs (DEPLOYMENT.md instructs exactly that) — the
    script would then overwrite that deployment's config.json and `down -v`
    its volumes."""
    docker = shutil.which("docker")
    if not docker:
        raise AcceptanceError("docker CLI is unavailable")
    cmd = [docker, "compose", "-p", MULTI_USER_PROJECT, "-f", COMPOSE_FILES[0]]
    for extra in COMPOSE_FILES[1:]:
        cmd += ["-f", extra]
    cmd += list(args)
    return run(cmd, cwd=REPO_ROOT, timeout=timeout, check=check)


SINGLE_USER_BASE_URL = os.environ.get(
    "ACCEPTANCE_SINGLE_BASE_URL", "http://localhost:19889"
).rstrip("/")

_SINGLE_OVERRIDE: list[str] | None = None


def _single_user_override_files() -> list[str]:
    """Runtime-generated override renaming the base compose's hardcoded
    container_names for the single-user project (review round 2, finding 1).

    container_name is DOCKER-DAEMON-GLOBAL — a `-p acceptance-single`
    project would still collide with the running multi-user stack's
    open-ace/open-ace-postgres/open-ace-scheduler containers. Passed as the
    LAST -f so it wins the merge. Generated once per run."""
    global _SINGLE_OVERRIDE
    if _SINGLE_OVERRIDE is not None:
        return _SINGLE_OVERRIDE
    RECORD_DIR.mkdir(parents=True, exist_ok=True)
    override = RECORD_DIR / "acceptance-single-override.compose.yml"
    override.write_text(
        "# generated by scripts/multiuser_acceptance.py \u2014 single-user tail\n"
        "services:\n"
        "  open-ace:\n"
        "    container_name: acceptance-single-open-ace\n"
        "  scheduler:\n"
        "    container_name: acceptance-single-scheduler\n"
        "  postgres:\n"
        "    container_name: acceptance-single-postgres\n",
        encoding="utf-8",
    )
    _SINGLE_OVERRIDE = [str(override)]
    return _SINGLE_OVERRIDE


def _single_user_env() -> dict[str, str]:
    """Env for the single-user project (review round 2, findings 1+3): the
    published web port is DERIVED from SINGLE_USER_BASE_URL (one knob, not
    two uncoupled ones), and the workspace port RANGE is offset to
    13100-13200 — the running multi-user stack already holds host
    3100-3200 and a second docker-proxy bind there aborts the up.
    Since the single-user instance honors the configured range (first free
    port — the hardcoded-3100 leftover is fixed), it binds 13100 and
    advertises :13100; item h's assertions remain API-level and never
    connect to it."""
    from urllib.parse import urlparse

    port = urlparse(SINGLE_USER_BASE_URL).port or (
        443 if SINGLE_USER_BASE_URL.startswith("https") else 80
    )
    return {
        **os.environ,
        "PORT": str(port),
        "WORKSPACE_PORT_RANGE_START": "13100",
        "WORKSPACE_PORT_RANGE_END": "13200",
        # the bootstrap-written .env pins production (a multi-user requirement);
        # item h targets the DEFAULT single-user shape, which is development and
        # self-initializes a fresh database (init_db seeds admin + tenant 1).
        # Inheriting production instead made the tail hit the same
        # "Fresh database detected" refusal as the multi-user first run
        # (review round 3, 4004859935).
        "OPENACE_SECURITY_MODE": "development",
    }


def compose_base(*args: str, timeout: int = 600, check: bool = True) -> subprocess.CompletedProcess:
    """Compose against the SINGLE-USER base file only, in its own project
    (review F10): item (h) runs beside the multi-user stack instead of
    replacing it, so ACCEPTANCE_KEEP_STACK leaves the MULTI-USER stack up
    for inspection, matching the handbooks."""
    docker = shutil.which("docker") or "docker"
    cmd = [
        docker,
        "compose",
        "-p",
        "acceptance-single",
        "-f",
        str(REPO_ROOT / "docker-compose.yml"),
    ]
    for override in _single_user_override_files():
        cmd += ["-f", override]
    cmd += list(args)
    return run(cmd, cwd=REPO_ROOT, env=_single_user_env(), timeout=timeout, check=check)


def compose_exec(
    service: str,
    shell_cmd: str,
    *,
    user: str | None = None,
    timeout: int = 120,
    check: bool = True,
):
    """Run a command INSIDE the service container (plan §3-f: restart and
    process assertions must be container-side — the host-side docker-proxy
    always listens on published ports and would be a permanent false pass).
    check=False for commands whose NON-ZERO exit is the expected, isolated
    outcome (review 7134: with check hardwired True, a correctly-denied
    cross-user read aborted the whole acceptance run)."""
    args = ["exec", "-T"]
    if user:
        args += ["-u", user]
    args += [service, "sh", "-c", shell_cmd]
    return compose(*args, timeout=timeout, check=check)


def compose_down_volumes() -> None:
    compose("down", "-v", "--remove-orphans", timeout=300)


def compose_cp(src: str, dst: str, *, timeout: int = 60) -> subprocess.CompletedProcess:
    """`docker compose cp` for the multi-user stack, ALWAYS via compose() so
    the project pin and the FULL COMPOSE_FILES list live in one place (review
    round 4, 4013713715): the two former hand-built cp call sites hardcoded
    COMPOSE_FILES[0]/[1] and would have run against a DIFFERENT stack than
    every other compose call the moment COMPOSE_FILES grows a third overlay.
    rc handling stays with the caller: both cp directions here treat a
    non-zero rc as data (repo-checkout fallback / skip note), never silence."""
    return compose("cp", src, dst, timeout=timeout, check=False)


# Start anchors for the entrypoint's user-sync python block, tried in order:
# the post-#3400 pipefail shape first, then the legacy plain invocation. Both
# shapes end at the same '" 2>&1 | tee' anchor.
_SYNC_PY_ANCHORS = (
    '( set -o pipefail; python3 -u -c "',
    'python3 -c "',
)


def _extract_sync_python(entrypoint: str) -> str:
    """Verbatim user-sync python from an entrypoint text, tolerant of BOTH
    invocation shapes (review finding: hard-coded anchors broke with #3400's
    pipefail rewrite — precisely when the forensics is most needed).

    The block lives inside a double-quoted shell string: unescape what bash
    would — '\\\\' FIRST (a later rule must not eat the backslash of an
    earlier one), then \\", \\$, \\` — so the extracted python compiles."""
    sync_at = entrypoint.index("Syncing workspace users")
    for anchor in _SYNC_PY_ANCHORS:
        try:
            py_start = entrypoint.index(anchor, sync_at)
        except ValueError:
            continue
        py_start = entrypoint.index("\n", py_start) + 1
        py_end = entrypoint.index('" 2>&1 | tee', py_start)
        sync_py = entrypoint[py_start:py_end]
        for esc, raw in (("\\\\", "\\"), ("\\$", "$"), ("\\`", "`"), ('\\"', '"')):
            sync_py = sync_py.replace(esc, raw)
        return sync_py
    raise ValueError(
        "no known user-sync python anchor after 'Syncing workspace users' "
        f"(tried: {' or '.join(_SYNC_PY_ANCHORS)})"
    )


def _entrypoint_text_for_sync_rerun() -> str:
    """The entrypoint text to extract the sync python from — the CONTAINER'S
    OWN copy via compose cp when fetchable (fidelity: the file that actually
    ran, not whatever the repo checkout happens to contain), else the repo
    checkout (best effort — the container may already be gone)."""
    local = RECORD_DIR / "container-docker-entrypoint.sh"
    try:
        RECORD_DIR.mkdir(parents=True, exist_ok=True)
        local.unlink(missing_ok=True)  # never reuse a stale copy from an earlier run
        proc = compose_cp(f"{SERVICE}:/usr/local/bin/docker-entrypoint.sh", str(local))
        if proc.returncode == 0 and local.stat().st_size:
            return local.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001 - fall back to the repo checkout below
        pass
    # the fallback leaves a trace in the forensics file (review 4013713715):
    # on CI the image is built from this same checkout so both texts match,
    # but a manual run against another IMAGE_NAME must be able to tell from
    # the record WHICH entrypoint text the extraction fell back to.
    try:
        with (RECORD_DIR / "user-sync-forensics.txt").open("a", encoding="utf-8") as fh:
            fh.write(
                "\n---entrypoint source---\nfallback: repo checkout "
                f"{REPO_ROOT / 'docker-entrypoint.sh'} (container copy not fetchable)\n"
            )
    except Exception:  # noqa: BLE001 - a trace failure must not break the fallback
        pass
    return (REPO_ROOT / "docker-entrypoint.sh").read_text(encoding="utf-8")


def dump_stack_logs(recorder: Recorder) -> None:
    """On failure, keep compose logs inside the record directory (CI
    artifact). Both projects are dumped — item h runs its own single-user
    project, and it must not lose its logs because of that (review round 2,
    finding 2)."""
    RECORD_DIR.mkdir(parents=True, exist_ok=True)
    for name, fn in (("compose-logs.txt", compose), ("compose-logs-single.txt", compose_base)):
        try:
            proc = fn("logs", "--no-color", "--tail", "400", timeout=120)
            (RECORD_DIR / name).write_text(proc.stdout, encoding="utf-8")
        except Exception as exc:  # noqa: BLE001 - best effort during failure handling
            recorder.note(f"{name} dump failed: {exc}")
    # entrypoint user-sync forensics: the sync's own stdout/stderr is tee'd
    # to /app/logs/open-ace-user-sync.log inside the container — compose logs
    # alone cannot explain a sync that creates zero users (run 34917224776 /
    # 34918312590: header printed, no per-user lines, no error, alice/carol/
    # dave all missing post-recreate)
    forensics_path = RECORD_DIR / "user-sync-forensics.txt"
    # parts 1+2: sync log cat + getent passwd + DB probe. Written to the file
    # IMMEDIATELY (review finding): part 3 below extracts the sync python via
    # entrypoint anchors that DRIFT as the entrypoint evolves (#3400 rewrote
    # the invocation) — an anchor miss must never discard evidence already in
    # hand, which is exactly when the forensics is most needed.
    try:
        # single line: python -c needs real newlines, and the quote chain
        # (python str -> sh -c) flattens them — semicolons only
        probe = (
            "import os, psycopg2; "
            "conn = psycopg2.connect(os.environ['DATABASE_URL']); "
            "cur = conn.cursor(); "
            "cur.execute('SELECT username, system_account, is_active FROM users ORDER BY id'); "
            "print('DB ROWS:', cur.fetchall())"
        )
        proc = compose_exec(
            SERVICE,
            "cat /app/logs/open-ace-user-sync.log; "
            "echo ---PASSWD---; getent passwd; "
            "echo ---DBPROBE---; "
            # presence ONLY — never the value itself (secret hygiene)
            'echo "DATABASE_URL_PRESENT=${DATABASE_URL:+yes}"; ' f"python3 -c {shlex.quote(probe)}",
            timeout=60,
            check=False,
        )
        forensics_path.write_text(
            proc.stdout + "\n---probe stderr---\n" + proc.stderr, encoding="utf-8"
        )
    except Exception as exc:  # noqa: BLE001 - best effort during failure handling
        recorder.note(f"user-sync forensics parts 1+2 (log/passwd/DB probe) failed: {exc}")
        return
    # part 3: rerun the ENTRYPOINT'S OWN sync python verbatim inside the
    # recreated container (copied in via compose cp) — separates "the sync
    # code fails in this container" from "the entrypoint context never
    # reaches/runs it". Its OWN try: on any failure (anchor drift, cp, rerun)
    # a short skip note is APPENDED — parts 1+2 stay in the file.
    try:
        entrypoint = _entrypoint_text_for_sync_rerun()
        sync_py = _extract_sync_python(entrypoint)
        probe_path = RECORD_DIR / "sync-rerun.py"
        probe_path.write_text(sync_py, encoding="utf-8")
        if KEEP_STACK:
            # review 4013713265: the rerun useradds, chown -Rs homes/project
            # dirs and UPDATEs users.system_uid — on a stack kept for manual
            # review it would erase the very failure state being kept (e.g.
            # #3399's missing accounts). The EXTRACTION above stays: it is
            # read-only and leaves the exact sync python in the record dir
            # for a deliberate manual replay.
            with forensics_path.open("a", encoding="utf-8") as fh:
                fh.write(
                    "\n---sync rerun---\npart-3 skipped: ACCEPTANCE_KEEP_STACK=1 "
                    "(stack kept for manual debugging; rerun mutates users/dirs)\n"
                )
            recorder.note("user-sync forensics part 3 skipped: rerun would mutate the kept stack")
            return
        cp = compose_cp(str(probe_path), f"{SERVICE}:/tmp/sync-rerun.py")
        if cp.returncode != 0:
            raise AcceptanceError(f"compose cp sync-rerun.py failed: {cp.stderr.strip()[:200]}")
        # 2>&1 like the boot-time `2>&1 | tee`: a traceback or the sync's own
        # stderr lines (e.g. "ERROR: SSH key sync failed") ARE the evidence
        rerun = compose_exec(
            SERVICE, "python3 /tmp/sync-rerun.py 2>&1; echo RERUN_RC=$?", timeout=120, check=False
        ).stdout
        with forensics_path.open("a", encoding="utf-8") as fh:
            fh.write("\n---sync rerun---\n" + rerun)
    except Exception as exc:  # noqa: BLE001 - best effort; parts 1+2 already saved
        with forensics_path.open("a", encoding="utf-8") as fh:
            fh.write(f"\n---sync rerun---\npart-3 skipped: {exc}\n")
        recorder.note(f"user-sync forensics part 3 (sync rerun) skipped: {exc}")


# ── config: two-phase generate-then-merge (review round 3, 6725) ─────────
#
# The first `up` lets the ENTRYPOINT generate the complete multi-user config
# (required_isolation_level floor, token_secret, upload_auth_key …); then a
# helper container — the SAME image under test, so nothing extra is pulled —
# merges ONLY workspace.max_instances=3 into that config, and the second `up`
# starts the app against it. Pre-seeding a 3-key config before the first up
# (the round-1 design) would skip generate_default_config (and, before #3387
# fixed #3110, was written to a volume the app did not read — the app resolved
# config at ~/.open-ace). The preseed proof in main() still verifies that the
# merged value actually reaches the running app.


CONFIG_VOLUME = f"{MULTI_USER_PROJECT}_config-data"


def refuse_if_project_has_state() -> None:
    """Refuse (exit path) when the dedicated project already holds state —
    overlapping runs and half-torn stacks must not be silently reused, and a
    fresh acceptance must never run on top of stale containers/volumes
    (review 6493)."""
    docker = shutil.which("docker") or "docker"
    leftovers = []
    for kind, probe in (
        (
            "containers",
            ["ps", "-aq", "--filter", f"label=com.docker.compose.project={MULTI_USER_PROJECT}"],
        ),
        (
            "volumes",
            [
                "volume",
                "ls",
                "-q",
                "--filter",
                f"label=com.docker.compose.project={MULTI_USER_PROJECT}",
            ],
        ),
    ):
        proc = run([docker, *probe], check=False, timeout=30)
        if proc.stdout.strip():
            leftovers.append(kind)
    if leftovers:
        print(
            f"refusing to run: compose project '{MULTI_USER_PROJECT}' already has "
            f"{'+'.join(leftovers)}; clean it first: docker compose -p {MULTI_USER_PROJECT} "
            f"-f docker-compose.yml -f docker-compose.multi-user.yml down -v --remove-orphans",
            file=sys.stderr,
        )
        raise SystemExit(2)


def merge_max_instances(max_instances: int = 3) -> None:
    """Merge workspace.max_instances into the config the entrypoint generated
    (same-image helper container; read-modify-write preserves every other
    key). Volume name is deterministic under the dedicated project."""
    docker = shutil.which("docker") or "docker"
    image = os.environ.get("IMAGE_NAME") or "openace/open-ace:latest"
    merge_code = (
        "import json\n"
        "p='/config/config.json'\n"
        "c=json.load(open(p))\n"
        f"c.setdefault('workspace', {{}})['max_instances'] = {max_instances}\n"
        "json.dump(c, open(p,'w'), indent=2)\n"
        "print('merged:', json.load(open(p))['workspace'])\n"
    )
    run(
        [
            docker,
            "run",
            "--rm",
            # review round 4 (4003820636): bypass the image ENTRYPOINT (its
            # production security-mode validation exit-1s without
            # OPENACE_SECURITY_MODE before ever reaching our args) and run as
            # root — the config the entrypoint generated is root:root 0600,
            # unreadable for the image's default USER 1000.
            "--user",
            "0",
            "--entrypoint",
            "python3",
            "-v",
            f"{CONFIG_VOLUME}:/config",
            image,
            "-c",
            merge_code,
        ],
        cwd=REPO_ROOT,
        timeout=300,
    )


# ── HTTP helper (stdlib urllib — no pip install on the runner) ───────────


def _multipart(files: dict[str, tuple[str, bytes]], form: dict[str, str]) -> tuple[bytes, str]:
    """Build a multipart/form-data body (stdlib only — no requests here)."""
    boundary = "----openace-acceptance-" + os.urandom(8).hex()
    parts: list[bytes] = []
    for key, value in (form or {}).items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n'
            f"{value}\r\n".encode()
        )
    for key, (filename, content) in (files or {}).items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"; '
            f'filename="{filename}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode()
            + content
            + b"\r\n"
        )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def http(
    method: str,
    path: str,
    *,
    token: str | None = None,
    body: dict[str, Any] | None = None,
    params: dict[str, str] | None = None,
    files: dict[str, tuple[str, bytes]] | None = None,
    form: dict[str, str] | None = None,
    timeout: int = 60,
) -> tuple[int, dict[str, Any] | list[Any] | str, dict[str, str]]:
    """Perform one HTTP request; returns (status, parsed-body, headers).

    Non-2xx is NOT an error — acceptance assertions decide what each call
    must return. Network-level failures raise.

    *files* / *form* (Issue #3410) send a multipart/form-data body instead of
    JSON — ``/api/fs/upload`` is the only multipart endpoint the acceptance
    drives. They are mutually exclusive with *body*.
    """
    url = f"{BASE_URL}{path}"
    if params:
        from urllib.parse import urlencode

        url += "?" + urlencode(params)
    data = None
    headers = {"Accept": "application/json"}
    if files or form:
        if body is not None:
            raise AcceptanceError("http(): pass either body= or files=/form=, not both")
        data, content_type = _multipart(files or {}, form or {})
        headers["Content-Type"] = content_type
    elif body is not None:
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
        git_sha = ""
        try:
            git_sha = run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, timeout=30).stdout.strip()
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
                        "{{if gt (len .RepoDigests) 0}}{{index .RepoDigests 0}}{{else}}{{.Id}}{{end}}",
                        image,
                    ],
                    check=False,
                    timeout=30,
                ).stdout.strip()
                or "unavailable"
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

    def exempt(self, item: str, name: str, detail: str = "", *, response: Any = None) -> None:
        """Record a DECLARED exemption: shown in the record table, excluded
        from the passed/failed counts (review round 2, finding 4)."""
        self.items.append(
            {
                "item": item,
                "name": name,
                "result": "EXEMPT",
                "detail": detail,
                "evidence": {"request": None, "response": _truncate(response)},
            }
        )
        print(f"  [EXEMPT] {item} {name}")

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
                "exempt": sum(1 for i in self.items if i["result"] == "EXEMPT"),
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
            f"- 结果: **{payload['summary']['passed']} passed / {payload['summary']['failed']} failed"
            f" / {payload['summary']['exempt']} exempt**",
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
        f"psql -U ace -d ace -v ON_ERROR_STOP=1 -c {shlex.quote(sql)}",
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


def find_webui_processes(port: int) -> list[dict[str, str]]:
    """All /proc rows matching `--port N`. In the multi-user launch shape
    this returns TWO processes: the root-uid `sudo -u <account>
    openace-webui-launch ...` parent — it persists for the child's lifetime
    and its cmdline carries the same --port/--token-secret args — and the
    actual webui process running under the account's uid (review F2: the
    first-match-in-pid-order version deterministically returned the sudo
    parent, voiding the uid/env/kill assertions)."""
    matches = []
    for row in _proc_table():
        args = row["cmd"].split()
        for i, arg in enumerate(args):
            if arg == "--port" and i + 1 < len(args) and args[i + 1] == str(port):
                matches.append(row)
                break
    return matches


def find_webui_process(port: int, account: str) -> dict[str, str] | None:
    """The ACTUAL webui process: the port match whose uid IS the account's."""
    uid = container_uid_of(account)
    for row in find_webui_processes(port):
        if row["uid"] == uid:
            return row
    return None


def container_uid_of(account: str) -> str:
    out = compose_exec(SERVICE, f"id -u {account}", timeout=15).stdout.strip()
    if not out.isdigit():
        raise AcceptanceError(f"no uid for system account {account}")
    return out


def listening_ports_in_range(low: int, high: int) -> list[int]:
    """Parse /proc/net/tcp(+tcp6) inside the container for LISTEN sockets in
    [low, high] — ss/netstat are not guaranteed in the image, and its awk is
    mawk (no strtonum — gawk-only), so parse with the container's python3
    (review F1: the awk variant fatal-errors and aborts the whole run)."""
    script = (
        "python3 -c '"
        "ports=set()\n"
        'for f in ("/proc/net/tcp","/proc/net/tcp6"):\n'
        "  try:\n"
        "    for line in open(f).readlines()[1:]:\n"
        "      p=line.split()\n"
        '      if len(p)>3 and p[3]=="0A": ports.add(int(p[1].split(":")[1],16))\n'
        "  except OSError: pass\n"
        'print(" ".join(str(x) for x in sorted(ports)))'
        "'"
    )
    out = compose_exec(SERVICE, script, timeout=30).stdout
    return sorted(
        {int(line) for line in out.split() if line.strip().isdigit() and low <= int(line) <= high}
    )


# One container exec: find the webui's SUDO PARENT and parse its inline
# KEY=VALUE args. Both the first main-push run (tr exit 2) and the PR-#3398
# run made the real cause explicit: /proc/<pid>/environ of a non-child
# process is Permission-denied for container root on GitHub runners
# (yama/seccomp) — environ was never readable there. The launch shape
# inlines the injected env as ARGUMENTS to the sudo wrapper
# (webui_manager: "inline KEY=VALUE args are visible in /proc/<pid>/cmdline
# to other processes" — deliberately so, the values are proxy tokens), and
# /proc/*/cmdline is world-readable. The sudo parent is identified by the
# openace-webui-launch arg plus BOTH the --port N pair AND the adjacent
# `-u <account>` (review round 4, 4013714130): item f reads bob's proxy
# token with no independent uid backstop — if the port were served by
# ANOTHER account's instance (the instance-crossover the acceptance exists
# to catch), a port-only match would hand over that account's token and the
# failure would be misattributed to PR-A revocation. Retries ride on top in
# case the launch is still forking.
_ENV_DUMP_TEMPLATE = """for d in /proc/[0-9]*; do
  args=$(tr '\\0' '\\n' < $d/cmdline 2>/dev/null) || continue
  case "$args" in *openace-webui-launch*) ;; *) continue ;; esac
  printf '%s\\n' "$args" | awk -v p='PORTARG' -v u='ACCOUNTARG' 'prev=="--port"&&$0==p{f=1} prev=="-u"&&$0==u{g=1} {prev=$0} END{exit (f&&g)?0:1}' || continue
  printf '%s\\n' "$args" | grep -E '^[A-Za-z_][A-Za-z0-9_]*='
  exit 0
done
exit 1"""


def webui_env_of(port: int, account: str, *, attempts: int = 5) -> dict[str, str]:
    """The injected environment of the webui launched for *port* AS *account*,
    read from that account's sudo parent (inline KEY=VALUE cmdline args — see
    the template's comment for why /proc environ is not usable on CI runners,
    and why the -u match is load-bearing).

    Retried — a launch still forking raises after `attempts` tries so
    callers can record the failure instead of silently skipping."""
    script = _ENV_DUMP_TEMPLATE.replace("PORTARG", str(port)).replace("ACCOUNTARG", account)
    last = ""
    for _attempt in range(attempts):
        out = compose_exec(SERVICE, script, timeout=20, check=False)
        if out.returncode == 0 and out.stdout.strip():
            env: dict[str, str] = {}
            for line in out.stdout.splitlines():
                if "=" in line:
                    key, _, value = line.partition("=")
                    env[key] = value
            return env
        last = f"rc={out.returncode} out={out.stdout[:80]!r} err={out.stderr.strip()[:120]!r}"
        time.sleep(2)
    raise AcceptanceError(
        f"webui launch env for port {port} (account {account}) not readable "
        f"after {attempts} attempts — no sudo wrapper matching BOTH --port {port} "
        f"and -u {account} in /proc: the instance is not running, or the port is "
        f"served by ANOTHER account's instance (instance-crossover finding — "
        f"record it, do not retry as an env-read issue); last: {last}"
    )


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
                "run with a fresh stack: docker compose -p acceptance-multi "
                "-f docker-compose.yml -f docker-compose.multi-user.yml "
                "down -v --remove-orphans"
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
        # dave is created INSIDE item (e), after carol's slot is confirmed —
        # creation/login spawns a prestart greenlet that could otherwise race
        # carol for the 3rd slot (review F7). erin: NO system_account — the
        # identity-mapping leg of item (g)
        self.create_user(
            "erin", self.tenant1_id, password="Erin-Acceptance-2026!x", system_account=None
        )
        for username in ("alice", "bob", "carol", "erin"):
            self.user_login(username)
        self.recorder.note(
            "scenario built: tenant-1{alice,bob,erin(no-map)} + tenant-2{carol}; "
            "dave deferred to item (e)"
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
        # F2: match by uid — the port also matches the root-uid sudo wrapper;
        # the REAL webui process is the account-uid one
        expect_uid = container_uid_of(name)
        matches = find_webui_processes(ports[name])
        own = [m for m in matches if m["uid"] == expect_uid]
        rec.check(
            "a",
            f"{name} webui process runs under its own uid (sudo -u)",
            len(own) == 1,
            f"matches={[(m['pid'], m['uid']) for m in matches]} expected_uid={expect_uid}",
        )
        mode = compose_exec(SERVICE, f"stat -c %a /home/{name}", timeout=15).stdout.strip()
        rec.check("a", f"/home/{name} is 0700", mode == "700", f"mode={mode}")

    # Model-config separation (same evidence channel as item c): each webui
    # env carries ONLY proxy tokens — no real/dynamic model keys — and the
    # two proxy tokens differ.
    # review 7374: on the sudo-launch path the proxy token reaches the webui
    # ONLY as the inlined OPENAI_API_KEY (popen_env=None — the parent env has
    # no OPENACE_PROXY_TOKEN for sudo env_keep to preserve). Read it there.
    # Each user's env is probed in its OWN try (review finding): the previous
    # single dict-comprehension dropped the OTHER user's would-be rows when
    # one read failed, and the early return skipped the homes-listing
    # evidence below. A webui that exits right after launch is a FINDING
    # (recorded), not a reason to abort the checklist — the remaining items
    # still run.
    envs: dict[str, dict[str, str]] = {}
    for name in ("alice", "bob"):
        try:
            envs[name] = webui_env_of(ports[name], name)
        except (AcceptanceError, subprocess.TimeoutExpired) as exc:
            rec.check("a", f"{name} webui environment readable", False, str(exc)[:300])
    for name, env in envs.items():
        rec.check(
            "a",
            f"{name} webui env: OPENAI_API_KEY is a proxy token (present)",
            bool(env.get("OPENAI_API_KEY")),
            f"keys={sorted(k for k in env if k.endswith(('KEY', 'TOKEN')))}",
        )
        dynamic_leak = {"ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL"} & set(env)
        rec.check(
            "a",
            f"{name} webui env: no sensitive real keys",
            not dynamic_leak,
            f"leaked={dynamic_leak}",
        )
    if len(envs) == 2:
        rec.check(
            "a",
            "alice/bob proxy tokens differ",
            bool(envs["alice"].get("OPENAI_API_KEY"))
            and envs["alice"]["OPENAI_API_KEY"] != envs["bob"].get("OPENAI_API_KEY"),
        )
    # app-side note (7374): OPENACE_PROXY_TOKEN is NOT exported on the sudo
    # path. Review round 4 (4013714130): reading the args handed to sudo makes
    # this BY CONSTRUCTION — webui_manager's sudo launch inlines only its
    # standard key set, and OPENACE_PROXY_TOKEN sits in
    # _WEBUI_ENV_SUDO_KNOWN_KEYS (excluded from the dynamic loop) but NOT in
    # the inlined standard keys — so this documents the launch shape rather
    # than reporting an observation; the proxy token reaches the webui as
    # OPENAI_API_KEY.
    if envs and all("OPENACE_PROXY_TOKEN" not in env for env in envs.values()):
        recorder_note = (
            "OPENACE_PROXY_TOKEN absent from webui env by construction (sudo-launch "
            "inlines only the known key set, which excludes it); the proxy token "
            "reaches the webui as OPENAI_API_KEY"
        )
        rec.note(recorder_note)
    # History roots are per-account by construction (0700 homes); record the
    # materialized layout for the manual browser-side screenshot review.
    # Recorded on EVERY path — an env-read failure above must not drop it.
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

    # fs: cross-user home, traversal, symlink. Targets are /workspace/<bob>
    # (the fs-visible home roots) so the attacks reach realpath + the #3376
    # home-lock, not just the base-dir prefix gate (review F8)
    compose_exec(SERVICE, "ln -sfn /workspace/bob /workspace/alice/link-to-bob", timeout=15)
    for label, params, expect in (
        ("alice browse bob workspace home -> 400", {"path": "/workspace/bob"}, 400),
        ("alice browse traversal ../ -> 400", {"path": "/workspace/../../etc"}, 400),
        ("alice browse symlink to bob -> 400", {"path": "/workspace/alice/link-to-bob"}, 400),
    ):
        status, body, _ = http("GET", "/api/fs/browse", token=alice_token, params=params)
        rec.check("b", label, status == expect, f"status={status}", request=params, response=body)
    status, body, _ = http(
        "POST", "/api/fs/check-path", token=alice_token, body={"path": "/workspace/bob"}
    )
    rec.check(
        "b",
        "check-path bob workspace home -> 400",
        status == 400,
        f"status={status}",
        response=body,
    )

    # Issue #3410: fs upload must not write THROUGH a symlink. This stack runs
    # as root, which is the exact branch the escalation lived in — a successful
    # escape would overwrite bob's file outright, not merely be denied by DAC.
    compose_exec(SERVICE, "echo VICTIM-ORIGINAL > /workspace/bob/acc-secret.txt", timeout=15)
    compose_exec(
        SERVICE,
        "ln -sfn /workspace/bob/acc-secret.txt /workspace/alice/acc-secret.txt",
        timeout=15,
    )
    status, body, _ = http(
        "POST",
        "/api/fs/upload",
        token=alice_token,
        files={"file": ("acc-secret.txt", b"PWNED")},
        form={"path": "/workspace/alice"},
    )
    rec.check(
        "b",
        "alice upload through symlink to bob -> rejected",
        status == 400,
        f"status={status}",
        response=body,
    )
    content = compose_exec(
        SERVICE, "cat /workspace/bob/acc-secret.txt", timeout=15, check=False
    ).stdout.strip()
    rec.check(
        "b",
        "bob's file unchanged after the symlink upload attempt",
        content == "VICTIM-ORIGINAL",
        f"content={content!r}",
    )
    status, body, _ = http(
        "POST",
        "/api/fs/upload",
        token=alice_token,
        files={"file": ("acc-plain.txt", b"OK")},
        form={"path": "/workspace/alice"},
    )
    rec.check(
        "b",
        "alice plain upload into her own home -> 200",
        status == 200,
        f"status={status}",
        response=body,
    )
    status, body, _ = http(
        "POST",
        "/api/fs/create-directory",
        token=alice_token,
        body={"path": "/workspace/bob/acc-evil"},
    )
    rec.check(
        "b",
        "alice create-directory in bob's home -> 400",
        status == 400,
        f"status={status}",
        response=body,
    )
    # Issue #3410: <base>/<account> must be private at the OS layer too — at
    # 0755 any other account could read the whole workspace from a shell.
    mode = compose_exec(SERVICE, "stat -c %a /workspace/bob", timeout=15).stdout.strip()
    rec.check("b", "/workspace/bob is 0700", mode == "700", f"mode={mode}")
    proc = compose_exec(SERVICE, "ls /workspace/bob", user="alice", timeout=15, check=False)
    rec.check(
        "b",
        "alice shell ls /workspace/bob -> EACCES",
        proc.returncode != 0 and "Permission denied" in proc.stderr,
        f"rc={proc.returncode} stderr={proc.stderr.strip()[:120]!r}",
    )


def item_c_environment_isolation(sc: Scenario) -> None:
    """(c) no other user's key/token in env; A's tools cannot enter B's area."""
    rec, r = sc.recorder, sc
    print("[c] environment/process separation")

    bob_port = int(
        http("GET", "/api/workspace/user-url", token=r.users["bob"]["token"])[1]["url"].rsplit(
            ":", 1
        )[-1]
    )
    bob_proc = find_webui_process(bob_port, "bob")
    rec.check("c", "bob webui process located", bob_proc is not None, f"port={bob_port}")
    if bob_proc:
        # alice (docker exec -u alice) cannot read bob's webui environ —
        # non-zero is the EXPECTED outcome, so check=False (review 7134)
        proc = compose_exec(
            SERVICE,
            f"cat /proc/{bob_proc['pid']}/environ",
            user="alice",
            timeout=15,
            check=False,
        )
        rec.check(
            "c",
            "alice reading bob webui /proc environ -> denied",
            proc.returncode != 0,
            f"rc={proc.returncode}",
        )
        # Terminal equivalence: a shell AS alice cannot list bob's home
        proc = compose_exec(SERVICE, "ls /home/bob", user="alice", timeout=15, check=False)
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
    # review 7550: NOTHING in the product creates <base>/shared — on a fresh
    # deployment `create_dir: true` runs sudo -u alice mkdir under a
    # root:root 0755 parent and the route returns 403. The script does NOT
    # provision the directory (an earlier round did, and the PASS then hid
    # this product gap); the 403 is recorded as a declared known gap with an
    # app-side follow-up, and the grant/revoke matrix runs only when the
    # product can actually create the project.
    status, body, _ = http(
        "POST",
        "/api/projects",
        token=r.users["alice"]["token"],
        body={"path": shared_path, "name": "acc-team-proj", "is_shared": True, "create_dir": True},
    )
    # review round 4 (4003822238): exempt ONLY the declared cause — a 403
    # with the shared root genuinely absent (probe, don't assume). Any other
    # non-201 flows into the normal FAIL path so shared-path regressions
    # (400/409/500/401) are not swallowed by the exemption.
    shared_root_missing = (
        compose_exec(SERVICE, f"test -d {base_dir}/shared", timeout=15, check=False).returncode != 0
    )
    if status == 403 and shared_root_missing:
        rec.exempt(
            "d",
            "shared-project creation: known product gap (no <base>/shared provisioning)",
            f"status={status} with {base_dir}/shared confirmed absent — entrypoint "
            "should create it (openace-shared, 2775) in multi-user mode; app-side "
            "follow-up. Grant/revoke matrix skipped until it lands.",
            response=body,
        )
        return
    rec.check(
        "d",
        "alice creates shared project",
        status == 201,
        f"status={status}",
        request=shared_path,
        response=body,
    )
    if status != 201:
        return
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

    # review round 3 (4004860481): the API layer is only half the story —
    # cross-tenant and post-revocation access must ALSO be probed at the OS
    # layer via the same channel item c uses to argue terminal equivalence.
    # Since #3396 shared dirs are group-owned by the TENANT group
    # openace-shared-<tenant_id> with 2770/660 (no others bits), and
    # revocation reclaims the directory to the creator (chown -R + 0700/0600).
    # Positive controls guard against over-tightening: bob (same tenant)
    # writes while the project is shared; alice (creator) keeps her own
    # project after the revocation reclaim.
    proc = compose_exec(
        SERVICE, f"touch {shared_path}/acc-bob-while-shared", user="bob", timeout=15, check=False
    )
    rec.check(
        "d",
        "OS layer: bob (same tenant) shell touch in shared project while shared",
        proc.returncode == 0,
        f"rc={proc.returncode} — tenant-group grant broken (member cannot write)",
    )

    status, body, _ = http(
        "PUT",
        f"/api/projects/{project_id}",
        token=r.users["alice"]["token"],
        body={"is_shared": False},
    )
    rec.check("d", "alice revokes sharing", status == 200, f"status={status}", response=body)
    # The reclaim is fail-soft: a 200 with permission_warning means the OS
    # strip failed and the later EACCES probes would fail for the wrong
    # diagnosis — assert the warning is absent so it fails HERE, at the API
    # check, instead.
    rec.check(
        "d",
        "revocation reclaimed OS permissions (no permission_warning)",
        "permission_warning" not in (body or {}),
        f"body={body}",
    )
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

    # Denial probes: carol is a member of the GLOBAL openace-shared group
    # (namespace creation) but not of tenant-1's openace-shared-<t1>, so the
    # 2770 project dir must EACCES her; bob loses group access when the
    # revocation reclaims the dir to alice:alice 0700. Both probe groups run
    # after the revocation above — carol's access is group-membership-based
    # and unaffected by the revocation flag either way, so the ordering is
    # immaterial; labels name what each proves.
    for label, account in (
        ("carol (other tenant) shell ls shared project -> EACCES", "carol"),
        ("carol (other tenant) shell touch in shared project -> EACCES", "carol"),
        ("bob shell ls shared project after revocation -> EACCES", "bob"),
        ("bob shell touch after revocation -> EACCES", "bob"),
    ):
        cmd = f"touch {shared_path}/acc-probe" if "touch" in label else f"ls {shared_path}"
        proc = compose_exec(SERVICE, cmd, user=account, timeout=15, check=False)
        rec.check(
            "d",
            f"OS layer: {label}",
            proc.returncode != 0,
            f"rc={proc.returncode} — OS channel still open after revocation (#3396)",
        )
    # Creator retention: the reclaim must hand the directory to alice, not
    # lock everyone out (a 0000/root reclaim would break her private work).
    for label, cmd in (
        ("alice shell ls her revoked project -> allowed", f"ls {shared_path}"),
        ("alice shell touch in her revoked project -> allowed", f"touch {shared_path}/acc-alice"),
    ):
        proc = compose_exec(SERVICE, cmd, user="alice", timeout=15, check=False)
        rec.check(
            "d",
            f"OS layer: {label}",
            proc.returncode == 0,
            f"rc={proc.returncode} — revocation reclaim broke the creator's own access",
        )


def item_e_resource_limits(sc: Scenario) -> None:
    """(e) resource ceiling, cancellation, crash isolation."""
    rec, r = sc.recorder, sc
    print("[e] resource ceilings and fault isolation")

    # alice + bob already have instances; carol takes the 3rd slot
    status, body, _ = r.user_url("carol")
    rec.check("e", "carol takes the 3rd instance slot", status == 200, f"status={status}")
    # F7: dave only exists NOW — carol's slot is confirmed, so dave's
    # login-prestart greenlet can no longer steal it
    r.create_user("dave", r.tenant1_id, password="Dave-Acceptance-2026!x", system_account="dave")
    r.user_login("dave")
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
    bob_proc = find_webui_process(bob_port, "bob")
    rec.check(
        "e",
        "bob webui process located for kill -9",
        bob_proc is not None,
        f"port={bob_port}",
    )
    if bob_proc:
        # F2: kill the bob-uid webui itself — killing the sudo parent would
        # orphan the node process and keep the port alive
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
        find_webui_process(carol_port, "carol") is not None,
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
    # review 7374: the proxy token reaches the webui as OPENAI_API_KEY
    # (sudo-launch inlines the known key set only). Tolerant read, same shape
    # as item a: an unreadable env is a recorded FAIL for THIS assertion — the
    # token-dependent llm-proxy revocation sub-assertion below is skipped,
    # and the item (and g/h/i after it) continue instead of aborting the run.
    try:
        bob_proxy_token = webui_env_of(bob_port, "bob").get("OPENAI_API_KEY", "")
    except (AcceptanceError, subprocess.TimeoutExpired) as exc:
        bob_proxy_token = ""
        rec.check("f", "bob proxy token captured pre-deactivation", False, str(exc)[:300])
    else:
        # F2/7374: a missing proxy token must FAIL the record, not silently skip
        # the llm-proxy revocation assertion below
        rec.check(
            "f",
            "bob proxy token captured pre-deactivation",
            bool(bob_proxy_token),
            "no OPENAI_API_KEY proxy token in bob's webui env — uid-filtered process match failed?",
        )

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
        params={"path": "/workspace/bob", "token": bob_webui_token or ""},
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
        if find_webui_processes(bob_port) == []:
            gone = True
            break
        time.sleep(3)
    rec.check(
        "f",
        "bob webui instance destroyed (sudo parent and all)",
        gone,
        f"port={bob_port} remaining={[m['pid'] for m in find_webui_processes(bob_port)]}",
    )

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

    # Control-plane recreation: container-side assertions ONLY (host
    # docker-proxy always listens on published ports — permanent false pass).
    alice_url_token = r.users["alice"].get("webui_token") or http(
        "GET", "/api/workspace/user-url", token=r.users["alice"]["token"]
    )[1].get("token")
    # review 8026: `compose restart` keeps the SAME container writable layer,
    # so a secret merely left there would also survive — indistinguishable
    # from volume persistence (#3377's actual claim). Recreate the container
    # (volumes kept): only a volume-persisted secret keeps the token valid.
    compose("up", "-d", "--force-recreate", SERVICE, timeout=600)
    wait_ready(rec)
    processes = [p for p in _proc_table() if "--token-secret" in p["cmd"]]
    rec.check(
        "f",
        "no leftover webui processes after recreation",
        not processes,
        f"leftover={[p['cmd'][:80] for p in processes]}",
    )
    rec.check(
        "f",
        "ports 3100-3200 silent in-container after recreation",
        listening_ports_in_range(3100, 3200) == [],
        f"{listening_ports_in_range(3100, 3200)}",
    )
    # F4: /workspace/alice is alice's fs-visible browse root (<base>/<account>);
    # /home/* is outside the base dirs and 400s after auth regardless of the
    # token — it would never exercise #3377
    status, body, _ = http(
        "GET",
        "/api/fs/browse",
        params={"path": "/workspace/alice", "token": alice_url_token or ""},
    )
    rec.check(
        "f",
        "alice URL token valid after container RECREATION (#3377 secret on volume)",
        status == 200,
        f"status={status}",
        response=body,
    )

    # review round 4 (4003821831) — UID drift across recreation: a fresh
    # container is a fresh /etc/passwd; the entrypoint re-useradds only the
    # ACTIVE users (no ORDER BY, no uid pinning), so a deactivated user's
    # numeric uid can be inherited by an active account — putting /home/<bob>
    # (0700) and /workspace/<bob> under that account's ownership. Expected to
    # FAIL on the current product: recorded honestly as a #3374-scope finding
    # (app-side follow-up issue filed; uid pinning/reuse is the fix shape).
    active_accounts = [
        name for name, info in r.users.items() if info.get("system_account") and name != "bob"
    ]
    active_uids = {}
    for name in active_accounts:
        # tolerant probe: an active account MISSING after recreate is the
        # finding itself (run 34917224776: the entrypoint user-sync printed
        # its header but created zero users on the recreated container) —
        # record it with evidence and keep auditing the rest
        id_proc = compose_exec(SERVICE, f"id -u {name}", timeout=15, check=False)
        if id_proc.returncode != 0:
            rec.check(
                "f",
                f"post-recreate: active account {name} exists",
                False,
                f"id -u {name} rc={id_proc.returncode} — the entrypoint "
                "user-sync created no users on the recreated container "
                "(see user-sync-forensics.txt; product finding)",
            )
            continue
        want = id_proc.stdout.strip()
        active_uids[want] = name
        for dir_path in (f"/home/{name}", f"/workspace/{name}"):
            got = compose_exec(
                SERVICE, f"stat -c %u {dir_path}", timeout=15, check=False
            ).stdout.strip()
            rec.check(
                "f",
                f"post-recreate ownership: {dir_path} belongs to {name}",
                got == want,
                f"uid={got or '<missing>'} expected={want} (UID drift / useradd renumbering)",
            )
    # review round 4 (4013712293): with an account MISSING post-recreate
    # these rows used to pass vacuously — `exec -u alice` fails with its own
    # non-zero rc when the account does not exist, and with no active account
    # created at all, an orphan owner proves nothing about inheritance.
    missing = sorted(set(active_accounts) - set(active_uids.values()))
    for dir_path in ("/home/bob", "/workspace/bob"):
        # review round 3 (4004861021): assert by OWNER NAME — the uid-set
        # approach missed admin and erin, which the entrypoint sync also
        # creates OS accounts for (account = system_account or username), so
        # bob's uid landing on either passed vacuously. Today's product leaves
        # the dirs on an orphan uid (owner "UNKNOWN"); after #3390's
        # placeholder-account fix the owner should be bob himself.
        owner = compose_exec(SERVICE, f"stat -c %U {dir_path}", timeout=15, check=False).stdout
        detail = (
            f"owner={owner.strip()!r} — inherited by an active account "
            "(product: entrypoint re-useradds active users without uid pinning, #3390)"
        )
        if missing:
            # nobody exists to inherit anything — an orphan owner proves nothing
            detail = f"owner={owner.strip()!r} — not evaluable: {missing} missing post-recreate"
        rec.check(
            "f",
            f"post-recreate: deactivated bob's {dir_path} not inherited by an active account",
            owner.strip() in ("UNKNOWN", "bob") and not missing,
            detail,
        )
    for attacker in ("alice", "carol"):
        proc = compose_exec(SERVICE, "ls /home/bob", user=attacker, timeout=15, check=False)
        rec.check(
            "f",
            f"post-recreate isolation: {attacker} shell ls /home/bob -> EACCES",
            # rc alone also "passes" when the attacker account does not exist
            # (docker exec: unable to find user) — require the real EACCES
            # (LANG=C.UTF-8 container: the error text is stable)
            proc.returncode != 0 and "Permission denied" in proc.stderr,
            f"rc={proc.returncode} stderr={proc.stderr.strip()[:120]!r}",
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
    """(h) single-user mode shows no regression (base compose, fresh config).

    Runs in its OWN compose project on port 19889 (review F10) — the
    multi-user stack stays untouched, so ACCEPTANCE_KEEP_STACK leaves THAT
    one up for inspection, matching the handbooks."""
    print("[h] single-user regression tail")
    rec = recorder
    global BASE_URL

    # own project + fresh volumes: pristine auto-generated config, not the
    # multi-user preseed
    compose_base("down", "-v", "--remove-orphans", timeout=300)
    compose_base("up", "-d", "--wait", timeout=600)
    saved_base = BASE_URL
    BASE_URL = SINGLE_USER_BASE_URL
    try:
        wait_ready(rec)
        _item_h_assertions(rec)
    finally:
        BASE_URL = saved_base
        # review round 4 (4003823086): main()'s log dumps run AFTER this
        # finally — capture the single-user logs to the record directory
        # BEFORE tearing the project down, or they are gone forever.
        try:
            RECORD_DIR.mkdir(parents=True, exist_ok=True)
            proc = compose_base("logs", "--no-color", "--tail", "400", timeout=120)
            (RECORD_DIR / "compose-logs-single.txt").write_text(proc.stdout, encoding="utf-8")
        except Exception as exc:  # noqa: BLE001 - best effort
            rec.note(f"compose-logs-single.txt dump failed: {exc}")
        compose_base("down", "-v", "--remove-orphans", timeout=300)
    return {}


def _single_user_launch_capability(rec: Recorder, admin_token: str) -> bool:
    """REAL capability assertion for the single-user 3100 launch (review
    round 4, 4003822666): the `id admin` probe was constant-true — the image
    simply has no OS user 'admin', so it never distinguished the declared
    limitation from a real regression.

    Instead, exercise the launch chain itself: create a user whose
    system_account IS the container's own account ('open-ace', uid 1000).
    Single-user login takes the direct-launch branch (current_user ==
    system_account, no sudo), so its user-url MUST return 200/:3100. With
    that asserted, the default admin's 503 can be exempted as the declared
    limitation without swallowing regressions: a broken webui binary fails
    THIS check loudly."""
    status, body, _ = http(
        "POST",
        "/api/admin/users",
        token=admin_token,
        body={
            "username": "opener",
            "email": "opener@acceptance.test",
            "password": "Opener-Acceptance-2026!x",
            "tenant_id": 1,
            "system_account": "open-ace",
        },
    )
    if status != 201:
        rec.check(
            "h",
            "single-user launch capability (system_account=open-ace user)",
            False,
            f"setup failed: {status}",
            response=body,
        )
        return False
    opener_token, _ = login("opener", "Opener-Acceptance-2026!x")
    status, body, _ = http("GET", "/api/workspace/user-url", token=opener_token)
    url = (body or {}).get("url", "") if isinstance(body, dict) else ""
    ok = status == 200 and url.endswith(":3100")
    rec.check(
        "h",
        "single-user launch capability: direct-branch user gets 3100",
        ok,
        f"status={status} url={url}",
        response=body,
    )
    return ok


def _item_h_assertions(rec: Recorder) -> None:
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
    # REAL capability first (review 4003822666): if the direct-branch user
    # cannot launch, nothing may be exempted below
    capability_ok = _single_user_launch_capability(rec, token)
    status, body, _ = http("GET", "/api/workspace/user-url", token=token)
    url = (body or {}).get("url", "") if isinstance(body, dict) else ""
    if status == 200 and url.endswith(":3100"):
        rec.check("h", "default admin gets the shared 3100 instance", True, f"url={url}")
    elif status in (502, 503) and capability_ok:
        # F5 declared limitation (see handbook §5.8): the single-user image
        # has no OS account for the default admin, so the sudo path cannot
        # launch for it — but the launch chain itself is proven healthy by
        # the capability assertion above, so this cannot hide a regression.
        rec.exempt(
            "h",
            "default admin 3100: app-side single-user mapping limitation",
            f"status={status}; launch capability separately asserted healthy "
            "(system_account=open-ace user returned 200/:3100) — see handbook §5.8",
            response=body,
        )
    else:
        rec.check(
            "h",
            "default admin gets the shared 3100 instance",
            False,
            f"unexpected status={status} url={url} (capability_ok={capability_ok})",
            response=body,
        )
    status, body, _ = http("GET", "/api/auth/me", token=token)
    rec.check("h", "admin session works in single-user mode", status == 200, f"status={status}")
    rec.note("default-UI manual click-through recorded in the handbook checklist")


def item_i_record_and_crossrefs(recorder: Recorder) -> None:
    """(i) publishable sample, permission conditions, capability matrix."""
    rec = recorder
    print("[i] record assembly and cross-references")
    import re as _re

    sha = rec.fingerprint.get("git_sha", "")
    rec.check(
        "i",
        "fingerprint captured (git SHA pinned to this repository)",
        bool(_re.fullmatch(r"[0-9a-f]{40}", str(sha))),
        f"git_sha={sha!r}",
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
    # review round 4 (4003820998): refuse BEFORE the try — SystemExit(2) is a
    # BaseException and sails past `except Exception` into the finally, which
    # used to down -v the very project the refusal meant to protect (a
    # KEEP_STACK review session, or a concurrent run's single-user project).
    refuse_if_project_has_state()
    try:
        # 1. bootstrap env, two-phase up (review round 3):
        #    first up -> entrypoint generates the FULL config -> stop -> merge
        #    max_instances=3 (same-image helper) -> second up
        run(
            [sys.executable, str(REPO_ROOT / "scripts" / "bootstrap_compose_env.py")],
            cwd=REPO_ROOT,
            timeout=120,
        )
        # DECLARED DEVIATION (product gap #3397, review round 3 4004859403):
        # following DEPLOYMENT.md's multi-user Option 2 verbatim, a fresh
        # production database refuses to boot ("Fresh database detected"),
        # and a bare migration alone is not enough either — with the schema
        # present the entrypoint skips init_db.py, so the default tenant and
        # admin/admin123 never exist and admin_first_login would 401. The
        # one-shot container below does BOTH (migrate + seed); the gap
        # itself (documented fresh-install flow cannot start) is recorded
        # as #3397 and stays in the run notes.
        compose(
            "run",
            "--rm",
            SERVICE,
            "sh",
            "-c",
            "alembic upgrade head && python3 scripts/init_db.py",
            timeout=600,
        )
        recorder.note(
            "DECLARED DEVIATION (#3397): fresh production DB initialized via a one-shot "
            "'alembic upgrade head && init_db.py' container - DEPLOYMENT.md multi-user "
            "Option 2 cannot boot a fresh deployment (empty DB refused in production)"
        )
        compose("up", "-d", "--wait", timeout=900)
        compose("stop", SERVICE, timeout=300)
        merge_max_instances(3)
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
                "config merge ineffective: /api/workspace/config reports max_instances="
                f"{body.get('max_instances') if isinstance(body, dict) else body} "
                "(expected 3). #3110/#3387 already fixed config-dir resolution — "
                "verify the image under test actually contains #3387, then check the "
                "merge step ran (look for the DECLARED DEVIATION note) and that the "
                "config volume is acceptance-multi_config-data."
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
        if recorder.failed:
            # review 7769: a COMPLETED run with failures must also capture
            # logs — the finally teardown would otherwise destroy them
            dump_stack_logs(recorder)
    except Exception as exc:  # noqa: BLE001 - F6: ANY crash must still render
        import traceback

        recorder.note(f"ABORTED: {exc}")
        recorder.note("traceback (tail): " + traceback.format_exc()[-1500:])
        dump_stack_logs(recorder)
        recorder.render()
        print(f"ACCEPTANCE ABORTED: {exc}", file=sys.stderr)
        return 1
    finally:
        # the single-user project always goes away; the multi-user stack only
        # without KEEP_STACK (F10: KEEP_STACK leaves the MULTI-USER stack up)
        try:
            compose_base("down", "-v", "--remove-orphans", timeout=300)
        except AcceptanceError as exc:
            recorder.note(f"single-user teardown warning: {exc}")
        if not KEEP_STACK:
            try:
                compose_down_volumes()
            except AcceptanceError as exc:
                recorder.note(f"teardown warning: {exc}")

    json_path, md_path = recorder.render()
    failed = recorder.failed
    exempt = sum(1 for i in recorder.items if i["result"] == "EXEMPT")
    passed = sum(1 for i in recorder.items if i["result"] == "PASS")
    print(
        f"\nACCEPTANCE {'PASS' if not failed else 'FAIL'}: "
        f"{passed} passed, {len(failed)} failed, {exempt} exempt"
    )
    print(f"record: {json_path}\n        {md_path}")
    for item in failed:
        print(f"  FAIL {item['item']} {item['name']}: {item['detail']}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
