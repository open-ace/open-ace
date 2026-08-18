"""Tests for scripts/push.sh — the interactive-session push gate.

Regression coverage for #2721: the gate must fold formatter autofixes into
the commit being pushed AND still push when the only failures were the
autofixes themselves (pre-commit exits 1 whenever a hook modifies a file).
Earlier versions aborted on exactly that path, so the script never pushed in
the scenario it existed for, could amend commits already published on a
remote branch, and folded unrelated work-in-progress into the push.

The tests drive a scratch git repo with a bare "origin" and a stub
``pre-commit`` on PATH whose behaviour is scripted per invocation via a plan
file: each line ``<rc> [text]`` means "exit rc, appending text to the target
file first". That is enough to model every outcome the real hooks produce
(fix-and-fail, clean pass, unfixable findings, endless oscillation).
"""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PUSH_SCRIPT = REPO_ROOT / "scripts" / "push.sh"

STUB_PRE_COMMIT = """#!/bin/bash
# Test stub for pre-commit. Plan file: one "<rc> [text]" line per
# invocation; appends text to $PUSH_STUB_TARGET before exiting rc.
set -eu
state_dir="${PUSH_STUB_STATE:?}"
target="${PUSH_STUB_TARGET:?}"
count_file="$state_dir/count"
n=$(( $(cat "$count_file" 2>/dev/null || echo 0) + 1 ))
echo "$n" > "$count_file"
line="$(sed -n "${n}p" "$state_dir/plan")"
rc="${line%% *}"
[ -n "$rc" ] || rc=0
text="${line#* }"
if [ -n "$text" ] && [ "$text" != "$line" ]; then
    printf '%s\\n' "$text" >> "$target"
fi
exit "$rc"
"""


def _git_is_functional() -> bool:
    """True only if git can initialize repositories."""
    try:
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                ["git", "init", "--bare", str(Path(tmp) / "test.git")],
                capture_output=True,
                timeout=2,
            )
            return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


pytestmark = [
    pytest.mark.skipif(
        not _git_is_functional(),
        reason="git init is restricted or non-functional - required for push.sh tests",
    ),
    pytest.mark.regression,
    pytest.mark.issue(2721),
]


def _git(repo, *args, check=True):
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=check,
    )


def _make_repo(tmp_path, push_topic=True, set_upstream=True, extra_commit=True):
    """Scratch repo: bare origin, main pushed, topic branch off it.

    Ends on ``topic`` with a ``feat`` commit; when ``push_topic`` the remote
    branch exists at ``feat`` (upstream set when ``set_upstream``) and an
    extra unpushed ``feat2`` commit is added when ``extra_commit``.
    """
    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(origin)],
        check=True,
        capture_output=True,
    )
    work = tmp_path / "work"
    work.mkdir()
    _git(work, "init", "-b", "main")
    _git(work, "config", "user.email", "test@example.com")
    _git(work, "config", "user.name", "Test")
    (work / "base.txt").write_text("base\n")
    _git(work, "add", ".")
    _git(work, "commit", "-m", "base")
    _git(work, "remote", "add", "origin", str(origin))
    _git(work, "push", "origin", "main")
    _git(work, "checkout", "-b", "topic")
    (work / "code.py").write_text("x = 1\n")
    _git(work, "add", ".")
    _git(work, "commit", "-m", "feat")
    if push_topic:
        _git(work, "push", *(["-u"] if set_upstream else []), "origin", "topic")
    if extra_commit:
        (work / "code2.py").write_text("y = 2\n")
        _git(work, "add", ".")
        _git(work, "commit", "-m", "feat2")
    return work, origin


def _make_stub(tmp_path, plan_lines):
    """Put a stub pre-commit on PATH; return (env, state_dir)."""
    stub_dir = tmp_path / "stub-bin"
    stub_dir.mkdir(exist_ok=True)
    stub = stub_dir / "pre-commit"
    stub.write_text(STUB_PRE_COMMIT)
    stub.chmod(0o755)
    state = tmp_path / "stub-state"
    state.mkdir(exist_ok=True)
    (state / "plan").write_text("".join(f"{line}\n" for line in plan_lines))
    env = dict(os.environ)
    env["PATH"] = f"{stub_dir}{os.pathsep}{env['PATH']}"
    env["PUSH_STUB_STATE"] = str(state)
    env["PUSH_STUB_TARGET"] = str(tmp_path / "work" / "code.py")
    env.pop("ACE_PUSH_SKIP_CHECK", None)
    return env, state


