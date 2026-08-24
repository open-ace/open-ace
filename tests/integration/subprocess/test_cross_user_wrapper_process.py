"""Cross-user wrapper contract tests — the openace-run-as.sh cluster (Issue #1395).

Three tests execute REAL bash (flock ACL-lock scoping, background-wait stdin
EOF, git-entry ACL restore via extracted shell helpers + setfacl); four pin
the wrapper script's source contract (git safe.directory scoping, signal
traps, ACL normalization, stale-signature skip) — the sudo-root wrapper
cannot be behavior-tested in CI, so its source is the pinned artifact
(same exemption class as the 1748/1760 deployment pins). The mock-driven
Python-side companions live in tests/unit/test_cross_user_permissions.py.
"""

import base64
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.security,
    pytest.mark.regression,
    pytest.mark.issue(1395),
]

WRAPPER_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "openace-run-as.sh"


def test_isolated_wrapper_scopes_git_safe_directory_to_worktree():

    wrapper = WRAPPER_SCRIPT.read_text(encoding="utf-8")

    assert '"GIT_CONFIG_COUNT=1"' in wrapper
    assert '"GIT_CONFIG_KEY_0=safe.directory"' in wrapper
    assert '"GIT_CONFIG_VALUE_0=$project_dir"' in wrapper
    assert "git config --global" not in wrapper


def test_isolated_wrapper_can_handle_signals_while_waiting_for_agent():

    wrapper = WRAPPER_SCRIPT.read_text(encoding="utf-8")

    # #2403: the EXIT trap is now `on_exit` (full tree reclaim), with an
    # earlier-armed 'reclaim_task_tree || true' EXIT trap covering the
    # pre-child window; HUP/INT/TERM exit-code traps are registered twice
    # (task setup + child wait).
    assert "trap on_exit EXIT" in wrapper
    assert "trap 'reclaim_task_tree || true' EXIT" in wrapper
    assert wrapper.count("trap 'exit 129' HUP") == 2
    assert wrapper.count("trap 'exit 130' INT") == 2
    assert wrapper.count("trap 'exit 143' TERM") == 2
    assert '"$@" <&0 9>&- &' in wrapper
    assert "agent_child_pid=$!" in wrapper
    assert 'wait "$agent_child_pid"' in wrapper


