"""Issue #2403: the wrapper must reclaim its per-task runtime tree on exit.

``scripts/openace-run-as.sh`` only ever wiped ``$task_base`` on START, so every
agent run left one directory under ``/run/openace-agent-tasks`` forever. In
production 46 of them filled the 3.1G ``/run`` tmpfs and every autonomous
workflow started failing with a misleading "Failed to access project dir"
(really ENOSPC writing the signature registry).

The reclaim logic lives in two self-contained shell functions so it can be
exercised without root, cgroups, ACLs or a real agent. These tests extract each
function from the script and run it for real against a temporary tree — they are
behavioural, not source greps. The wiring that installs them (trap placement,
call sites) cannot be observed that way, so it is pinned separately in
``test_reclaim_wiring.py``.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import textwrap
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "openace-run-as.sh"


def _extract_function(name: str) -> str:
    """Return the shell source of a top-level-in-block function definition."""
    src = SCRIPT.read_text(encoding="utf-8")
    match = re.search(
        rf"^(?P<indent>[ \t]*){re.escape(name)}\(\) \{{\n(?P<body>.*?)^(?P=indent)\}}$",
        src,
        re.MULTILINE | re.DOTALL,
    )
    assert match, f"{name}() not found in {SCRIPT}; did it get renamed or inlined?"
    return textwrap.dedent(match.group(0))


# reclaim_task_tree calls _move_to_preserve (Issue #2442), so any harness that
# runs it must define both. Extract them together once at import time.
_RECLAIM_SRC = (
    _extract_function("_move_to_preserve") + "\n" + _extract_function("reclaim_task_tree")
)


def _run_snippet(snippet: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    full_env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    full_env.update(env or {})
    return subprocess.run(
        ["bash", "-euo", "pipefail", "-c", snippet],
        capture_output=True,
        text=True,
        env=full_env,
        timeout=60,
    )


def _gnu_find() -> bool:
    """The reaper needs GNU findutils (-quit, and -newermt with a relative time)."""
    probe = subprocess.run(
        ["find", ".", "-maxdepth", "0", "-newermt", "-1 days", "-print", "-quit"],
        capture_output=True,
        text=True,
    )
    return probe.returncode == 0


requires_gnu_find = pytest.mark.skipif(
    not _gnu_find(), reason="requires GNU findutils (-quit / relative -newermt); BSD find on macOS"
)
_FLOCK_SHIM = """#!/usr/bin/env python3
# Minimal flock(1) for platforms without util-linux (macOS). Covers the forms
# used here: `flock -n <fd>` (the reaper) and `flock -x <fd>` (a test holding a
# lock), on an already-open inherited descriptor.
import fcntl, sys

mode = fcntl.LOCK_EX
nonblock = 0
fds = []
for arg in sys.argv[1:]:
    if arg == "-n":
        nonblock = fcntl.LOCK_NB
    elif arg == "-x":
        mode = fcntl.LOCK_EX
    elif arg == "-s":
        mode = fcntl.LOCK_SH
    elif arg == "-u":
        mode = fcntl.LOCK_UN
    else:
        fds.append(arg)
try:
    fcntl.flock(int(fds[0]), mode | nonblock)
except (OSError, ValueError, IndexError):
    sys.exit(1)
