"""Issue #2403: pin how the reclaim functions are wired into the wrapper.

``test_task_tree_reclaim.py`` extracts each function and runs it for real, which
proves the logic works but says nothing about whether the script ever calls it,
or calls it at a point where it can help. Three real ways to break the fix are
all invisible to that file:

1. Defining ``reclaim_task_tree`` after the first exit that can strand a tree.
   Bash resolves a trap's function name when the trap FIRES, so a late
   definition makes the trap a silent no-op.
2. Reverting the upgraded ``trap on_exit EXIT`` back to ``cleanup_isolated``.
   Bash traps replace rather than stack, so that one edit disarms reclamation
   for the whole run.
3. Folding the reclaim into ``cleanup_isolated``, whose pre-flight call happens
   *after* the tree is built and ``.claude`` restored into it.

These are source assertions by necessity — they are wiring locks, and they do
not count as behavioural coverage. The behavioural gate is the other file.
"""

from __future__ import annotations

import re
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "openace-run-as.sh"
SRC = SCRIPT.read_text(encoding="utf-8")
LINES = SRC.splitlines()


def _line_of(pattern: str, *, start: int = 0) -> int:
    for index in range(start, len(LINES)):
        if re.search(pattern, LINES[index]):
            return index
    raise AssertionError(f"pattern not found in {SCRIPT.name}: {pattern!r}")


def test_reclaim_is_defined_before_any_exit_that_can_strand_a_tree():
    """The cgroup fail-closed `exit 66` fires while a task tree exists.

    It sits well before the other cleanup helpers, so defining reclaim beside
    them would leave that path calling an undefined function. That path is
    dormant in production today (no cgroup key in agent-launcher.conf) but
    becomes a per-attempt path the moment cgroups are enabled as designed.
    """
    definition = _line_of(r"^\s*reclaim_task_tree\(\) \{")
    tree_built = _line_of(r'^\s*mkdir -p "\$task_home"')
    cgroup_exit = _line_of(r"^\s*exit 66", start=tree_built)
    assert definition < cgroup_exit, (
        f"reclaim_task_tree() defined at line {definition + 1} but `exit 66` at "
        f"line {cgroup_exit + 1} can fire earlier with a tree on disk; bash "
        f"resolves the name when the trap fires, so that trap would be a no-op"
    )


def test_trap_is_armed_before_the_preserve_move_window():
    """Arming after the tree is built leaves the preserve-move window bare.

    The window runs from the preserve path being derived through the wipe and
    re-creation of the tree; a signal or early exit anywhere in it must find the
    trap already installed.
    """
    derive = _line_of(r'^\s*preserve_claude_dir="\$\{task_base\}\.claude-preserve"')
    arm = _line_of(r"^\s*trap reclaim_task_tree EXIT", start=derive)
    startup_wipe = _line_of(r'^\s*rm -rf -- "\$task_base"', start=arm)
    assert derive < arm < startup_wipe, (
        f"derive={derive + 1} arm={arm + 1} wipe={startup_wipe + 1}: the trap must "
        f"be armed between deriving the preserve path and wiping the tree"
    )


def test_signal_traps_are_armed_with_the_reclaim_trap():
    """Until HUP/INT/TERM are trapped, a signal kills bash and no EXIT trap runs."""
    arm = _line_of(r"^\s*trap reclaim_task_tree EXIT")
    window = "\n".join(LINES[arm : arm + 6])
    for signal_name in ("HUP", "INT", "TERM"):
        assert signal_name in window, (
            f"{signal_name} is not trapped alongside the reclaim trap; a signal "
            f"in that window would bypass reclamation entirely"
        )


def test_exit_trap_is_upgraded_to_on_exit():
    assert re.search(r"^\s*trap on_exit EXIT", SRC, re.MULTILINE), (
        "the EXIT trap must name on_exit; bash traps replace rather than stack, "
        "so reverting this to cleanup_isolated silently disarms reclamation"
    )


def test_on_exit_reclaims_between_runtime_cleanup_and_acl_revocation():
    """Order is load-bearing: see the 5s SIGTERM-to-SIGKILL budget."""
    body = re.search(r"^\s*on_exit\(\) \{\n(.*?)^\s*\}$", SRC, re.MULTILINE | re.DOTALL)
    assert body, "on_exit() not found"
    text = body.group(1)
    runtime = text.index("_cleanup_task_runtime")
    reclaim = text.index("reclaim_task_tree")
    acls = text.index("_revoke_task_acls")
    assert runtime < reclaim < acls, (
        "reclaim must run after the kill and before the ACL sweep: a missed ACL "
        "revocation is replayed from the registry next run, a stranded tree is "
        "reclaimed by nothing"
    )


def test_on_exit_steps_are_individually_guarded():
    """errexit stays live inside trap handlers."""
    body = re.search(r"^\s*on_exit\(\) \{\n(.*?)^\s*\}$", SRC, re.MULTILINE | re.DOTALL)
    assert body
    for step in ("_cleanup_task_runtime", "reclaim_task_tree", "_revoke_task_acls"):
        assert re.search(rf"{step}\s*\|\| true", body.group(1)), (
            f"{step} is not `|| true`; a non-zero return would skip the "
            f"remaining steps, and the likeliest cause is a full /run"
        )


def test_preflight_cleanup_does_not_reclaim():
    """The pre-flight cleanup runs after the tree is built and .claude restored.

    Reclaiming there would delete the freshly restored session history before
    the agent starts — the reason this is a separate function at all.
    """
    body = re.search(
        r"^\s*cleanup_isolated\(\) \{(.*?)\}$",
        SRC,
        re.MULTILINE | re.DOTALL,
    )
    assert body, "cleanup_isolated() not found"
    assert "reclaim_task_tree" not in body.group(1)


def test_success_path_reclaims_before_disarming_the_trap():
    disarm = _line_of(r"^\s*trap - EXIT HUP INT TERM")
    window = "\n".join(LINES[max(0, disarm - 4) : disarm])
    assert "on_exit" in window, (
        "the success path must reclaim before `trap -` disarms the handler, "
        "otherwise a clean run is the one case that still leaks"
    )


def test_preserve_reaper_runs_after_the_restore():
    """Sweeping earlier would make this run's own preserve dir a candidate."""
    restore = _line_of(r'^\s*mv "\$preserve_claude_dir" "\$task_home/\.claude"')
    call = _line_of(r"^\s*reap_stale_preserve_dirs$")
    assert restore < call


def test_preserve_dir_is_initialised_unconditionally():
    """It is only assigned inside the task_id block, but read from a trap."""
    init = _line_of(r'^\s*preserve_claude_dir=""')
    # Anchor on the task-tree setup block specifically — `[ -n "$task_id" ]`
    # guards several unrelated blocks earlier in the script.
    block = _line_of(r'^\s*task_root="\$\{OPENACE_AGENT_TASK_ROOT')
    assert init < block, (
        "preserve_claude_dir must be initialised before the task_id block; "
        "under `set -u` an unset name kills the wrapper from its own trap"
    )


def test_lock_directory_is_a_constant_not_an_environment_override():
    """Redirecting it would break mutual exclusion between concurrent attempts."""
    assert re.search(r'^\s*_lock_dir="/run/lock"', SRC, re.MULTILINE)
    assert not re.search(
        r"_lock_dir=.*\$\{?OPENACE", SRC
    ), "the lock directory must not be environment-controlled"


def test_the_disproven_reconciler_comment_is_gone():
    """That comment is why the leak went unnoticed: it described a mechanism
    nobody ever implemented."""
    assert "reconciled by the scheduler on restart" not in SRC
