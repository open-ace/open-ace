#!/usr/bin/env python3
"""Multi-user deployment smoke (#3289 + #3293).

Runs INSIDE the production image as root (the ci.yml docker job's
"Multi-user deployment smoke" step) and hard-asserts the root+multi-user
positive paths that no PR lane can cover:

  A1  ensure_system_user creates a REAL system user through the real
      openace-useradd wrapper chain, plus the validation negatives.
  A2  Root-direct multi-user collection reads permission-700 homes and
      attributes rows per user. This is the Docker multi-user product
      path (FETCH_USE_SUDO=false, both compose services run as root);
      the wrapper+privilege-drop shape belongs to the package/systemd
      deployment and cannot run here (the drop user does not exist in
      the image and the dropped process has no root re-read).
  A3  The service account (open-ace, uid 1000) cannot read another
      user's 700 home while root can (cross-user read regression).
  A4  100-real-user scale: every user's session lands attributed
      (SMOKE_USER_COUNT overrides; fixtures carry per-user-unique
      message ids because daily_messages has
      UNIQUE(date, tool_name, message_id, host_name)).
  A5  The fetch wrapper, installed in its package-deployment shape,
      runs its validated invocation as root and pseudonymizes the
      audit-log caller (#3292).

Exit status is non-zero on the first failed assertion.
"""

import json
import os
import pwd
import re
import shutil
import sqlite3
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

AUDIT_LOG = Path("/var/log/openace/fetch-audit.log")
CONFIG_PATH = Path("/etc/openace/config.json")
DB_PATH = Path("/tmp/smoke-data/smoke.sqlite")
WRAPPER_DEST = Path("/usr/local/bin/openace-fetch-wrapper")
OPT_SCRIPTS = Path("/opt/open-ace/scripts")

PSEUDONYM_RE = re.compile(r"^[a-z]?\*\*\*-[0-9a-f]{8}$")


def check(condition, message):
    if not condition:
        raise SystemExit(f"SMOKE FAIL: {message}")
    print(f"  ok: {message}")


def run(cmd, **kwargs):
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


# ── fixture: one real qwen session per user ─────────────────────────


def write_session(user: str, tag: str) -> Path:
    """Write a minimal real-shape qwen session under /home/<user>/.qwen.

    The two-entry shape (user -> assistant with usageMetadata) is what
    process_jsonl_file parses into daily_messages; the file sits DIRECTLY
    inside the project directory (find_all_qwen_project_dirs only sees
    jsonl at projects/<proj>/*.jsonl or projects/<proj>/<x>/chats/*.jsonl);
    ids embed per-user uniqueness so the
    UNIQUE(date, tool_name, message_id, host_name) constraint cannot
    collapse distinct users into one row.
    """
    project_dir = Path("/home") / user / ".qwen" / "projects" / "smoke-proj"
    project_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    session_id = f"session-{uuid.uuid4().hex[:8]}"
    user_uuid = f"{tag}-user-{uuid.uuid4().hex[:8]}"
    asst_uuid = f"{tag}-asst-{uuid.uuid4().hex[:8]}"
    entries = [
        {
            "type": "user",
            "uuid": user_uuid,
            "parentUuid": None,
            "timestamp": now,
            "sessionId": session_id,
            "message": {"role": "user", "parts": [{"text": f"smoke hello from {tag}"}]},
        },
        {
            "type": "assistant",
            "uuid": asst_uuid,
            "parentUuid": user_uuid,
            "timestamp": now,
            "sessionId": session_id,
            "message": {"role": "assistant", "parts": [{"text": f"smoke reply for {tag}"}]},
            "usageMetadata": {
                "promptTokenCount": 100,
                "candidatesTokenCount": 50,
                "totalTokenCount": 150,
            },
        },
    ]
    session_file = project_dir / f"{tag}-{uuid.uuid4().hex[:8]}.jsonl"
    session_file.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
    # The fixtures are written by root; hand them to the user so the tree is
    # deployment-shaped (the 0700 home traversal that A3 relies on is checked
    # separately in smoke_collection — root is never blocked by ownership).
    qwen_root = Path("/home") / user / ".qwen"
    check(
        run(["chown", "-R", f"{user}:", str(qwen_root)]).returncode == 0,
        f"fixtures chowned to {user}",
    )
    return session_file