"""


@pytest.fixture
def flock_path(tmp_path_factory):
    """PATH containing a usable flock(1).

    The lock branch is the only one production ever reaches, so skipping it off
    Linux would leave it unverified on the machine where it is being written.
    """
    existing = shutil.which("flock")
    if existing:
        return os.environ.get("PATH", "/usr/bin:/bin")
    shim_dir = tmp_path_factory.mktemp("flockshim")
    shim = shim_dir / "flock"
    shim.write_text(_FLOCK_SHIM, encoding="utf-8")
    shim.chmod(0o755)
    return f"{shim_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}"


def _make_tree(task_root: Path, task_id: str, *, with_claude: bool = True) -> dict[str, Path]:
    base = task_root / task_id
    home = base / "home"
    (base / "tmp").mkdir(parents=True)
    home.mkdir(parents=True)
    (base / "tmp" / "big").write_text("x" * 1024, encoding="utf-8")
    if with_claude:
        projects = home / ".claude" / "projects" / "encoded"
        projects.mkdir(parents=True)
        (projects / "session.jsonl").write_text('{"m":"hi"}\n', encoding="utf-8")
    return {"base": base, "home": home, "preserve": task_root / f"{task_id}.claude-preserve"}


def _reclaim_harness(task_root: Path, task_id: str) -> str:
    base = task_root / task_id
    return f"""
{_RECLAIM_SRC}
task_base={base!s}
task_home={base!s}/home
preserve_claude_dir={task_root!s}/{task_id}.claude-preserve
reclaim_task_tree
"""


class TestReclaimTaskTree:
    def test_tree_is_gone_after_reclaim(self, tmp_path):
        """The whole point: the per-task tree must not survive the run."""
        paths = _make_tree(tmp_path, "t1")
        result = _run_snippet(_reclaim_harness(tmp_path, "t1"))
        assert result.returncode == 0, result.stderr
        assert not paths["base"].exists()

    def test_claude_history_is_preserved(self, tmp_path):
        """#2035 must not regress: --resume needs the session history to survive."""
        paths = _make_tree(tmp_path, "t2")
        result = _run_snippet(_reclaim_harness(tmp_path, "t2"))
        assert result.returncode == 0, result.stderr
        survivor = paths["preserve"] / "projects" / "encoded" / "session.jsonl"
        assert survivor.is_file()
        assert survivor.read_text(encoding="utf-8") == '{"m":"hi"}\n'

    def test_second_execution_does_not_destroy_saved_history(self, tmp_path):
        """A signal between the success-path call and `trap -` runs it twice.

        If the `rm -rf $preserve_claude_dir` ever escapes the `-d` guard, the
        second pass deletes exactly the history the first pass just rescued.
        """
        paths = _make_tree(tmp_path, "t3")
        snippet = _reclaim_harness(tmp_path, "t3") + "\nreclaim_task_tree\n"
        result = _run_snippet(snippet)
        assert result.returncode == 0, result.stderr
        assert (paths["preserve"] / "projects" / "encoded" / "session.jsonl").is_file()

    def test_empty_task_base_touches_nothing(self, tmp_path):
        """On the legacy uid-* path task_base is "" — it must be inert, not `rm -rf ""`."""
        sentinel = tmp_path / "sentinel"
        sentinel.mkdir()
        (sentinel / "keep").write_text("keep", encoding="utf-8")
        snippet = f"""
{_RECLAIM_SRC}
task_base=""
task_home=""
preserve_claude_dir=""
reclaim_task_tree
echo "rc=$?"
"""
        result = _run_snippet(snippet)
        assert result.returncode == 0, result.stderr
        assert "rc=0" in result.stdout
        assert (sentinel / "keep").is_file()

    def test_blank_preserve_path_aborts_instead_of_dropping_history(self, tmp_path):
        """A real tree plus a blank preserve path must be a no-op, not data loss.

        Without the second guard the rescue `mv` silently fails (empty
        destination) and the following `rm -rf $task_base` then takes the
        session history with it — the tree is reclaimed but #2035 --resume is
        permanently broken for that task.
        """
        paths = _make_tree(tmp_path, "t5")
        snippet = f"""
{_RECLAIM_SRC}
task_base={paths["base"]!s}
task_home={paths["base"]!s}/home
preserve_claude_dir=""
reclaim_task_tree
echo "rc=$?"
"""
        result = _run_snippet(snippet)
        assert result.returncode == 0, result.stderr
        assert "rc=0" in result.stdout
        assert (
            paths["home"] / ".claude" / "projects" / "encoded" / "session.jsonl"
        ).is_file(), "history was destroyed with no preserve path to rescue it into"

    def test_failed_reclaim_is_recorded(self, tmp_path):
        """Silence is why this leak went unnoticed for so long.

        A partial `rm -rf` on a full tmpfs is exactly the condition this issue
        is about; if it is swallowed, the next investigation starts from zero
        again. Provoked here by making the parent unwritable so the unlink
        cannot succeed.
        """
        victim = tmp_path / "victim"
        victim.mkdir()
        paths = _make_tree(victim, "t6")
        audit = tmp_path / "audit.log"  # outside the frozen directory
        victim.chmod(0o500)  # read+execute only: children cannot be unlinked
        try:
            snippet = f"""
log_audit() {{ printf '%s\\n' "$1" >> {audit!s}; }}
{_RECLAIM_SRC}
task_base={paths["base"]!s}
task_home={paths["base"]!s}/home
preserve_claude_dir={victim!s}/t6.claude-preserve
task_id=t6
reclaim_task_tree
echo "rc=$?"
"""
            result = _run_snippet(snippet)
        finally:
            victim.chmod(0o700)
        assert result.returncode == 0, result.stderr
        if paths["base"].exists():
            assert audit.is_file() and "reclaim_failed" in audit.read_text(
                encoding="utf-8"
            ), "reclamation failed and nothing was recorded"

    def test_no_claude_dir_does_not_create_empty_preserve(self, tmp_path):
        paths = _make_tree(tmp_path, "t4", with_claude=False)
        result = _run_snippet(_reclaim_harness(tmp_path, "t4"))
        assert result.returncode == 0, result.stderr
        assert not paths["base"].exists()
        assert not paths["preserve"].exists()