def _run_script(repo, env, *args):
    return subprocess.run(
        ["bash", str(PUSH_SCRIPT), *args],
        cwd=str(repo),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _calls(state):
    count_file = state / "count"
    return int(count_file.read_text()) if count_file.exists() else 0


def _remote_tip(origin, branch="topic"):
    return _git(origin, "rev-parse", f"refs/heads/{branch}", check=False).stdout.strip()


def _local_tip(repo, branch="topic"):
    return _git(repo, "rev-parse", f"refs/heads/{branch}").stdout.strip()


class TestPushScript:
    def test_autofixes_are_folded_and_pushed(self, tmp_path):
        """The reason the script exists: hooks fix files (exit 1), the re-run
        is clean — the push must happen with the fixes folded in."""
        work, origin = _make_repo(tmp_path)
        env, state = _make_stub(tmp_path, ["1 fix", "0 "])
        before_tip = _local_tip(work)

        result = _run_script(work, env)

        assert result.returncode == 0, result.stderr
        assert _calls(state) == 2, "expected a confirmation pass after the fold"
        after_tip = _local_tip(work)
        assert after_tip != before_tip, "autofixes were not folded into HEAD"
        assert _remote_tip(origin) == after_tip, "push did not reach the remote"
        assert _git(work, "log", "-1", "--pretty=%s").stdout.strip() == "feat2"
        assert _git(work, "rev-list", "--count", "origin/main..HEAD").stdout.strip() == "2"
        assert "fix" in (work / "code.py").read_text()

    def test_unfixable_failure_aborts_the_push(self, tmp_path):
        work, origin = _make_repo(tmp_path)
        env, state = _make_stub(tmp_path, ["1 "])
        before_remote = _remote_tip(origin)

        result = _run_script(work, env)

        assert result.returncode == 1
        assert _calls(state) == 1
        assert "not autofixable" in result.stderr
        assert _remote_tip(origin) == before_remote, "pushed despite unfixable failure"

    def test_unfixable_after_fixes_keeps_fixes_but_aborts(self, tmp_path):
        """Fixes land, the settled tree still fails — keep the folded fixes,
        refuse to push the known-red commit."""
        work, origin = _make_repo(tmp_path)
        env, state = _make_stub(tmp_path, ["1 fix", "1 "])
        before_tip = _local_tip(work)

        result = _run_script(work, env)

        assert result.returncode == 1
        assert _calls(state) == 2
        assert _local_tip(work) != before_tip, "applied fixes were not kept"
        assert "fixes were kept" in result.stderr
        assert _remote_tip(origin) == _git(work, "rev-parse", "HEAD~1").stdout.strip()

    def test_oscillation_never_pushes(self, tmp_path):
        """Hooks that keep modifying files never settle — no push."""
        work, origin = _make_repo(tmp_path)
        env, state = _make_stub(tmp_path, ["1 fix", "1 fix", "1 fix"])
        before_remote = _remote_tip(origin)

        result = _run_script(work, env)

        assert result.returncode == 1
        assert _calls(state) == 3, "the fixpoint loop must be capped"
        assert "still modified files on pass 3" in result.stderr
        assert _remote_tip(origin) == before_remote

    def test_refuses_to_amend_published_commit(self, tmp_path):
        """HEAD pushed without -u (upstream unset, remote branch contains
        it): folding must refuse to amend the published commit."""
        work, origin = _make_repo(tmp_path, push_topic=True, set_upstream=False, extra_commit=False)
        env, state = _make_stub(tmp_path, ["1 fix", "0 "])
        before_tip = _local_tip(work)

        result = _run_script(work, env)

        assert result.returncode == 1
        assert "refusing to amend a published commit" in result.stderr
        assert _local_tip(work) == before_tip, "published commit was rewritten"

    def test_refuses_to_run_on_main(self, tmp_path):
        work, origin = _make_repo(tmp_path)
        _git(work, "checkout", "main")
        env, state = _make_stub(tmp_path, ["1 fix"])

        result = _run_script(work, env)

        assert result.returncode == 1
        assert "refusing to commit on main" in result.stderr
        assert _calls(state) == 0

    def test_dirty_worktree_aborts_before_lint(self, tmp_path):
        """Pre-existing dirt must be rejected up front, never folded in."""
        work, origin = _make_repo(tmp_path)
        (work / "code.py").write_text("x = 1\n# unrelated WIP\n")
        env, state = _make_stub(tmp_path, ["0 "])

        result = _run_script(work, env)

        assert result.returncode == 1
        assert "uncommitted changes" in result.stderr
        assert _calls(state) == 0, "lint ran over unrelated dirt"

    def test_missing_precommit_degrades_to_warning_and_pushes(self, tmp_path):
        """No pre-commit on PATH: the mandated push route must still push."""
        work, origin = _make_repo(tmp_path)
        git_bin = Path(shutil.which("git")).resolve()
        bin_dir = tmp_path / "minimal-bin"
        bin_dir.mkdir()
        (bin_dir / "git").symlink_to(git_bin)
        minimal_path = os.pathsep.join([str(bin_dir), "/bin", "/usr/bin"])
        if shutil.which("pre-commit", path=minimal_path) is not None:
            pytest.skip("a real pre-commit is visible on the minimal PATH")
        env = dict(os.environ)
        env["PATH"] = minimal_path
        env.pop("ACE_PUSH_SKIP_CHECK", None)

        result = _run_script(work, env)

        assert result.returncode == 0, result.stderr
        assert "pre-commit not found" in result.stderr
        assert _remote_tip(origin) == _local_tip(work)

    def test_escape_hatch_skips_everything(self, tmp_path):
        work, origin = _make_repo(tmp_path)
        env, state = _make_stub(tmp_path, ["1 fix"])
        env["ACE_PUSH_SKIP_CHECK"] = "1"

        result = _run_script(work, env)

        assert result.returncode == 0, result.stderr
        assert _calls(state) == 0
        assert _remote_tip(origin) == _local_tip(work)

    def test_empty_branch_delta_skips_lint(self, tmp_path):
        """Nothing ahead of the remote — nothing to lint, push is a no-op."""
        work, origin = _make_repo(tmp_path, extra_commit=False)
        env, state = _make_stub(tmp_path, ["1 fix"])

        result = _run_script(work, env)

        assert result.returncode == 0, result.stderr
        assert _calls(state) == 0, "lint ran with an empty branch delta"
        assert "nothing to lint" in result.stdout
