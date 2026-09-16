"""
Shared-namespace root provisioning in the Docker entrypoint (Issue #3379)
and tenant-scoped shared-group sync (Issue #3396).

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

The #3396 tenant-scoped shared-group sync (also in the entrypoint) is tested
the same way: the quoted ``PY_SYNC_GROUPS_EOF`` heredoc is extracted verbatim
and executed under stubbed ``psycopg2``/``subprocess`` modules, asserting the
per-tenant enrollment and the legacy-directory reconcile command sequences.
"""

import os
import shutil
import subprocess
import sys
import tempfile
from collections import deque
from pathlib import Path
from types import ModuleType, SimpleNamespace

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
# /etc/openace (the openace-chown.conf dir) ALWAYS fails in the harness: the
# conf write must degrade to its WARNING branch, and a root-run harness must
# never touch the host's real /etc/openace (PR #3402 review).
mkdir() { echo "mkdir $*" >> "$CALLS_FILE"; case "$*" in *"/etc/openace"*) return 1;; esac; if [ "$FAIL" = "mkdir" ]; then case "$*" in *"/shared") return 1;; esac; fi; return 0; }
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


def _bash_candidates() -> list[str]:
    """/bin/bash (3.2 on macOS) plus a Homebrew bash 5 when installed — the
    CI runners and the container run bash 5, whose `set -e` aborts on a
    failed `{ ...; } > file` redirection where 3.2 does not (PR #3402
    review). Proving the degradation on BOTH locally pins the CI behavior."""
    candidates = ["/bin/bash"]
    for extra in ("/opt/homebrew/bin/bash", "/usr/local/bin/bash"):
        if (
            Path(extra).is_file()
            and "version 5"
            in subprocess.run([extra, "--version"], capture_output=True, text=True).stdout
        ):
            candidates.append(extra)
    return candidates


