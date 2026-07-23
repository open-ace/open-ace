"""Integration tests for the repo-drift guardrail using a real git repo.

The unit tests in ``test_repo_drift_validation.py`` mock ``GitHubOps`` and
verify the orchestrator's if/else and fail-closed branching. They do NOT
exercise real ``git merge-base`` / ``git fetch`` semantics. These tests build a
real local git repository (a bare "origin" + a working clone) and drive
``_main_drift_is_benign_pull`` against it with ``system_account=None``, so the
two core paths run through actual git:

  - An external ``git pull`` (main fast-forwards to a remote commit) → allowed.
  - A local escape ``git commit`` on main (not pushed) → blocked.

This protects the key assumption the unit-mocked tests rely on: that a
forward, remote-sourced move is graph-distinguishable from a local commit.
"""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from app.modules.workspace.autonomous.github_ops import GitHubOps
from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator


def _git(repo, *args, check=True):
    """Run git in ``repo`` and return the completed process."""
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=check,
    )


def _build_repo(tmp_path):
    """Create a bare origin + a working clone with one commit on main.

    Returns (clone_path, base_commit_sha).
    """
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "--bare", str(origin))
    clone = tmp_path / "repo"
    _git(tmp_path, "clone", str(origin), str(clone))
    _git(clone, "config", "user.email", "t@t.test")
    _git(clone, "config", "user.name", "Test")
    (clone / "f.txt").write_text("v1\n")
    _git(clone, "add", "f.txt")
    _git(clone, "commit", "-m", "c1")
    _git(clone, "branch", "-M", "main")
    _git(clone, "push", "-q", "origin", "main")
    _git(clone, "fetch", "-q", "origin")
    base = _git(clone, "rev-parse", "HEAD").stdout.strip()
    return clone, base


def _add_remote_commit(origin, clone):
    """Add a new commit on origin/main (simulating a collaborator push).

    Returns the new commit SHA. Pushed via a second clone so the original
    clone's main isn't moved yet. Uses ``HEAD:refs/heads/main`` so the push is
    independent of the clone's default branch name (CI runners may default to
    ``master``).
    """
    other = clone.parent / "other"
    _git(clone.parent, "clone", "-q", str(origin), str(other))
    _git(other, "config", "user.email", "t@t.test")
    _git(other, "config", "user.name", "Test")
    _git(other, "checkout", "-q", "-B", "main", "origin/main")
    (other / "f.txt").write_text("v2\n")
    _git(other, "add", "f.txt")
    _git(other, "commit", "-q", "-m", "remote c2")
    _git(other, "push", "-q", "origin", "HEAD:refs/heads/main")
    return _git(other, "rev-parse", "HEAD").stdout.strip()


def _make_orchestrator():
    wf = {"workflow_id": "wf-drift-int"}
    with (
        patch("app.modules.workspace.autonomous.orchestrator.Database"),
        patch(
            "app.modules.workspace.autonomous.orchestrator.AutonomousWorkflowRepository"
        ) as mock_repo_cls,
    ):
        mock_repo = MagicMock()
        mock_repo.get_workflow.return_value = wf
        mock_repo_cls.return_value = mock_repo
        o = AutonomousOrchestrator(wf["workflow_id"])
        o.repo = mock_repo
    return o


@pytest.mark.regression
class TestRepoDriftIntegration:
    def test_external_pull_is_allowed(self, tmp_path):
        # main starts at base. A collaborator pushes c2 to origin; the local
        # clone then `git pull`s it. before=base, after=c2: forward AND on
        # origin/main → benign → allowed.
        clone, base = _build_repo(tmp_path)
        origin = tmp_path / "origin.git"
        c2 = _add_remote_commit(origin, clone)
        # Simulate the external pull on the working clone.
        _git(clone, "pull", "-q", "--ff-only", "origin", "main")
        after = _git(clone, "rev-parse", "HEAD").stdout.strip()
        assert after == c2

        o = _make_orchestrator()
        assert o._main_drift_is_benign_pull(str(clone), base, after, None) is True

    def test_local_escape_commit_is_blocked(self, tmp_path):
        # main starts at base. Agent makes a local commit on main WITHOUT
        # pushing. before=base, after=local: forward BUT not on origin/main →
        # blocked.
        clone, base = _build_repo(tmp_path)
        (clone / "g.txt").write_text("agent hack\n")
        _git(clone, "add", "g.txt")
        _git(clone, "commit", "-q", "-m", "agent escape")
        after = _git(clone, "rev-parse", "HEAD").stdout.strip()

        o = _make_orchestrator()
        assert o._main_drift_is_benign_pull(str(clone), base, after, None) is False

    def test_scope_guard_uses_branch_point_after_origin_main_advances(self, tmp_path):
        clone, base = _build_repo(tmp_path)
        origin = tmp_path / "origin.git"
        _git(clone, "checkout", "-q", "-b", "auto-dev/test", base)
        (clone / "agent.py").write_text("answer = 42\n")
        _git(clone, "add", "agent.py")
        _git(clone, "commit", "-q", "-m", "agent change")
        head = _git(clone, "rev-parse", "HEAD").stdout.strip()

        # Main advances independently after the workflow branch was created.
        # A diff against moving origin/main contains both f.txt and agent.py;
        # a diff against the merge-base contains only the autonomous file.
        _add_remote_commit(origin, clone)
        _git(clone, "fetch", "-q", "origin", "main")

        o = _make_orchestrator()
        o._update_workflow = MagicMock()
        gh = GitHubOps(str(clone))
        with patch("app.modules.workspace.autonomous.orchestrator.MAX_AUTONOMOUS_CHANGED_FILES", 1):
            reason = o._validate_autonomous_change_scope(gh, {}, base, head)

        assert reason == ""
        o._update_workflow.assert_called_once_with({"base_commit_sha": base})