def test_background_child_does_not_inherit_acl_lock(tmp_path):
    """A SIGKILLed wrapper must not strand fd 9 in its live child."""
    if not shutil.which("flock"):
        pytest.skip("flock is required for the launcher lock regression")

    lock_path = tmp_path / "agent.lock"
    child_pid_path = tmp_path / "child.pid"
    wrapper = subprocess.Popen(
        [
            "bash",
            "-c",
            (
                'exec 9>"$1"; flock -x 9; '
                'sleep 30 9>&- & child=$!; printf "%s" "$child" > "$2"; wait "$child"'
            ),
            "openace-lock-test",
            str(lock_path),
            str(child_pid_path),
        ]
    )
    child_pid = 0
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if child_pid_path.exists() and child_pid_path.read_text().strip():
                child_pid = int(child_pid_path.read_text().strip())
                break
            time.sleep(0.05)
        assert child_pid > 0

        wrapper.kill()
        wrapper.wait(timeout=5)
        acquired = subprocess.run(
            ["flock", "-w", "1", str(lock_path), "true"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert acquired.returncode == 0
    finally:
        if wrapper.poll() is None:
            wrapper.kill()
            wrapper.wait(timeout=5)
        if child_pid:
            try:
                os.kill(child_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_isolated_wrapper_normalizes_recovered_acls_before_git_baseline():
    """Launcher ACL recovery precedes exact signature/ACL verification.

    The durable registry carries the exact ACL snapshot, cleanup runs before
    recovery, and the next baseline is published before any new ACL grant.
    """

    wrapper = WRAPPER_SCRIPT.read_text(encoding="utf-8")

    # The registry is keyed by the per-attempt isolation key (#2437/#2020:
    # task-<id> when sharded, uid-<uid> otherwise) — no longer target_uid.
    signature_registry = 'signature_registry="/run/openace-agent-git-signature-${isolation_key}"'
    recovery = '"$previous_git_acl_snapshot"; then'
    current_baseline = 'git_entry_before="$(git_entry_signature "$project_dir")"'
    current_acl_baseline = 'git_acl_before="$(git_entry_acl_snapshot "$project_dir")"'
    first_acl_grant = 'setfacl -m "u:${target_user}:x" "$parent"'
    final_verification = (
        'if ! verify_and_restore_git_entry "$project_dir" "$git_entry_before" "$git_acl_before";'
    )

    assert signature_registry in wrapper
    assert 'previous_git_acl_snapshot="$(sed -n \'3p\' "$signature_registry")"' in wrapper
    assert wrapper.index("cleanup_isolated\n") < wrapper.index(recovery)
    assert wrapper.index(recovery) < wrapper.index(current_baseline)
    assert wrapper.index(current_baseline) < wrapper.index(first_acl_grant)
    assert wrapper.index(current_acl_baseline) < wrapper.index(first_acl_grant)
    assert wrapper.index("printf '%s\\n%s\\n%s\\n' \\") < wrapper.index(first_acl_grant)
    # Post-wait reclaim: the explicit on_exit call (the EXIT trap is disarmed
    # on the success path right after it — #2403) precedes final verification.
    assert wrapper.index("    on_exit\n", wrapper.index('wait "$agent_child_pid"')) < (
        wrapper.index(final_verification)
    )
    assert wrapper.index(final_verification) < wrapper.index(
        'rm -f "$signature_registry"', wrapper.index(final_verification)
    )


def test_stale_signature_registry_skipped_for_different_project():
    """A per-user signature registry left by an interrupted run on a different
    project (e.g. a throwaway merge-<wf> worktree cleaned up by the
    orchestrator) must not trigger OPENACE_REPO_INTEGRITY_VIOLATION for an
    unrelated subsequent run.

    The registry is keyed by the shared isolation account (uid), not the
    project path, so a stale entry from a different project is discarded
    instead of being compared against the current project's .git.
    """

    wrapper = WRAPPER_SCRIPT.read_text(encoding="utf-8")

    previous_block = (
        'if [ -n "$previous_signature_project" ] && [ -n "$previous_git_signature" ]; then'
    )
    same_project_guard = '"$previous_signature_project" = "$project_dir"'
    interrupted_violation = (
        "OPENACE_REPO_INTEGRITY_VIOLATION: .git entry changed during interrupted agent execution"
    )

    assert previous_block in wrapper
    block_start = wrapper.index(previous_block)

    # The same-project guard must appear inside the previous-signature block…
    assert same_project_guard in wrapper[block_start:]
    guard_pos = wrapper.index(same_project_guard, block_start)
    # …and it must come before the interrupted-execution violation message,
    # so a stale registry from a *different* project is discarded without
    # triggering the violation.
    assert guard_pos < wrapper.index(interrupted_violation, guard_pos)


def test_git_entry_acl_restore_preserves_exact_integrity(tmp_path):
    """Only launcher-shaped mask churn is restored; real changes fail."""
    if sys.platform != "linux":
        pytest.skip("GNU stat signature regression is Linux-specific")
    if not shutil.which("setfacl"):
        pytest.skip("setfacl is required for the ACL mask regression")

    from textwrap import dedent

    wrapper = WRAPPER_SCRIPT.read_text(encoding="utf-8")
    function_start = wrapper.index("    normalize_group_class_signature() {")
    function_end = function_start
    # Extract every consecutive helper from normalize_group_class_signature
    # through verify_and_restore_git_entry. The count must track the number of
    # helpers defined in that span (bumped to 7 when
    # signatures_differ_only_by_inode was inserted between them for #2529).
    for _ in range(7):
        function_end = wrapper.index("\n    }\n", function_end + 1) + len("\n    }")
    signature_functions = dedent(wrapper[function_start:function_end])
    project = tmp_path / "project"
    project.mkdir()
    git_entry = project / ".git"
    git_entry.write_text("gitdir: /safe/metadata\n", encoding="utf-8")

    def shell_value(expression: str) -> str:
        result = subprocess.run(
            ["bash", "-c", f"{signature_functions}\n{expression}", "signature", project],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout

    def signature() -> str:
        return shell_value('git_entry_signature "$1"')

    def acl_snapshot() -> str:
        return shell_value('git_entry_acl_snapshot "$1"')

    def verify(expected_signature: str, expected_acl: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [
                "bash",
                "-c",
                f'{signature_functions}\nverify_and_restore_git_entry "$1" "$2" "$3"',
                "verify",
                project,
                expected_signature,
                expected_acl,
            ],
            capture_output=True,
            text=True,
            check=False,
        )

    # Extended ACL: launcher mask churn is restored to the exact baseline.
    subprocess.run(
        ["setfacl", "-m", f"u:{os.getuid()}:rwx,m::rw", str(git_entry)],
        check=True,
    )
    git_entry.chmod(0o664)
    baseline = signature()
    baseline_acl = acl_snapshot()
    git_entry.chmod(0o674)
    assert signature() != baseline
    assert verify(baseline, baseline_acl).returncode == 0
    assert signature() == baseline
    assert acl_snapshot() == baseline_acl

    # Owner/other permissions, content, inode, and non-mask ACL changes fail.
    git_entry.chmod(0o675)
    assert verify(baseline, baseline_acl).returncode != 0
    git_entry.chmod(0o664)
    git_entry.write_text("gitdir: /tampered/metadata\n", encoding="utf-8")
    assert verify(baseline, baseline_acl).returncode != 0
    git_entry.write_text("gitdir: /safe/metadata\n", encoding="utf-8")
    subprocess.run(["setfacl", "-m", "g::rwx", str(git_entry)], check=True)
    assert verify(baseline, baseline_acl).returncode != 0
    subprocess.run(
        ["setfacl", "-n", "--set-file=-", str(git_entry)],
        input=base64.b64decode(baseline_acl),
        check=True,
    )
    git_entry.unlink()
    git_entry.write_text("gitdir: /safe/metadata\n", encoding="utf-8")
    git_entry.chmod(0o664)
    assert verify(baseline, baseline_acl).returncode != 0

    # Plain ACL -> launcher mask-only ACL is restored exactly, while a plain
    # Unix group-mode change (group::, not mask::) is rejected.
    git_entry.unlink()
    git_entry.write_text("gitdir: /safe/metadata\n", encoding="utf-8")
    subprocess.run(["setfacl", "-b", str(git_entry)], check=True)
    git_entry.chmod(0o600)
    plain_baseline = signature()
    plain_acl = acl_snapshot()
    subprocess.run(["setfacl", "-m", f"u:{os.getuid()}:r", str(git_entry)], check=True)
    subprocess.run(["setfacl", "-x", f"u:{os.getuid()}", str(git_entry)], check=True)
    assert verify(plain_baseline, plain_acl).returncode == 0
    assert signature() == plain_baseline
    assert acl_snapshot() == plain_acl

    git_entry.chmod(0o640)
    assert verify(plain_baseline, plain_acl).returncode != 0

    # Legacy two-line registries get one restricted group-class-compatible
    # recovery, then the caller immediately writes the exact ACL-aware format.
    subprocess.run(
        ["setfacl", "-n", "--set-file=-", str(git_entry)],
        input="user::rw-\ngroup::---\nother::---\n",
        text=True,
        check=True,
    )
    git_entry.chmod(0o674)
    legacy_expected = signature().replace(":674:", ":664:")
    assert verify(legacy_expected, "").returncode != 0
    subprocess.run(
        ["setfacl", "-m", f"u:{os.getuid()}:rwx,m::rwx", str(git_entry)],
        check=True,
    )
    assert verify(legacy_expected, "").returncode == 0


def test_background_wait_keeps_runner_stdin_until_eof():
    import subprocess

    result = subprocess.run(
        [
            "bash",
            "-c",
            '{ IFS= read -r line; printf "received=%s" "$line"; } <&0 & ' 'child=$!; wait "$child"',
        ],
        input="hello-from-runner\n",
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout == "received=hello-from-runner"


# ── _wrap_agent_cmd / _is_cross_user (agent launch) ─────────────────────
