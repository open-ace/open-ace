"""
Shared-namespace root provisioning in the Docker entrypoint (Issue #3379).

The multi-user acceptance run exposed a fresh-deployment gap: nothing in the
product created ``<base>/shared``, so ``POST /api/projects`` with
``create_dir: true`` ran ``sudo -u <user> mkdir -p`` against a root:root 0755
parent and every user's first shared-project creation EACCESed (HTTP 403).
The #3376 first-class ``<base>/shared/<name>`` namespace needs its root to
pre-exist, group-writable by ``openace-shared`` with setgid.

The provisioning block is tested FUNCTIONALLY (review round 2, 4004368482 —
the previous text assertions survived three targeted mutations): the block is
extracted from the entrypoint between its marker comments and executed under
``set -e`` with ``mkdir``/``chgrp``/``chmod``/``id``/``stat`` replaced by
recording function stubs, then the executed command sequences are asserted
per scenario. The app-side takeover guard lives in
``app/utils/workspace.py::_ensure_workspace_dirs`` and has its own tests in
``TestEnsureWorkspaceDirsSharedGuard`` below.
"""

import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent.parent

pytestmark = [pytest.mark.regression, pytest.mark.issue(3379)]

ENTRYPOINT = ROOT / "docker-entrypoint.sh"

START_MARKER = "# Ensure workspace base directory exists"
END_MARKER = "unset _base_dir _workspace_base_dirs"

# Executes the extracted block under `set -e` with stubbed system commands.
# Logs one line per stub invocation; a failure is injected via FAIL to
# exercise the warning-degradation path.
_HARNESS = """
CALLS_FILE="$TEST_TMP/calls"
touch "$CALLS_FILE"
# FAIL injects a failure ONLY for the shared-root steps (a failing BASE mkdir
# legitimately aborts — pre-existing behavior outside this provisioning).
mkdir() { echo "mkdir $*" >> "$CALLS_FILE"; if [ "$FAIL" = "mkdir" ]; then case "$*" in *"/shared") return 1;; esac; fi; return 0; }
chgrp() { echo "chgrp $*" >> "$CALLS_FILE"; [ "$FAIL" = "chgrp" ] && return 1; return 0; }
chmod() { echo "chmod $*" >> "$CALLS_FILE"; [ "$FAIL" = "chmod" ] && return 1; return 0; }
id()    { echo "id $*" >> "$CALLS_FILE"; [ -n "$ID_SHARED_OK" ] && [ "$1" = "shared" ] && return 0; return 1; }
stat()  { echo "stat $*" >> "$CALLS_FILE"; if [ -n "$STAT_OWNER" ]; then echo "$STAT_OWNER"; else echo "root"; fi; return 0; }
set -e
SHARED_GROUP=openace-shared
# the extracted block assigns WORKSPACE_DIR from WORKSPACE_BASE_DIR
WORKSPACE_BASE_DIR="$WORKSPACE_DIR_UNDER_TEST"
"""


def _extract_block() -> str:
    content = ENTRYPOINT.read_text(encoding="utf-8")
    start = content.index(START_MARKER)
    end = content.index(END_MARKER, start) + len(END_MARKER)
    block = content[start:end]
    assert 'mkdir -p "$_base_dir/shared"' in block
    return block


def _run_block(
    workspace_dir_value: str,
    *,
    id_shared_ok: bool = False,
    stat_owner: str = "",
    fail: str = "",
    precreate_shared: bool = False,
) -> str:
    """Run the extracted provisioning block with stubs; returns rc + call log.

    When the workspace value is the literal "TMP", a throwaway directory is
    used as the base (so scenarios can pre-create <base>/shared and exercise
    the -e branch of the collision guard)."""
    with tempfile.TemporaryDirectory() as tmp:
        ws = str(Path(tmp) / "base") if workspace_dir_value == "TMP" else workspace_dir_value
        if precreate_shared:
            Path(ws).mkdir(parents=True, exist_ok=True)
            (Path(ws) / "shared").mkdir(exist_ok=True)
        script = _HARNESS + _extract_block()
        env = {
            "PATH": "/usr/bin:/bin",
            "TEST_TMP": tmp,
            "WORKSPACE_DIR_UNDER_TEST": ws,
            "FAIL": fail,
        }
        if id_shared_ok:
            env["ID_SHARED_OK"] = "1"
        if stat_owner:
            env["STAT_OWNER"] = stat_owner
        proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env)
        calls_path = Path(tmp) / "calls"
        calls = calls_path.read_text() if calls_path.exists() else ""
        return f"rc={proc.returncode}\n{calls}"