def run_collection() -> int:
    """Root-direct multi-user collection (the Docker multi-user product path)."""
    result = run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "fetch_qwen.py"),
            "--multi-user",
            "--recent",
            "--days",
            "1",
            "--config",
            str(CONFIG_PATH),
        ]
    )
    if result.returncode != 0:
        print(result.stdout[-2000:], file=sys.stderr)
        print(result.stderr[-2000:], file=sys.stderr)
    check(result.returncode == 0, "fetch_qwen --multi-user exit 0")
    return result.returncode


def distinct_senders() -> set:
    conn = sqlite3.connect(str(DB_PATH))
    try:
        rows = conn.execute("SELECT DISTINCT sender_id FROM daily_messages").fetchall()
    finally:
        conn.close()
    return {r[0] for r in rows}


# ── A1: ensure_system_user positive path (#3289) ────────────────────


def smoke_system_user():
    print("[A1] ensure_system_user positive path (#3289)")
    from app.utils.workspace import ensure_system_user

    user = "smokeu1"
    check(ensure_system_user(user) is True, f"ensure_system_user({user!r}) -> True")
    check(run(["id", user]).returncode == 0, f"id {user} exit 0 (real useradd ran)")
    pw = pwd.getpwnam(user)
    workspace_qwen = Path("/workspace") / user / ".qwen"
    check(workspace_qwen.is_dir(), f"{workspace_qwen} exists")
    stat = workspace_qwen.stat()
    check(stat.st_uid == pw.pw_uid, f"{workspace_qwen} owned by {user}")
    check(ensure_system_user(user) is True, "second ensure_system_user idempotent")

    # Validation negatives (real branches, no mocks).
    check(ensure_system_user("Bad_Name") is False, "invalid username format -> False")
    check(ensure_system_user("") is False, "empty username -> False")
    check(ensure_system_user("smokeu1", uid=500) is False, "reserved uid 500 -> False")


# ── A2: permission-700 collection, per-user attribution (#3293) ─────


A2_USERS = ["smokeu2", "smokeu3"]


def smoke_collection():
    print("[A2] root-direct multi-user collection over 700 homes (#3293)")
    users = A2_USERS
    from app.utils.workspace import ensure_system_user

    sample_files = {}
    for user in users:
        check(ensure_system_user(user) is True, f"user {user} created")
        sample_files[user] = write_session(user, tag=user)
    # Debian useradd -m homes default to 700; assert the premise A3 relies on.
    mode = (Path("/home") / "smokeu2").stat().st_mode & 0o777
    check(mode == 0o700, f"/home/smokeu2 is 700 (got {oct(mode)})")

    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps({"database": {"type": "sqlite", "url": f"sqlite:///{DB_PATH}"}}),
        encoding="utf-8",
    )

    run_collection()

    conn = sqlite3.connect(str(DB_PATH))
    try:
        (n_rows,) = conn.execute("SELECT COUNT(*) FROM daily_messages").fetchone()
    finally:
        conn.close()
    check(n_rows > 0, f"daily_messages has rows ({n_rows})")
    senders = distinct_senders()
    check(set(users) <= senders, f"attribution covers {users} (got {sorted(senders)})")
    return sample_files["smokeu2"]


# ── A3: cross-user read regression (#3293) ──────────────────────────