def _reap_harness(task_root: Path, lock_dir: Path, days: object = 30) -> str:
    return f"""
{_extract_function("_task_lock_shard")}
_TASK_LOCK_SHARDS=64
{_extract_function("reap_stale_preserve_dirs")}
task_root={task_root!s}
preserve_max_age_days={days}
_lock_dir={lock_dir!s}
reap_stale_preserve_dirs
"""


def _shard_lock_name(task_id: str) -> str:
    """The shard lock file NAME the reaper probes for task_id (#2437).

    Runs the script's own ``_task_lock_shard`` (cksum-based) so the test always
    agrees with the reaper, whatever the exact hash function. task_id is passed
    as $1 to avoid any shell-quoting pitfall.
    """
    snippet = (
        _extract_function("_task_lock_shard") + "\n_TASK_LOCK_SHARDS=64\n" + '_task_lock_shard "$1"'
    )
    result = subprocess.run(["bash", "-c", snippet, "_", task_id], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return f"openace-agent-task-shard-{result.stdout.strip()}.lock"


def _age(path: Path, days: int) -> None:
    """Backdate a path's mtime.

    Uses os.utime rather than `touch -d`: the relative-date form is GNU-only and
    this machine may well have GNU find but BSD touch.

    Callers must age children before parents — writing into a directory bumps
    that directory's own mtime.
    """
    stamp = time.time() - days * 86400
    os.utime(path, (stamp, stamp))


@requires_gnu_find
class TestReapStalePreserveDirs:
    def test_stale_preserve_is_reaped(self, tmp_path):
        task_root = tmp_path / "tasks"
        lock_dir = tmp_path / "lock"
        task_root.mkdir()
        lock_dir.mkdir()
        stale = task_root / "old.claude-preserve"
        (stale / "projects").mkdir(parents=True)
        (stale / "projects" / "s.jsonl").write_text("{}", encoding="utf-8")
        for target in (stale / "projects" / "s.jsonl", stale / "projects", stale):
            _age(target, 90)

        result = _run_snippet(_reap_harness(task_root, lock_dir))
        assert result.returncode == 0, result.stderr
        assert not stale.exists()

    def test_fresh_preserve_survives(self, tmp_path):
        task_root = tmp_path / "tasks"
        lock_dir = tmp_path / "lock"
        task_root.mkdir()
        lock_dir.mkdir()
        fresh = task_root / "new.claude-preserve"
        fresh.mkdir()
        (fresh / "s.jsonl").write_text("{}", encoding="utf-8")

        result = _run_snippet(_reap_harness(task_root, lock_dir))
        assert result.returncode == 0, result.stderr
        assert fresh.exists()

    def test_task_directories_are_never_touched(self, tmp_path):
        """Scope is *.claude-preserve only; reaping task dirs needs a liveness
        test this script cannot make (the Python launch path creates them
        without ever taking a lock)."""
        task_root = tmp_path / "tasks"
        lock_dir = tmp_path / "lock"
        task_root.mkdir()
        lock_dir.mkdir()
        task_dir = task_root / "some-task-id"
        (task_dir / "home").mkdir(parents=True)
        # Age the child FIRST, per _age's contract. Leaving a fresh child makes
        # the age gate short-circuit, so the glob scope is never exercised and
        # widening it to "$task_root"/* — which would `rm -rf` live task trees
        # as root — would go undetected.
        _age(task_dir / "home", 400)
        _age(task_dir, 400)

        result = _run_snippet(_reap_harness(task_root, lock_dir))
        assert result.returncode == 0, result.stderr
        assert (task_dir / "home").is_dir()

    def test_old_directory_with_fresh_history_survives(self, tmp_path):
        """The regression that a dir-mtime implementation would fail.

        Claude CLI writes to .claude/projects/<encoded>/*.jsonl, so the top
        directory's mtime does not move as a session grows, and rename(2) does
        not touch the renamed inode's mtime either. Keying the age check on the
        directory would delete the most active long-lived sessions first.
        """
        task_root = tmp_path / "tasks"
        lock_dir = tmp_path / "lock"
        task_root.mkdir()
        lock_dir.mkdir()
        active = task_root / "busy.claude-preserve"
        projects = active / "projects" / "encoded"
        projects.mkdir(parents=True)
        (projects / "live.jsonl").write_text('{"fresh":true}', encoding="utf-8")
        # Everything except the leaf file looks ancient.
        _age(projects, 90)
        _age(active / "projects", 90)
        _age(active, 90)

        result = _run_snippet(_reap_harness(task_root, lock_dir))
        assert result.returncode == 0, result.stderr
        assert (projects / "live.jsonl").is_file(), (
            "a stale-looking directory holding a fresh session was reaped — the age "
            "test is reading the directory inode instead of the tree"
        )

    def test_empty_preserve_directory_is_reaped(self, tmp_path):
        task_root = tmp_path / "tasks"
        lock_dir = tmp_path / "lock"
        task_root.mkdir()
        lock_dir.mkdir()
        empty = task_root / "empty.claude-preserve"
        empty.mkdir()
        _age(empty, 90)

        result = _run_snippet(_reap_harness(task_root, lock_dir))
        assert result.returncode == 0, result.stderr
        assert not empty.exists()

    def test_missing_lock_file_does_not_manufacture_one(self, tmp_path):
        """The peer lock must be opened read-only: `8>` would create it.

        Lock files cannot be reclaimed safely — unlink followed by a same-name
        open yields a different inode, so two holders stop excluding each other.
        A sweep that creates one per candidate would trade this leak for another.
        """
        task_root = tmp_path / "tasks"
        lock_dir = tmp_path / "lock"
        task_root.mkdir()
        lock_dir.mkdir()
        stale = task_root / "nolock.claude-preserve"
        stale.mkdir()
        _age(stale, 90)

        result = _run_snippet(_reap_harness(task_root, lock_dir))
        assert result.returncode == 0, result.stderr
        assert not stale.exists()
        assert (
            list(lock_dir.iterdir()) == []
        ), f"the sweep created lock files: {[p.name for p in lock_dir.iterdir()]}"

    def test_unheld_lock_still_reaps(self, tmp_path, flock_path):
        """The only branch production ever takes.

        The task lock is created on every run and unlinked nowhere, so for any
        preserve dir that can exist its lock file exists too — same run, same
        boot, and /run and /run/lock are cleared together. That makes the
        `[ ! -e "$_plock" ]` short-circuit unreachable in production, and both
        "is reaped" tests above go down it. Without this case, neutering the
        flock branch entirely would leave the whole suite green while the leak
        F1c exists to close stayed wide open.
        """
        task_root = tmp_path / "tasks"
        lock_dir = tmp_path / "lock"
        task_root.mkdir()
        lock_dir.mkdir()
        stale = task_root / "done.claude-preserve"
        stale.mkdir()
        (stale / "s.jsonl").write_text("{}", encoding="utf-8")
        _age(stale / "s.jsonl", 90)
        _age(stale, 90)
        # Present but unheld: the task finished and released its shard lock.
        (lock_dir / _shard_lock_name("done")).touch()

        result = _run_snippet(_reap_harness(task_root, lock_dir), env={"PATH": flock_path})
        assert result.returncode == 0, result.stderr
        assert not stale.exists(), (
            "a finished task's preserve dir survived even though its lock was "
            "free — the flock branch is not actually reaping anything"
        )

    def test_held_lock_protects_a_stale_looking_preserve(self, tmp_path, flock_path):
        """Age alone is not a liveness test.

        The startup preserve-move and the restore bracket a window in which the
        dir legitimately exists mid-run, and the restore can fail silently on a
        full tmpfs. A run holding the task lock must keep its history.
        """
        task_root = tmp_path / "tasks"
        lock_dir = tmp_path / "lock"
        task_root.mkdir()
        lock_dir.mkdir()
        busy = task_root / "live.claude-preserve"
        busy.mkdir()
        (busy / "s.jsonl").write_text("{}", encoding="utf-8")
        _age(busy / "s.jsonl", 90)
        _age(busy, 90)
        lock_file = lock_dir / _shard_lock_name("live")
        lock_file.touch()

        # Hold the lock for the duration of the sweep, exactly as a live run would.
        snippet = (
            f"exec 7>{lock_file!s}\nflock -x 7\n"
            + _reap_harness(task_root, lock_dir)
            + "\nexec 7>&-\n"
        )
        result = _run_snippet(snippet, env={"PATH": flock_path})
        assert result.returncode == 0, result.stderr
        assert busy.exists(), "a locked (live) preserve dir was reaped"
        assert (busy / "s.jsonl").is_file()

    def test_find_failure_skips_rather_than_deletes(self, tmp_path):
        """A failing age test must not read as "stale".

        The age gate is a destructive test, so its failure direction matters:
        swallowing find's exit status turns any error into empty output, which
        the emptiness check then reads as "nothing fresh here" and deletes. The
        threshold is injected directly to provoke the failure — the script's own
        guard against bad config values is pinned separately.
        """
        task_root = tmp_path / "tasks"
        lock_dir = tmp_path / "lock"
        task_root.mkdir()
        lock_dir.mkdir()
        precious = task_root / "keepme.claude-preserve"
        precious.mkdir()
        (precious / "s.jsonl").write_text("{}", encoding="utf-8")

        result = _run_snippet(_reap_harness(task_root, lock_dir, days="not-a-number"))
        assert result.returncode == 0, result.stderr
        assert precious.exists(), (
            "the age test errored and the directory was deleted anyway — find's "
            "exit status is being ignored, so any failure means 'stale'"
        )

    def test_no_candidates_is_a_clean_noop(self, tmp_path):
        """The glob stays literal when nothing matches; the loop must not run."""
        task_root = tmp_path / "tasks"
        lock_dir = tmp_path / "lock"
        task_root.mkdir()
        lock_dir.mkdir()

        result = _run_snippet(_reap_harness(task_root, lock_dir))
        assert result.returncode == 0, result.stderr
        assert not (task_root / "*.claude-preserve").exists()


def _script_default_preserve_days() -> int:
    """The default the script itself falls back to, parsed from source.

    _reap_harness always injects a threshold, so the script's own default was
    never exercised — reverting it to 1 passed the whole suite.
    """
    m = re.search(r"^\s*preserve_max_age_days=(\d+)\s*$", SCRIPT.read_text(encoding="utf-8"), re.M)
    assert m, "could not find the preserve_max_age_days default in the script"
    return int(m.group(1))


def _age_minutes(path: Path, minutes: int) -> None:
    stamp = time.time() - minutes * 60
    os.utime(path, (stamp, stamp))


class TestPreservedHistoryIsPrivate:
    """The reclaim must not move ~/.claude into a world-readable location.

    $task_root is created by `mkdir -p` as root (umask 022) so it is 0755, and
    the preserve dir is its child, not a child of the `chmod 700` tree. Before
    #2403 this layout existed only for the sub-millisecond startup window;
    reclaiming on exit makes it the steady state between runs.
    """

    def test_preserved_history_is_not_world_readable(self, tmp_path):
        paths = _make_tree(tmp_path, "priv1")
        (paths["home"] / ".claude").chmod(0o755)
        result = _run_snippet(_reclaim_harness(tmp_path, "priv1"))
        assert result.returncode == 0, result.stderr
        mode = paths["preserve"].stat().st_mode & 0o777
        assert mode == 0o700, (
            f"preserve dir is {mode:o}, not 700: every local account can read the "
            f"agent's shell snapshots, settings, file-history copies of private "
            f"source and encoded project paths out of /run"
        )

    def test_group_and_other_bits_are_cleared_even_from_a_permissive_source(self, tmp_path):
        paths = _make_tree(tmp_path, "priv2")
        (paths["home"] / ".claude").chmod(0o777)
        _run_snippet(_reclaim_harness(tmp_path, "priv2"))
        mode = paths["preserve"].stat().st_mode & 0o777
        assert not mode & 0o077, f"group/other bits still set: {mode:o}"


@requires_gnu_find
class TestAgeThresholdUnits:
    def test_threshold_is_days_not_minutes(self, tmp_path, flock_path):
        """`-newermt "-N days"` vs `"-N minutes"` is invisible to every other test.

        The existing reaper tests use either just-written files (fresh in any
        unit) or 90/400-day-old ones (stale in any unit), so swapping the unit
        passed all of them — while in production it would reap every session
        line idle more than 30 minutes and silently break --resume.
        """
        task_root = tmp_path / "tasks"
        lock_dir = tmp_path / "lock"
        task_root.mkdir()
        lock_dir.mkdir()
        pdir = task_root / "t.claude-preserve"
        pdir.mkdir()
        (pdir / "f").write_text("x", encoding="utf-8")
        _age_minutes(pdir / "f", 45)
        _age_minutes(pdir, 45)

        result = _run_snippet(_reap_harness(task_root, lock_dir, days=1), env={"PATH": flock_path})
        assert result.returncode == 0, result.stderr
        assert pdir.exists(), (
            "a 45-minute-old preserve dir was reaped under a 1-DAY threshold; "
            "the find expression is counting minutes"
        )

    def test_script_default_threshold_keeps_recent_history(self, tmp_path, flock_path):
        """Pin the script's own fallback, which no harness ever exercised."""
        default_days = _script_default_preserve_days()
        task_root = tmp_path / "tasks"
        lock_dir = tmp_path / "lock"
        task_root.mkdir()
        lock_dir.mkdir()
        pdir = task_root / "t.claude-preserve"
        pdir.mkdir()
        (pdir / "f").write_text("x", encoding="utf-8")
        _age(pdir / "f", 20)
        _age(pdir, 20)

        result = _run_snippet(
            _reap_harness(task_root, lock_dir, days=default_days), env={"PATH": flock_path}
        )
        assert result.returncode == 0, result.stderr
        assert pdir.exists(), (
            f"20-day-old history reaped under the script default of {default_days} "
            f"days; lowering that default silently shortens --resume's memory"
        )


class TestTaskLockShard:
    """#2437: the per-task mutex is sharded onto a FIXED lock-file set (N files),
    chosen by a deterministic function of task_id. Exercises the real bash
    _task_lock_shard so the determinism + bound are verified, not assumed."""

    _SNIPPET = _extract_function("_task_lock_shard") + "\n_TASK_LOCK_SHARDS=64\n"

    @staticmethod
    def _shard(task_id: str) -> int:
        result = subprocess.run(
            ["bash", "-c", TestTaskLockShard._SNIPPET + '_task_lock_shard "$1"', "_", task_id],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        return int(result.stdout.strip())

    def test_shard_is_deterministic(self):
        for tid in ("abc", "auto-dev/xyz", "wf-2437", "t", ""):
            assert self._shard(tid) == self._shard(tid), f"non-deterministic shard for {tid!r}"

    def test_shard_set_is_bounded_by_n(self):
        shards = {self._shard(f"task-{i}") for i in range(200)}
        assert all(0 <= s < 64 for s in shards), "a shard fell outside [0, 64)"
        # 200 distinct task_ids map onto AT MOST 64 shards (the fixed set) — this
        # is the bound that keeps /run/lock from accumulating one file per attempt.
        assert len(shards) <= 64
