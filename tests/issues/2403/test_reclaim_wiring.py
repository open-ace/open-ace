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
    arm = _line_of(r"^\s*trap reclaim_task_tree EXIT")
    assert definition < arm, (
        f"reclaim_task_tree() is defined at line {definition + 1} but the trap "
        f"naming it is armed at line {arm + 1}; everything in between — "
        f"including the mkdir/chmod that fail under `set -e` on a full tmpfs, "
        f"the exact incident condition — would fire an undefined function"
    )
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
    window = "\n".join(LINES[arm : arm + 8])
    for signal_name in ("HUP", "INT", "TERM"):
        assert re.search(rf"^\s*trap 'exit \d+' {signal_name}$", window, re.MULTILINE), (
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
    """Anchor on a bare CALL, never a substring of the surrounding window.

    A window search is satisfied by the explanatory comment sitting right above
    the call, so deleting the call outright — restoring the leak on the most
    common path in production, and stopping ACL revocation too, since the very
    next line disarms the handler — would still pass.
    """
    disarm = _line_of(r"^\s*trap - EXIT HUP INT TERM")
    call = _line_of(r"^\s*on_exit$")
    assert call < disarm, (
        f"on_exit call at line {call + 1} must precede the disarm at "
        f"{disarm + 1}; otherwise a clean run is the one case that still leaks"
    )


def test_preflight_call_site_stays_the_narrow_cleanup():
    """The pre-flight site must call cleanup_isolated, never on_exit.

    It runs after the tree is built and .claude has been restored into it, so
    calling on_exit there would `rm -rf` the tree — and the session history —
    before the agent starts, breaking #2035 --resume on every run. Asserting
    only that cleanup_isolated's *body* lacks the reclaim leaves this unpinned.
    """
    arm_full = _line_of(r"^\s*trap on_exit EXIT")
    preflight = _line_of(r"^\s*cleanup_isolated$")
    assert preflight < arm_full, "the bare cleanup_isolated pre-flight call is gone"
    for index in range(0, arm_full):
        assert not re.match(r"^\s*on_exit$", LINES[index]), (
            f"on_exit is called at line {index + 1}, before the full handler is "
            f"armed — at that point it would delete the just-restored .claude"
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


def test_history_is_rescued_by_rename_not_copy():
    """`mv` is load-bearing and the end state cannot distinguish it from `cp`.

    Both leave a copy at the preserve path, so a behavioural test sees no
    difference. The difference only shows on a full tmpfs — the condition this
    issue is about: rename needs no space, while `cp -R` needs room for a second
    copy, fails, is swallowed by `|| true`, and the following `rm -rf` then
    destroys the only remaining copy of the session history.
    """
    body = re.search(r"^\s*reclaim_task_tree\(\) \{\n(.*?)^\s*\}$", SRC, re.MULTILINE | re.DOTALL)
    assert body, "reclaim_task_tree() not found"
    assert re.search(r'mv "\$task_home/\.claude" "\$preserve_claude_dir"', body.group(1))
    assert "cp " not in body.group(1), "the rescue must be an atomic rename, not a copy"


def test_acl_registry_is_removed_at_its_point_of_consumption():
    """Truncating instead of removing leaves a stray empty registry behind.

    It must be removed inside the revocation helper, never in
    reclaim_task_tree: on the early `exit 66` path the revocation has not run
    yet, so deleting the record there would orphan the agent's write ACLs.
    """
    body = re.search(r"^\s*_revoke_task_acls\(\) \{\n(.*?)^\s*\}$", SRC, re.MULTILINE | re.DOTALL)
    assert body, "_revoke_task_acls() not found"
    assert 'rm -f "$acl_registry"' in body.group(1)
    assert ': > "$acl_registry"' not in SRC, "registry is truncated rather than removed"
    reclaim = re.search(
        r"^\s*reclaim_task_tree\(\) \{\n(.*?)^\s*\}$", SRC, re.MULTILINE | re.DOTALL
    )
    assert reclaim and "acl_registry" not in reclaim.group(1), (
        "reclaim_task_tree must not touch the ACL registry — on the early exit "
        "path that record has not been consumed yet"
    )


def test_acl_registry_is_created_with_a_tight_umask():
    """A separate chmod would leave a world-readable window on every run."""
    assert re.search(r"umask 077;.*acl_registry", SRC), (
        "the registry must be created under umask 077 in the same step as the "
        "write; it is recreated fresh every run now that it is removed"
    )


def test_zero_age_threshold_is_rejected():
    """`0` passes a naive numeric guard and means 'delete every history'."""
    assert re.search(r"^\s*0\|''\|\*\[!0-9\]\*\)", SRC, re.MULTILINE), (
        "agent_task_preserve_max_age_days=0 would satisfy a digits-only check "
        "and reap every session history on every run"
    )


def test_the_disproven_reconciler_comment_is_gone():
    """That comment is why the leak went unnoticed: it described a mechanism
    nobody ever implemented."""
    assert "reconciled by the scheduler on restart" not in SRC