def _run_block(
    workspace_dir_value: str,
    *,
    id_shared_ok: bool = False,
    stat_owner: str = "",
    fail: str = "",
    precreate_shared: bool = False,
    bash_bin: str = "/bin/bash",
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
        proc = subprocess.run([bash_bin, "-c", script], capture_output=True, text=True, env=env)
        calls_path = Path(tmp) / "calls"
        calls = calls_path.read_text() if calls_path.exists() else ""
        return f"rc={proc.returncode}\n{calls}"


class TestSharedNamespaceProvisioning:
    def test_provisions_group_writable_setgid_root(self):
        log = _run_block("/ws")
        assert "mkdir -p /ws" in log
        assert "mkdir -p /ws/shared" in log
        assert "chgrp openace-shared /ws/shared" in log
        assert "chmod 3770 /ws/shared" in log, (
            "sticky bit required (cross-tenant rename guard) and NO others "
            "bits — others r-x let non-member processes list shared project names"
        )

    def test_comma_list_iterates_and_trims(self):
        log = _run_block("  /a , /b  ")
        assert "mkdir -p /a/shared" in log and "mkdir -p /b/shared" in log
        assert (
            log.count("chmod 3770") == 2
        ), "exactly two bases provisioned (empty/padded segments skipped)"
        assert not any(", /" in ln or "/," in ln for ln in log.splitlines()), "no literal comma dir"

    def test_skips_when_real_shared_account_exists(self):
        log = _run_block("/ws", id_shared_ok=True)
        assert "id shared" in log, "the guard probe must run"
        assert "chmod 3770 /ws/shared" not in log, "guard must skip provisioning on collision"

    def test_skips_when_existing_path_not_root_owned(self):
        log = _run_block("TMP", stat_owner="someoneelse", precreate_shared=True)
        assert (
            "chgrp openace-shared" not in log
        ), "non-root-owned existing root must not be re-provisioned"

    def test_provisioning_failure_degrades_to_warning_not_abort(self):
        # PR #3402 review: run under BOTH bash 3.2 and bash 5 (when
        # installed). Bash 5 aborts under set -e on a failed redirection —
        # the openace-chown.conf write used to sit in a then-branch, which
        # is what turned this test red on CI (the harness mkdir stub
        # succeeds, the real write to /etc/openace fails on non-root
        # runners) while passing on macOS bash 3.2. The write now lives in
        # the if-condition itself, so the failure degrades to the WARNING on
        # both versions.
        for bash_bin in _bash_candidates():
            for fail in ("mkdir", "chgrp", "chmod"):
                log = _run_block("/ws", fail=fail, bash_bin=bash_bin)
                assert log.startswith(
                    "rc=0"
                ), f"{fail} failure must not abort the entrypoint ({bash_bin})"

    def test_block_sits_after_group_creation_in_entrypoint(self):
        """The chgrp references $SHARED_GROUP, so the entrypoint must create
        the group before provisioning (ordering; textual by necessity — the
        functional scenarios cannot see the surrounding file)."""
        content = ENTRYPOINT.read_text(encoding="utf-8")
        assert content.index("groupadd") < content.index('mkdir -p "$_base_dir/shared"')


class TestChownConfWrite:
    """PR #3402 review: the boot-time openace-chown.conf generation.

    Two findings fixed here: (1) the ``{ ...; } > file`` write sat in a
    then-branch, so under bash 5 ``set -e`` a failed redirection (read-only
    /etc/openace mount, unwritable conf) exited the entrypoint — a crash
    loop, the exact opposite of the surrounding "degrade to a warning"
    contract; (2) the emitted conf was a SOURCED shell array, so a ``"`` or
    ``$(...)`` inside WORKSPACE_BASE_DIR became code the root-run wrapper
    executes. The conf is now data — one prefix per line, character-filtered
    — and the write is part of the if-condition."""

    CONF_BLOCK_START = '_chown_conf_dir="/etc/openace"'
    CONF_BLOCK_END = "unset _wb _wb_raw _wb_list _chown_conf_dir _chown_conf"

    @classmethod
    def _extract_conf_block(cls) -> str:
        content = ENTRYPOINT.read_text(encoding="utf-8")
        start = content.index(cls.CONF_BLOCK_START)
        end = content.index(cls.CONF_BLOCK_END, start) + len(cls.CONF_BLOCK_END)
        return content[start:end]

    @classmethod
    def _run_conf_block(cls, workspace_base_dir: str) -> tuple[int, str, str, str | None]:
        """Run the extracted block with /etc/openace redirected into a tmp
        dir; returns (rc, stdout, stderr, conf content or None)."""
        import shlex

        with tempfile.TemporaryDirectory() as tmp:
            conf_dir = f"{tmp}/etc-openace"
            block = cls._extract_conf_block().replace('"/etc/openace"', f'"{conf_dir}"')
            script = f"set -e\nWORKSPACE_BASE_DIR={shlex.quote(workspace_base_dir)}\n{block}"
            proc = subprocess.run(
                ["bash", "-c", script],
                capture_output=True,
                text=True,
                env={"PATH": "/usr/bin:/bin"},
            )
            conf_path = Path(conf_dir) / "openace-chown.conf"
            content = conf_path.read_text() if conf_path.is_file() else None
            return proc.returncode, proc.stdout, proc.stderr, content

    @staticmethod
    def _prefix_lines(content: str) -> list[str]:
        return [ln for ln in content.splitlines() if ln and not ln.startswith("#")]

    def test_writes_one_prefix_per_line_from_base_dirs(self):
        rc, _out, _err, content = self._run_conf_block(" /data , srv/ws/ ,/,//")
        assert rc == 0
        assert content is not None
        # "/" and "//" entries vanish (a "/" prefix would disable the
        # wrapper's path guard entirely); relative segments are rooted;
        # /home/ is always appended
        assert self._prefix_lines(content) == ["/data/", "/srv/ws/", "/home/"]
        # no shell syntax is ever emitted — the file is data, not code
        assert "ALLOWED_PREFIXES" not in content

    def test_unsafe_entries_are_skipped_not_emitted(self):
        rc, _out, err, content = self._run_conf_block('/da ta","$(reboot),/ok')
        assert rc == 0, "an unsafe entry must be skipped, never abort the boot"
        assert self._prefix_lines(content) == ["/ok/", "/home/"]
        assert "skipping unsafe" in err

    def test_write_failure_degrades_to_warning_under_bash5_too(self):
        """A conf path that cannot be written (here: it is a DIRECTORY, so
        the redirection fails for root and non-root alike) must produce the
        WARNING and rc=0 — on bash 3.2 AND bash 5, pinning the CI behavior
        (bash 5 aborts under set -e where 3.2 did not)."""
        with tempfile.TemporaryDirectory() as tmp:
            conf_dir = f"{tmp}/etc-openace"
            Path(conf_dir).mkdir()
            # pre-create the conf path as a directory: mkdir -p succeeds,
            # the `> file` redirection cannot
            Path(conf_dir, "openace-chown.conf").mkdir()
            block = self._extract_conf_block().replace('"/etc/openace"', f'"{conf_dir}"')
            script = f"set -e\nWORKSPACE_BASE_DIR=/ws\n{block}"
            for bash_bin in _bash_candidates():
                proc = subprocess.run(
                    [bash_bin, "-c", script],
                    capture_output=True,
                    text=True,
                    env={"PATH": "/usr/bin:/bin"},
                )
                assert proc.returncode == 0, f"write failure must not abort ({bash_bin})"
                assert "WARNING: could not write" in proc.stdout, bash_bin


class TestTenantSharedGroupSync:
    """Issue #3396: the entrypoint's DB-driven tenant-group enrollment pass.

    The block replaced the two /home-glob ``usermod`` passes (a directory
    name cannot reveal its tenant): it is a quoted ``PY_SYNC_GROUPS_EOF``
    heredoc executed after the main user sync, enrolling every ACTIVE DB
    user into the global namespace group plus the tenant content group, and
    reconciling legacy shared project dirs onto the tenant group. Tested
    FUNCTIONALLY per the #3379 convention: extracted verbatim and run under
    stubbed psycopg2/subprocess modules.
    """

    # The class covers both the #3379 extraction convention and the #3396
    # sync semantics themselves (PR #3402 review): the file-level issue
    # marker is 3379, so the #3396 defect regressions carry it here too.
    pytestmark = [pytest.mark.issue(3396)]

    HEREDOC_TAG = "PY_SYNC_GROUPS_EOF"

    @staticmethod
    def _extract_sync_code() -> str:
        content = ENTRYPOINT.read_text(encoding="utf-8")
        start = content.index(f"<<'{TestTenantSharedGroupSync.HEREDOC_TAG}'")
        body_start = content.index("\n", start) + 1
        end = content.index(f"\n{TestTenantSharedGroupSync.HEREDOC_TAG}\n", body_start)
        return content[body_start:end]

    @staticmethod
    def _run_sync(
        monkeypatch,
        tmp_path: Path,
        *,
        user_rows: list[tuple],
        project_rows: list[tuple],
        existing_groups: dict[str, str | tuple[str, str]],
        reclaim_rows: list[tuple] | None = None,
        system_groups: dict[str, list[str]] | None = None,
        links: dict[str, str] | None = None,
        missing_accounts: set[str] | None = None,
        ambiguous_accounts: set[str] | None = None,
        getent_rc_sequences: dict[str, list[int]] | None = None,
    ) -> list[tuple]:
        """Execute the extracted block under stubs; returns recorded commands.

        *existing_groups* maps an existing project path to what ``stat``
        would report for it — either a bare group name (mode reported as
        2775, i.e. WRONG, so the reconcile's group+mode fast path does not
        skip) or a ``(group, mode)`` tuple for exact control (missing paths
        stat-fail, exercising the reconcile's own groupadd path).

        *reclaim_rows* feeds the third query (the no-longer-shared RECLAIM
        pass; 3-tuples ``path, tenant_id, system_account``); defaults to
        none.

        *system_groups* feeds ``getent group``: existing OS groups mapped to
        their member lists (the membership-reconcile pass converges tenant
        groups onto the DB's desired list).

        *links* creates symlinks (rel path -> absolute target) AFTER the
        existing_groups placeholders are laid down (a placeholder the map
        pre-created is replaced by the link) — for the round-5 N1
        symlink-alias scenarios. *missing_accounts* makes ``getent passwd``
        fail for those names with rc=2 (ghost OS accounts);
        *ambiguous_accounts* fail with rc=1 (transient NSS failure, both
        attempts); *getent_rc_sequences* scripts per-call return codes for
        a member (consumed front-first, then the defaults apply) — for the
        retry-recovers scenario.
        """
        code = TestTenantSharedGroupSync._extract_sync_code()
        (tmp_path / "shared").mkdir(parents=True, exist_ok=True)
        group_by_abs_path: dict[str, tuple[str, str]] = {}
        for rel, reported in existing_groups.items():
            abs_dir = tmp_path / rel
            abs_dir.mkdir(parents=True, exist_ok=True)
            group, mode = reported if isinstance(reported, tuple) else (reported, "2775")
            group_by_abs_path[str(abs_dir)] = (group, mode)
        for rel, target in (links or {}).items():
            link_path = tmp_path / rel
            if link_path.is_symlink() or link_path.exists():
                if link_path.is_dir() and not link_path.is_symlink():
                    shutil.rmtree(link_path)
                else:
                    link_path.unlink()
            os.symlink(target, link_path)

        calls: list[tuple] = []
        executed_sql: list[str] = []

        def fake_run(cmd, **_kwargs):
            calls.append(tuple(cmd))
            if cmd[:2] == ["getent", "group"]:
                lines = [
                    f"{name}:x:{1000 + i}:{','.join(members)}"
                    for i, (name, members) in enumerate(sorted((system_groups or {}).items()))
                ]
                return SimpleNamespace(
                    returncode=0,
                    stdout="\n".join(lines) + ("\n" if lines else ""),
                    stderr="",
                )
            if cmd[:2] == ["getent", "passwd"]:
                # the account-existence model for round-5 N4 + the NSS
                # transient scenarios: everything resolves except the
                # flagged ghosts (rc=2), the flagged ambiguous members
                # (rc=1, both attempts), and any scripted rc sequence
                name = cmd[2]
                seqs = getent_rc_sequences or {}
                if name in seqs and seqs[name]:
                    rc = seqs[name].pop(0)
                    out = "" if rc else f"{name}:x:1042:1042::/:/bin/sh\n"
                    return SimpleNamespace(returncode=rc, stdout=out, stderr="")
                if name in (ambiguous_accounts or frozenset()):
                    return SimpleNamespace(returncode=1, stdout="", stderr="")
                if name in (missing_accounts or frozenset()):
                    return SimpleNamespace(returncode=2, stdout="", stderr="")
                return SimpleNamespace(
                    returncode=0, stdout=f"{name}:x:1042:1042::/:/bin/sh\n", stderr=""
                )
            if cmd[:3] == ["stat", "-c", "%G %a"]:
                reported = group_by_abs_path.get(cmd[3])
                if reported is None:
                    return SimpleNamespace(returncode=1, stdout="", stderr="no such file")
                return SimpleNamespace(
                    returncode=0, stdout=f"{reported[0]} {reported[1]}\n", stderr=""
                )
            if cmd[:2] == ["stat", "-c"]:
                # reclaim-pass probe (group only)
                reported = group_by_abs_path.get(cmd[3])
                if reported is None:
                    return SimpleNamespace(returncode=1, stdout="", stderr="no such file")
                return SimpleNamespace(returncode=0, stdout=f"{reported[0]}\n", stderr="")
            if cmd[:2] == ["id", "-u"] or cmd[:2] == ["id", "-g"]:
                return SimpleNamespace(returncode=0, stdout="1042\n", stderr="")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        fake_subprocess = ModuleType("subprocess")
        fake_subprocess.run = fake_run

        responses = deque(
            [
                ("FROM users", user_rows),
                ("FROM projects WHERE", project_rows),
                ("FROM projects p", reclaim_rows or []),
            ]
        )

        class _Cursor:
            def execute(self, sql):
                executed_sql.append(" ".join(sql.split()))
                normalized = " ".join(sql.split())
                # Match the queued response by SQL fragment: the sync runs
                # three queries (enrollment, reconcile, reclaim) whose FROM
                # clauses are textually distinct.
                for frag, rows in responses:
                    if frag in normalized:
                        self._rows = rows
                        return
                self._rows = []

            def fetchall(self):
                return self._rows

        class _Conn:
            cursor = lambda self: _Cursor()  # noqa: E731
            close = lambda self: None  # noqa: E731

        fake_psycopg2 = ModuleType("psycopg2")
        fake_psycopg2.connect = lambda _url: _Conn()

        monkeypatch.setitem(sys.modules, "subprocess", fake_subprocess)
        monkeypatch.setitem(sys.modules, "psycopg2", fake_psycopg2)
        monkeypatch.setenv("DATABASE_URL", "postgresql://stub")
        monkeypatch.setenv("WORKSPACE_BASE_DIR", str(tmp_path))
        import contextlib
        import io

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            try:
                exec(compile(code, "<entrypoint-shared-group-sync>", "exec"), {"__name__": "sync"})
            except SystemExit as exc:
                # the hardened sync exits 1 when any step failed — scenarios
                # exercising a loud failure record it instead of crashing
                calls.append(("__EXIT__", exc.code))
        calls.insert(0, ("__SQL__", *executed_sql))
        # stdout lands LAST: existing assertions index from the front
        calls.append(("__STDOUT__", buf.getvalue()))
        return calls

    def test_enrolls_each_user_into_global_and_tenant_groups(self, monkeypatch, tmp_path):
        calls = self._run_sync(
            monkeypatch,
            tmp_path,
            user_rows=[
                ("alice-acct", 1),
                ("bob-acct", 1),
                ("carol-acct", 2),
            ],
            project_rows=[],
            existing_groups={},
        )
        # tenant-1 members: global + openace-shared-1
        assert ("usermod", "-aG", "openace-shared", "alice-acct") in calls
        assert ("usermod", "-aG", "openace-shared-1", "alice-acct") in calls
        assert ("usermod", "-aG", "openace-shared-1", "bob-acct") in calls
        # tenant-2 member must NOT land in tenant-1's group
        assert ("usermod", "-aG", "openace-shared-2", "carol-acct") in calls
        assert ("usermod", "-aG", "openace-shared-1", "carol-acct") not in calls
        # groups are created before use
        assert calls.index(("groupadd", "-f", "openace-shared-1")) < calls.index(
            ("usermod", "-aG", "openace-shared-1", "alice-acct")
        )

    def test_null_tenant_maps_to_pseudo_group_zero(self, monkeypatch, tmp_path):
        calls = self._run_sync(
            monkeypatch,
            tmp_path,
            user_rows=[("admin-acct", None)],
            project_rows=[],
            existing_groups={},
        )
        assert ("usermod", "-aG", "openace-shared-0", "admin-acct") in calls

    def test_no_username_fallback_when_system_account_missing(self, monkeypatch, tmp_path):
        """PR #3402 review: ``system_account or username`` crossed tenant
        boundaries — an unmapped user's username may equal another user's
        system_account, and the sync would enroll THAT OS account into this
        user's tenant group. Enrollment (and the membership reconcile) must
        trust the mapping only."""
        calls = self._run_sync(
            monkeypatch,
            tmp_path,
            user_rows=[(None, 1)],
            project_rows=[],
            existing_groups={},
            system_groups={"openace-shared-1": ["erin"]},
        )
        assert not any(
            c[:3] == ("usermod", "-aG", "openace-shared-1") for c in calls
        ), "an unmapped username must never be enrolled"
        # the stale membership of that OS account is REMOVED: desired is
        # empty (no mapped users in tenant 1), so gpasswd -d must run
        assert ("gpasswd", "-d", "erin", "openace-shared-1") in calls

    def test_reconciles_legacy_shared_dir_onto_tenant_group(self, monkeypatch, tmp_path):
        legacy = str(tmp_path / "shared" / "team-proj")
        calls = self._run_sync(
            monkeypatch,
            tmp_path,
            user_rows=[],
            project_rows=[(legacy, 1)],
            existing_groups={"shared/team-proj": "openace-shared"},  # pre-#3396 layout
        )
        assert ("chgrp", "-R", "openace-shared-1", legacy) in calls
        # PR #3402 review: the chmod passes are BATCHED (`{} +`, one chmod
        # per find batch — not one fork per entry). The pinned argv is the
        # deliberate contract: `{} ;` on a node_modules-scale tree kept the
        # pre-service-start reconcile running for tens of minutes.
        assert ("find", legacy, "-type", "d", "-exec", "chmod", "2770", "{}", "+") in calls
        assert ("find", legacy, "-type", "f", "-exec", "chmod", "660", "{}", "+") in calls

    def test_reconcile_skipped_when_already_normalized(self, monkeypatch, tmp_path):
        clean = str(tmp_path / "shared" / "team-proj")
        calls = self._run_sync(
            monkeypatch,
            tmp_path,
            user_rows=[],
            project_rows=[(clean, 1)],
            existing_groups={"shared/team-proj": ("openace-shared-1", "2770")},
        )
        assert not any(c[:1] == ("chgrp",) for c in calls), "steady-state boot must not re-chgrp"
        assert not any(
            c[:1] == ("find",) and "2770" in c for c in calls
        ), "steady-state boot must not re-chmod"

    def test_reconcile_reruns_when_mode_wrong_despite_correct_group(self, monkeypatch, tmp_path):
        """Review on #3396: the fast path used to check the group alone, so a
        dir chgrp'd correctly but left on a wrong mode (e.g. legacy 2775)
        was skipped forever and never normalized to 2770."""
        drifted = str(tmp_path / "shared" / "team-proj")
        calls = self._run_sync(
            monkeypatch,
            tmp_path,
            user_rows=[],
            project_rows=[(drifted, 1)],
            existing_groups={"shared/team-proj": ("openace-shared-1", "2775")},
        )
        assert (
            "find",
            drifted,
            "-type",
            "d",
            "-exec",
            "chmod",
            "2770",
            "{}",
            "+",
        ) in calls, "wrong mode must be normalized even when the group already matches"
        assert ("find", drifted, "-type", "f", "-exec", "chmod", "660", "{}", "+") in calls

    def test_shared_rows_only_and_base_prefixed_paths_touched(self, monkeypatch, tmp_path):
        sql = self._run_sync(
            monkeypatch,
            tmp_path,
            user_rows=[],
            project_rows=[],
            existing_groups={},
        )[0]
        projects_sql = sql[2]
        assert "is_shared = true" in projects_sql, "private projects must never be reconciled"
        assert "is_active = true" in projects_sql
        # outside-base rows are skipped behaviorally below
        outside = "/elsewhere/proj"
        calls = self._run_sync(
            monkeypatch,
            tmp_path,
            user_rows=[],
            project_rows=[(outside, 1)],
            existing_groups={"shared/team-proj": "openace-shared"},
        )
        assert not any("stat" in c[:1] for c in calls), "outside-base paths must not be probed"

    def test_enrollment_sql_excludes_soft_deleted_users(self, monkeypatch, tmp_path):
        """Round-2 review N1: user_repo.delete_user soft-deletes by setting
        deleted_at ONLY (is_active stays true), so the enrollment query must
        filter deleted_at IS NULL — the same classification the user-sync
        above uses. Without it every restart re-enrolls deleted users into
        openace-shared-<t>, silently undoing the delete-path group drop
        app/routes/admin.py performs. The harness queues rows by SQL fragment
        (it cannot evaluate SQL), so the predicate is pinned verbatim like
        the reclaim SQL below."""
        sql = self._run_sync(
            monkeypatch,
            tmp_path,
            user_rows=[],
            project_rows=[],
            existing_groups={},
        )[0]
        assert sql[1] == (
            "SELECT system_account, tenant_id FROM users "
            "WHERE is_active = true AND deleted_at IS NULL"
        ), "soft-deleted users must never be re-enrolled at boot"

    def test_reclaim_pass_reclaims_not_shared_dir_with_tenant_group(self, monkeypatch, tmp_path):
        """Review on #3396 (finding 4): revocation runs fail-soft AFTER the DB
        flip, so a timeout/crash leaves the dir 2770 on openace-shared-<t>
        with is_shared=false forever. The boot sync must re-run the reclaim:
        chown -R to the creator + dirs 0700 / files 0600. PR #3402 review:
        the reclaim now keys on the ROW's tenant — the dir must be on this
        tenant's own group to be provably its leftover."""
        revoked = str(tmp_path / "shared" / "team-proj")
        calls = self._run_sync(
            monkeypatch,
            tmp_path,
            user_rows=[],
            project_rows=[],
            existing_groups={"shared/team-proj": "openace-shared-1"},
            reclaim_rows=[(revoked, 1, "alice-acct")],
        )
        assert ("chown", "-R", "1042:1042", revoked) in calls
        assert ("find", revoked, "-type", "d", "-exec", "chmod", "0700", "{}", "+") in calls
        assert ("find", revoked, "-type", "f", "-exec", "chmod", "0600", "{}", "+") in calls

    def test_reclaim_pass_uses_legacy_global_group_owner_too(self, monkeypatch, tmp_path):
        """A pre-#3396 dir still on the legacy global group with a revoked row
        must also be reclaimed — but ONLY at the exact first-level
        <base>/shared/<name> shape those deployments laid out (PR #3402
        review: anything else on the global group cannot be distinguished
        from namespace plumbing)."""
        revoked = str(tmp_path / "shared" / "legacy-proj")
        calls = self._run_sync(
            monkeypatch,
            tmp_path,
            user_rows=[],
            project_rows=[],
            existing_groups={"shared/legacy-proj": "openace-shared"},
            reclaim_rows=[(revoked, 1, "bob-acct")],
        )
        assert ("chown", "-R", "1042:1042", revoked) in calls

    def test_reclaim_pass_never_touches_active_shared_dirs(self, monkeypatch, tmp_path):
        """The reclaim query must select only NOT(active AND shared) rows; an
        active shared project's dir is normalized by the reconcile pass with
        2770/660 and must NEVER receive a 0700/0600 reclaim."""
        active = str(tmp_path / "shared" / "live-proj")
        calls = self._run_sync(
            monkeypatch,
            tmp_path,
            user_rows=[],
            project_rows=[(active, 1)],
            existing_groups={"shared/live-proj": ("openace-shared-1", "2770")},
            reclaim_rows=[],
        )
        assert not any(c[:1] == ("chown",) for c in calls), "active shared dirs must not be chowned"
        assert not any(
            "0700" in c or "0600" in c for c in calls if c[:1] == ("find",)
        ), "active shared dirs must keep 2770/660, never be reclaimed to 0700/0600"

    def test_reclaim_pass_skips_dirs_not_owned_by_shared_groups(self, monkeypatch, tmp_path):
        """A not-shared row whose dir is on some unrelated group was either
        already reclaimed by the app-side revoke or never group-shared —
        the boot pass must not chown it."""
        private = str(tmp_path / "shared" / "already-private")
        calls = self._run_sync(
            monkeypatch,
            tmp_path,
            user_rows=[],
            project_rows=[],
            existing_groups={"shared/already-private": "alice-acct"},
            reclaim_rows=[(private, 1, "alice-acct")],
        )
        assert not any(c[:1] == ("chown",) for c in calls)

    def test_reclaim_pass_reclaims_soft_deleted_shared_row(self, monkeypatch, tmp_path):
        """Soft delete of a shared project (is_active=false; projects has no
        deleted_at column) never reclaims at request time — the boot pass
        must do it."""
        deleted = str(tmp_path / "shared" / "gone-proj")
        result = self._run_sync(
            monkeypatch,
            tmp_path,
            user_rows=[],
            project_rows=[],
            existing_groups={"shared/gone-proj": "openace-shared-2"},
            reclaim_rows=[(deleted, 2, "carol-acct")],
        )
        reclaim_sql = " ".join(result[0][3].split())
        assert reclaim_sql == (
            "SELECT p.path, p.tenant_id, u.system_account FROM projects p "
            "LEFT JOIN users u ON p.created_by = u.id "
            "WHERE NOT (p.is_active = true AND p.is_shared = true)"
        ), (
            "the reclaim query is pinned verbatim: the harness routes rows by SQL "
            "text and cannot execute SQL, so any predicate drift (e.g. an extra "
            "AND false, or dropping the NOT) must fail HERE rather than silently "
            "select the wrong rows at boot"
        )
        assert ("chown", "-R", "1042:1042", deleted) in result
        assert ("find", deleted, "-type", "d", "-exec", "chmod", "0700", "{}", "+") in result

    def test_reclaim_pass_refuses_cross_tenant_takeover_of_live_shared(self, monkeypatch, tmp_path):
        """PR #3402 review (takeover, probe 2): private registration has NO
        path-ownership validation (api_create_project validates only
        ``if is_shared:``) and get_project_by_path is tenant-scoped, so
        tenant-2's carol can register tenant-1's LIVE shared project path
        as a private project. The old predicate (any openace-shared* group)
        would chown -R the dir to carol at the next boot, right after the
        reconcile pass normalized it. The tightened predicate must refuse:
        the path overlaps a live shared project."""
        live = str(tmp_path / "shared" / "team-proj")
        calls = self._run_sync(
            monkeypatch,
            tmp_path,
            user_rows=[],
            project_rows=[(live, 1)],
            existing_groups={"shared/team-proj": ("openace-shared-1", "2770")},
            reclaim_rows=[(live, 2, "carol-acct")],
        )
        assert not any(c[:1] == ("chown",) for c in calls), "takeover row must not be chowned"
        assert not any(
            c[:1] == ("find",) and ("0700" in c or "0600" in c) for c in calls
        ), "the live shared dir must keep 2770/660, never be reclaimed"

    def test_reclaim_never_follows_symlinks(self, monkeypatch, tmp_path):
        """Round-5 N1: every reclaim check is string-based and isdir/stat/
        chown -R follow symlinks, so a same-tenant private row aliased via
        ``ln -s <base>/shared/<live-project> <own-innocent-path>`` passes
        them all — and GNU chown dereferences its command-line operand,
        handing the LIVE project's root to the attacker (who can then chmod
        0700 and rename/delete it despite the sticky namespace root). A
        symlink can never be this row's own leftover: registrations realpath
        at creation."""
        victim = str(tmp_path / "shared" / "live-proj")
        alias = str(tmp_path / "mallory" / "innocent")
        calls = self._run_sync(
            monkeypatch,
            tmp_path,
            user_rows=[],
            project_rows=[(victim, 1)],  # the target is a LIVE shared project
            existing_groups={
                "shared/live-proj": ("openace-shared-1", "2770"),
                "mallory/innocent": "openace-shared-1",  # stat SUCCEEDS on the link
            },
            links={"mallory/innocent": victim},
            reclaim_rows=[(alias, 1, "mallory-acct")],
        )
        assert not any(
            c[:1] == ("chown",) and alias in c for c in calls
        ), "a symlinked row must never be chown -R'd (chown dereferences the operand)"
        assert not any(
            c[:1] == ("chown",) and victim in c for c in calls
        ), "the live project must not be reached through the alias"

    def test_reconcile_refuses_symlinked_active_shared_row(self, monkeypatch, tmp_path):
        """Round-5 N1 (reconcile side): an active-shared row whose path is a
        symlink is tampering/anomaly, not a project dir — chgrp/chmod through
        it would hit the link TARGET. Refused loudly (failure counter), the
        boot still degrades to a WARNING."""
        real = str(tmp_path / "real-target")
        aliased = str(tmp_path / "aliased")
        calls = self._run_sync(
            monkeypatch,
            tmp_path,
            user_rows=[],
            project_rows=[(aliased, 1)],
            existing_groups={
                "real-target": ("openace-shared-1", "2770"),
                "aliased": ("openace-shared-1", "2775"),
            },
            links={"aliased": real},
        )
        assert not any(
            c[:2] == ("chgrp", "-R") and aliased in c for c in calls
        ), "a symlinked shared row must not be reconciled through the link"
        assert not any(
            c[:1] == ("find",) and aliased in c for c in calls
        ), "no chmod pass may run through the aliased path"
        assert ("__EXIT__", 1) in calls, "the refusal must be a LOUD failure, not a silent skip"

    def test_converge_ignores_non_numeric_lookalike_groups(self, monkeypatch, tmp_path):
        """Round-5 N2: tenant group suffixes are numeric by construction.
        An operator-created lookalike (openace-shared-backup) matching the
        bare name prefix must never be converged — its desired set is empty,
        so the empty-set branch would strip its members one by one."""
        calls = self._run_sync(
            monkeypatch,
            tmp_path,
            user_rows=[("alice-acct", 1)],
            project_rows=[],
            existing_groups={},
            system_groups={
                "openace-shared-1": ["alice-acct"],
                "openace-shared-backup": ["ops-acct"],
            },
        )
        assert ("gpasswd", "-M", "alice-acct", "openace-shared-1") in calls
        assert not any(
            c[3] == "openace-shared-backup"
            for c in calls
            if c[:2] in (("gpasswd", "-M"), ("gpasswd", "-d"))
        ), "lookalike operator groups must never be converged"

    def test_converge_filters_members_missing_os_accounts(self, monkeypatch, tmp_path):
        """Round-5 N4: shadow-utils rejects a whole ``gpasswd -M`` call whose
        list contains an account missing from passwd, which silently kept the
        group's STALE list every boot. The -M list converges only the
        accounts that exist this boot; the ghost's absence is already loud in
        the user-sync log."""
        calls = self._run_sync(
            monkeypatch,
            tmp_path,
            user_rows=[("alice-acct", 1), ("ghost-acct", 1)],
            project_rows=[],
            existing_groups={},
            system_groups={"openace-shared-1": ["alice-acct"]},
            missing_accounts={"ghost-acct"},
        )
        assert ("gpasswd", "-M", "alice-acct", "openace-shared-1") in calls
        assert not any(
            c[:3] == ("gpasswd", "-M", "alice-acct,ghost-acct") for c in calls
        ), "a missing OS account must not poison the whole -M call"
        assert ("getent", "passwd", "ghost-acct") in calls, "the ghost was probed before filtering"

    def test_converge_ignores_unicode_digit_lookalike_groups(self, monkeypatch, tmp_path):
        """str.isdigit() ACCEPTS non-ASCII digits (fullwidth '１２３',
        Arabic-Indic '٤٢', ...), so a root-created
        openace-shared-<unicode-digits> group passed the numeric-suffix
        guard — and the converge loop then emptied it member by member
        (desired has no such tenant). The suffix must be ASCII digits."""
        calls = self._run_sync(
            monkeypatch,
            tmp_path,
            user_rows=[("alice-acct", 1)],
            project_rows=[],
            existing_groups={},
            system_groups={
                "openace-shared-1": ["alice-acct"],
                "openace-shared-１２３": ["ops-acct"],
            },
        )
        assert ("gpasswd", "-M", "alice-acct", "openace-shared-1") in calls
        assert not any(
            c[3] == "openace-shared-１２３"
            for c in calls
            if c[:2] in (("gpasswd", "-M"), ("gpasswd", "-d"))
        ), "a unicode-digit lookalike group must never be converged (isdigit alone accepts it)"

    def test_converge_skips_group_on_persistent_nss_transient_failure(self, monkeypatch, tmp_path):
        """NSS transient hardening: getent passwd failing with rc=1 twice
        (socket timeout / sssd restart — neither 0=present nor
        2=genuinely-absent) must NOT produce a membership list: converging
        one built on an unreliable answer either poisons gpasswd -M with a
        missing account (N4 rejection) or strips a member who is actually
        present. The group's convergence is skipped this boot with a loud
        warning; the current members stay untouched and the next boot
        retries. Not a failure — the degradation is deliberate."""
        calls = self._run_sync(
            monkeypatch,
            tmp_path,
            user_rows=[("alice-acct", 1), ("bob-acct", 2)],
            project_rows=[],
            existing_groups={},
            system_groups={
                "openace-shared-1": ["alice-acct"],
                "openace-shared-2": ["bob-acct"],
            },
            ambiguous_accounts={"alice-acct"},
        )
        # tenant 2 (all answers clean) still converges
        assert ("gpasswd", "-M", "bob-acct", "openace-shared-2") in calls
        # tenant 1 is skipped in BOTH forms — no -M and no -d
        assert not any(
            c[3] == "openace-shared-1"
            for c in calls
            if c[:2] in (("gpasswd", "-M"), ("gpasswd", "-d"))
        ), "an ambiguous presence answer must not drive any membership write"
        stdout = next(c[1] for c in calls if c[0] == "__STDOUT__")
        assert "still ambiguous after retry" in stdout
        assert "openace-shared-1" in stdout
        assert ("__EXIT__", 1) not in calls, "deliberate one-boot degradation, not a failure"

    def test_converge_nss_retry_recovers_and_keeps_member(self, monkeypatch, tmp_path):
        """One transient rc=1 followed by a clean rc=0 (retry succeeds) is
        NOT ambiguous — the member is present and must stay in the -M list."""
        calls = self._run_sync(
            monkeypatch,
            tmp_path,
            user_rows=[("alice-acct", 1)],
            project_rows=[],
            existing_groups={},
            system_groups={"openace-shared-1": ["alice-acct"]},
            getent_rc_sequences={"alice-acct": [1, 0]},
        )
        assert calls.count(("getent", "passwd", "alice-acct")) == 2, "exactly one retry"
        assert ("gpasswd", "-M", "alice-acct", "openace-shared-1") in calls

    def test_reclaim_pass_refuses_foreign_tenant_group_dir(self, monkeypatch, tmp_path):
        """PR #3402 review (takeover, general form): a private row whose dir
        sits on ANOTHER tenant's group (registered cross-tenant, no live
        row involved) must not be reclaimed — only the row's OWN tenant
        group proves this tenant ever shared the dir. The legitimate row of
        the OWNING tenant still is."""
        victim = str(tmp_path / "shared" / "t1-proj")
        legit = str(tmp_path / "shared" / "t1-old")
        calls = self._run_sync(
            monkeypatch,
            tmp_path,
            user_rows=[],
            project_rows=[],
            existing_groups={
                "shared/t1-proj": "openace-shared-1",
                "shared/t1-old": "openace-shared-1",
            },
            reclaim_rows=[(victim, 2, "carol-acct"), (legit, 1, "alice-acct")],
        )
        assert ("chown", "-R", "1042:1042", victim) not in calls
        assert ("chown", "-R", "1042:1042", legit) in calls

    def test_reclaim_pass_never_reclaims_namespace_root(self, monkeypatch, tmp_path):
        """PR #3402 review (takeover, probe 1): <base>/shared itself is
        root:openace-shared 3770 — a private row pointing at the namespace
        root used to chown -R the WHOLE namespace (every tenant's shared
        projects) to the registering user."""
        root = str(tmp_path / "shared")
        calls = self._run_sync(
            monkeypatch,
            tmp_path,
            user_rows=[],
            project_rows=[],
            existing_groups={"shared": "openace-shared"},
            reclaim_rows=[(root, 2, "carol-acct")],
        )
        assert not any(c[:1] == ("chown",) for c in calls)

    def test_reclaim_pass_refuses_subdir_of_live_shared_project(self, monkeypatch, tmp_path):
        """PR #3402 review (takeover, probe 3): registering a SUBDIRECTORY of
        a live shared project as a private project (it inherits the tenant
        group via setgid) must not hand the subtree — including other
        members' files — to the registrant. The overlap check contains it
        even though the group matches the row's tenant."""
        live = str(tmp_path / "shared" / "team-proj")
        sub = str(tmp_path / "shared" / "team-proj" / "sub")
        calls = self._run_sync(
            monkeypatch,
            tmp_path,
            user_rows=[],
            project_rows=[(live, 1)],
            existing_groups={"shared/team-proj/sub": "openace-shared-1"},
            reclaim_rows=[(sub, 1, "mallory-acct")],
        )
        assert not any(c[:1] == ("chown",) for c in calls)

    def test_reclaim_pass_skips_unshaped_legacy_global_group_dir(self, monkeypatch, tmp_path):
        """PR #3402 review: a legacy dir on the GLOBAL group that is not a
        first-level <base>/shared/<name> cannot be distinguished from
        namespace plumbing — declared residual, never auto-reclaimed."""
        deep = str(tmp_path / "shared" / "legacy" / "proj")
        home_like = str(tmp_path / "alice-acct" / "old-share")
        calls = self._run_sync(
            monkeypatch,
            tmp_path,
            user_rows=[],
            project_rows=[],
            existing_groups={
                "shared/legacy/proj": "openace-shared",
                "alice-acct/old-share": "openace-shared",
            },
            reclaim_rows=[(deep, 1, "alice-acct"), (home_like, 1, "alice-acct")],
        )
        assert not any(c[:1] == ("chown",) for c in calls)

    def test_membership_reconcile_converges_tenant_groups_onto_db(self, monkeypatch, tmp_path):
        """PR #3402 review: the sync used to only ADD (usermod -aG).
        Removals ran solely in the request-handling container, so after a
        tenant move the scheduler container's /etc/group kept BOTH tenant
        groups — and its autonomous agents (openace-run-as resolves
        supplementary groups against the local /etc/group) held the old
        tenant's content access until the container was RECREATED (restart
        does not reset /etc/group). The boot sync is now authoritative:
        gpasswd -M sets each existing openace-shared-<t> to the DB's exact
        member list; the GLOBAL group is deliberately never converged
        (deactivated accounts keep namespace-root creation — accepted
        residual, no content access)."""
        calls = self._run_sync(
            monkeypatch,
            tmp_path,
            user_rows=[("alice-acct", 1), ("bob-acct", 2)],  # bob moved 1 -> 2
            project_rows=[],
            existing_groups={},
            system_groups={
                "openace-shared": ["alice-acct", "bob-acct"],
                "openace-shared-1": ["alice-acct", "bob-acct"],  # bob stale
                "openace-shared-2": ["bob-acct"],
            },
        )
        assert ("gpasswd", "-M", "alice-acct", "openace-shared-1") in calls
        assert ("gpasswd", "-M", "bob-acct", "openace-shared-2") in calls
        # the global group is untouched in BOTH forms
        assert not any(c[:2] == ("gpasswd", "-M") and c[3] == "openace-shared" for c in calls)
        assert not any(c[:2] == ("gpasswd", "-d") and c[3] == "openace-shared" for c in calls)

    def test_membership_reconcile_removes_stale_members_when_desired_empty(
        self, monkeypatch, tmp_path
    ):
        """Deactivated/deleted/moved-away users are not in the active DB set,
        so their tenant group's desired list no longer names them. With
        remaining members, gpasswd -M drops them in one call; with NO
        remaining members, shadow-utils `gpasswd -M ""` is a documented
        no-op, so each current member is removed with gpasswd -d."""
        calls = self._run_sync(
            monkeypatch,
            tmp_path,
            user_rows=[("alice-acct", 1)],
            project_rows=[],
            existing_groups={},
            system_groups={
                "openace-shared-1": ["alice-acct", "erin-acct"],  # erin deactivated
                "openace-shared-2": ["carol-acct"],  # whole tenant moved away
            },
        )
        # erin dropped via the exact-list -M; carol's group emptied via -d
        assert ("gpasswd", "-M", "alice-acct", "openace-shared-1") in calls
        assert ("gpasswd", "-d", "carol-acct", "openace-shared-2") in calls
        assert not any(
            c[:4] == ("gpasswd", "-M", "", "openace-shared-2") for c in calls
        ), "gpasswd -M '' is a no-op in shadow-utils and must not be relied on"

    def test_block_runs_after_main_user_sync(self):
        """Enrollment must follow the main DB user sync (the accounts it
        usermods are created there); textual ordering by necessity."""
        content = ENTRYPOINT.read_text(encoding="utf-8")
        sync_end = content.index("open-ace-user-sync.log")
        # index() raises if the heredoc tag is absent after sync_end; the
        # explicit assert keeps the failure semantics visible (and satisfies
        # the false-positive scanner's no_assertion gate).
        assert content.index(f"<<'{self.HEREDOC_TAG}'", sync_end) > sync_end

    def test_sync_pipeline_hardened_like_user_sync(self):
        """Review on #3396 (finding 1): the old plain `python3 - <<EOF ...
        | tee LOG || echo WARNING` pipeline could never fire the WARNING —
        tee's rc=0 masked python's death (no pipefail) and the heredoc's
        outermost except exited 0 on every error. The invocation must match
        the #3390 user-sync hardening: scoped pipefail subshell, -u, and a
        python that exits 1 on failure paths."""
        content = ENTRYPOINT.read_text(encoding="utf-8")
        start = content.index(f"<<'{self.HEREDOC_TAG}'")
        line_start = content.rindex("\n", 0, start) + 1
        line = content[line_start : content.index("\n", start)]
        assert line.lstrip().startswith(
            "( set -o pipefail; python3 -u - <<'"
        ), "the group-sync must run in a pipefail subshell with unbuffered python"
        assert "2>&1 | tee /app/logs/open-ace-shared-groups.log )" in line
        assert '|| echo "WARNING' in line, "a nonzero python exit must surface as the WARNING line"
        code = self._extract_sync_code()
        assert "sys.exit(1)" in code, "the sync python must exit nonzero on failure paths"


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
        monkeypatch.setattr(ws, "_acceptance_mode_flag", modes, raising=False)

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


class TestWorkspaceDirPrivateMode:
    """Issue #3410: <base>/<account> must be 0700, like /home/<account>.

    It is the fs API's home root and the OS layer the capability contract's
    `filesystem` dimension claims; at 0755 any other system account could read
    a user's whole workspace from a terminal or webui session. Shared projects
    live at <base>/shared/<name>, outside every home, so sharing is unaffected.
    """

    @pytest.fixture()
    def stubbed(self, monkeypatch):
        from app.utils import workspace as ws

        monkeypatch.setattr(ws, "run_as_root_if_needed", lambda cmd: _FakeProc(0, ""))
        monkeypatch.setattr(ws, "_is_wrapper_available", lambda w: False)
        monkeypatch.setattr(ws, "_is_docker_multi_user_mode", lambda: False)
        # uid/gid lookup fails -> the chown block is skipped entirely; the mode
        # normalization must still run (it is at function level, not nested).
        monkeypatch.setattr(ws.subprocess, "run", lambda cmd, **kw: _FakeProc(1, ""))
        return ws

    @pytest.mark.security
    @pytest.mark.issue(3410)
    def test_new_workspace_dirs_are_private(self, stubbed, tmp_path):
        base = tmp_path / "workspace"
        base.mkdir()
        stubbed._ensure_workspace_dirs("alice", str(base))
        assert (base / "alice").is_dir()
        assert oct((base / "alice").stat().st_mode & 0o777) == "0o700"
        assert oct((base / "alice" / ".qwen").stat().st_mode & 0o777) == "0o700"

    @pytest.mark.security
    @pytest.mark.issue(3410)
    def test_pre_existing_0755_dirs_converge_on_restart(self, stubbed, tmp_path):
        base = tmp_path / "workspace"
        (base / "alice" / ".qwen").mkdir(parents=True)
        (base / "alice").chmod(0o755)
        (base / "alice" / ".qwen").chmod(0o755)
        stubbed._ensure_workspace_dirs("alice", str(base))
        assert oct((base / "alice").stat().st_mode & 0o777) == "0o700"
        assert oct((base / "alice" / ".qwen").stat().st_mode & 0o777) == "0o700"

    @pytest.mark.security
    @pytest.mark.issue(3410)
    def test_shared_namespace_root_is_never_chmodded(self, stubbed, tmp_path):
        """The 3770 namespace root must not be taken to 0700 by this pass.

        The guard at the top of _ensure_workspace_dirs is gated on
        _is_docker_multi_user_mode(), which went stale when #3393 made the
        package installer provision <base>/shared too — so this normalization
        must carry its own skip rather than depend on that guard firing.
        """
        base = tmp_path / "workspace"
        (base / "shared").mkdir(parents=True)
        (base / "shared").chmod(0o3770)
        stubbed._ensure_workspace_dirs("shared", str(base))
        assert oct((base / "shared").stat().st_mode & 0o7777) == "0o3770"