def smoke_cross_user_read(session_file: Path):
    print("[A3] service account cannot read other users' homes (#3293)")
    pw = pwd.getpwnam("open-ace")
    check(pw.pw_uid == 1000, "service account open-ace exists with uid 1000")
    # The denial must come from the 0700 mode of a home that genuinely
    # belongs to the user — otherwise real users would be locked out of
    # their own homes and this regression check would be vacuous.
    home = Path("/home") / "smokeu2"
    owner = pwd.getpwuid(home.stat().st_uid).pw_name
    check(owner == "smokeu2", f"{home} owned by smokeu2 (got {owner})")
    denied = run(["runuser", "-u", "open-ace", "--", "head", "-c", "1", str(session_file)])
    check(denied.returncode != 0, "runuser -u open-ace head -> permission denied")
    allowed = run(["head", "-c", "1", str(session_file)])
    check(allowed.returncode == 0, "root reads the same file (positive control)")


# ── A4: 100-real-user scale (#3293) ──────────────────────────────────


def smoke_scale():
    count = int(os.environ.get("SMOKE_USER_COUNT", "100"))
    print(f"[A4] {count}-real-user scale collection (#3293)")
    from app.utils.workspace import ensure_system_user

    for i in range(count):
        user = f"smokes{i:03d}"
        if not ensure_system_user(user):
            raise SystemExit(f"SMOKE FAIL: ensure_system_user({user!r}) -> False")
        write_session(user, tag=f"s{i:03d}")
    print(f"  ok: {count} users + fixtures created")

    run_collection()

    # A2's users live in the same database — the structural coupling keeps
    # this count correct if A2's population ever changes.
    expected = count + len(A2_USERS)
    senders = distinct_senders()
    check(
        len(senders) == expected, f"distinct attributed senders == {expected} (got {len(senders)})"
    )


# ── A5: wrapper invocation + audit pseudonymization (#3292) ─────────


def smoke_wrapper_audit():
    print("[A5] fetch wrapper (package shape) + audit pseudonymization")
    OPT_SCRIPTS.mkdir(parents=True, exist_ok=True)
    shutil.copy2(REPO_ROOT / "scripts" / "fetch_qwen.py", OPT_SCRIPTS / "fetch_qwen.py")
    shutil.copytree(REPO_ROOT / "scripts" / "shared", OPT_SCRIPTS / "shared", dirs_exist_ok=True)
    shutil.copy2(REPO_ROOT / "scripts" / "openace-fetch-wrapper", WRAPPER_DEST)
    WRAPPER_DEST.chmod(0o755)

    env = dict(os.environ)
    # RUN_USER=root skips the package-deployment privilege drop (the drop
    # user does not exist in this image — see module docstring); USER makes
    # the audit caller a REAL username so pseudonymization is exercised,
    # not the literal "unknown" fallback.
    env.update({"USER": "smokeu2", "RUN_USER": "root", "AUDIT_LOG": str(AUDIT_LOG)})
    result = run(
        [
            str(WRAPPER_DEST),
            "fetch_qwen",
            "--days",
            "1",
            "--multi-user",
            "--recent",
            "--config",
            str(CONFIG_PATH),
        ],
        env=env,
    )
    if result.returncode != 0:
        print(result.stdout[-2000:], file=sys.stderr)
        print(result.stderr[-2000:], file=sys.stderr)
    check(result.returncode == 0, "wrapper invocation exit 0")

    content = AUDIT_LOG.read_text(encoding="utf-8")
    callers = re.findall(r"\| caller=([^|]*) \|", content)
    check(bool(callers), "audit log has caller fields")
    check(
        all(PSEUDONYM_RE.match(c) for c in callers),
        f"every caller pseudonymized (got {callers[:3]!r}...)",
    )
    check("smokeu2" not in content, "raw smoke username absent from audit log")


def main():
    if os.geteuid() != 0:
        raise SystemExit("SMOKE FAIL: must run as root (--user 0)")
    check(os.environ.get("WORKSPACE_BASE_DIR") == "/workspace", "WORKSPACE_BASE_DIR=/workspace")
    if sys.platform != "linux":
        raise SystemExit("SMOKE FAIL: linux only (deployment smoke)")

    smoke_system_user()
    session_file = smoke_collection()
    smoke_cross_user_read(session_file)
    smoke_scale()
    smoke_wrapper_audit()
    print("SMOKE PASS: multi-user deployment smoke all green")


if __name__ == "__main__":
    main()