class TestSharedNamespaceProvisioning:
    def test_provisions_group_writable_setgid_root(self):
        log = _run_block("/ws")
        assert "mkdir -p /ws" in log
        assert "mkdir -p /ws/shared" in log
        assert "chgrp openace-shared /ws/shared" in log
        assert "chmod 3775 /ws/shared" in log, "sticky bit required (cross-tenant rename guard)"

    def test_comma_list_iterates_and_trims(self):
        log = _run_block("  /a , /b  ")
        assert "mkdir -p /a/shared" in log and "mkdir -p /b/shared" in log
        assert (
            log.count("chmod 3775") == 2
        ), "exactly two bases provisioned (empty/padded segments skipped)"
        assert not any(", /" in ln or "/," in ln for ln in log.splitlines()), "no literal comma dir"

    def test_skips_when_real_shared_account_exists(self):
        log = _run_block("/ws", id_shared_ok=True)
        assert "id shared" in log, "the guard probe must run"
        assert "chmod 3775 /ws/shared" not in log, "guard must skip provisioning on collision"

    def test_skips_when_existing_path_not_root_owned(self):
        log = _run_block("TMP", stat_owner="someoneelse", precreate_shared=True)
        assert (
            "chgrp openace-shared" not in log
        ), "non-root-owned existing root must not be re-provisioned"

    def test_provisioning_failure_degrades_to_warning_not_abort(self):
        for fail in ("mkdir", "chgrp", "chmod"):
            log = _run_block("/ws", fail=fail)
            assert log.startswith("rc=0"), f"{fail} failure must not abort the entrypoint"

    def test_block_sits_after_group_creation_in_entrypoint(self):
        """The chgrp references $SHARED_GROUP, so the entrypoint must create
        the group before provisioning (ordering; textual by necessity — the
        functional scenarios cannot see the surrounding file)."""
        content = ENTRYPOINT.read_text(encoding="utf-8")
        assert content.index("groupadd") < content.index('mkdir -p "$_base_dir/shared"')


class TestPostSyncGroupEnrollment:
    def test_enrollment_pass_exists_after_db_sync(self):
        """Review round 2 (4004368045): on a recreated container the pre-sync
        enrollment pass no-ops (empty /etc/passwd) and useradd adds no
        supplementary groups — a second pass must run AFTER the DB sync."""
        content = ENTRYPOINT.read_text(encoding="utf-8")
        sync_end = content.index("open-ace-user-sync.log")
        content.index('usermod -aG "$SHARED_GROUP"', sync_end)
        assert "Syncing workspace users from database" in content[:sync_end]


class _FakeProc:
    def __init__(self, rc: int, out: str):
        self.returncode = rc
        self.stdout = out
        self.stderr = ""


class TestEnsureWorkspaceDirsSharedGuard:
    """The app-side takeover guard (review round 2, 4004367597)."""

    @pytest.fixture()
    def stubbed(self, monkeypatch, tmp_path):
        from app.utils import workspace as ws

        calls: list[tuple] = []
        monkeypatch.setattr(
            ws, "run_as_root_if_needed", lambda cmd: calls.append(tuple(cmd)) or _FakeProc(0, "")
        )
        monkeypatch.setattr(ws, "_is_wrapper_available", lambda w: False)
        # review round 3: setting ws._is_docker_multi_user_mode directly in
        # tests leaked across the session — route through monkeypatch
        modes: list[bool] = []
        monkeypatch.setattr(ws, "_is_docker_multi_user_mode", lambda: bool(modes))
        ws._acceptance_mode_flag = modes

        def fake_run(cmd, **kw):
            calls.append(tuple(cmd))
            out = "0" if "id" in cmd[:2] else ""
            return _FakeProc(0, out)

        monkeypatch.setattr(ws.subprocess, "run", fake_run)
        return ws, calls, str(tmp_path)

    def test_guard_refuses_shared_account_in_multi_user_mode(self, stubbed):
        ws, calls, base = stubbed
        ws._acceptance_mode_flag.append(True)
        ws._ensure_workspace_dirs("shared", base)
        touched = [
            c for c in calls if c and str(c[0]).split("/")[-1].startswith(("chown", "mkdir"))
        ]
        assert not touched, "the namespace root must not be taken over"

    def test_guard_inactive_for_normal_accounts(self, stubbed):
        ws, calls, base = stubbed
        ws._acceptance_mode_flag.append(True)
        ws._ensure_workspace_dirs("alice", base)
        assert any(str(c[0]).split("/")[-1].startswith("chown") for c in calls)

    def test_guard_inactive_in_single_user_mode(self, stubbed):
        ws, calls, base = stubbed
        ws._ensure_workspace_dirs("shared", base)
        assert any(
            str(c[0]).split("/")[-1].startswith("chown") for c in calls
        ), "single-user/package mode has no namespace root; 'shared' is an ordinary account there"
