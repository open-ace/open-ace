"""Bug A: git_add_all must not stage test-pollution artifacts.

When the dev agent runs pytest in its worktree, test side-effects create
empty files whose names are repr() of MagicMock objects (mocks passed to
Path(...)/open(...)), plus the usual __pycache__/.pytest_cache. These are
not legitimate source files and must not be committed.
"""

from unittest.mock import MagicMock

from app.modules.workspace.autonomous.github_ops import GitHubOps


def _make_git_ops():
    """Construct a GitHubOps whose _run_git is a controllable mock."""
    gh = GitHubOps.__new__(GitHubOps)
    gh.repo_path = "/tmp/test-repo"
    return gh


def test_git_add_all_filters_magicmock_and_pycache_artifacts():
    """git_add_all must unstage MagicMock-named files and __pycache__.

    After git_add_all(), only legitimate source files should remain staged.
    """
    gh = _make_git_ops()

    # The set of staged paths as seen by ``git diff --cached --name-only``.
    # This mirrors what prod sees: ``git add -A`` stages everything including
    # the MagicMock repr files and __pycache__.
    staged_after_add = [
        "app/real_file.py",  # legitimate
        "<MagicMock id='1234567890'>",  # test pollution (MagicMock repr)
        "<MagicMock name='mock.obj' id='9876543210'>",
        "__pycache__/something.cpython-311.pyc",  # test cache
        ".pytest_cache/v/cache/lastfailed",  # pytest cache
        "tests/test_real.py",  # legitimate
    ]

    call_log = []

    def fake_run_git(args, check=True):
        call_log.append(list(args))
        if args[0] == "add" and "-A" in args:
            return MagicMock(returncode=0, stdout="")
        if args[0] == "rm" and "--cached" in args:
            return MagicMock(returncode=0, stdout="")
        if args[0] == "diff" and "--cached" in args:
            return MagicMock(returncode=0, stdout="\n".join(staged_after_add) + "\n")
        if args[0] == "reset":
            # Simulate un-staging: remove the path from staged_after_add.
            path = args[-1]
            if path in staged_after_add:
                staged_after_add.remove(path)
            return MagicMock(returncode=0, stdout="")
        return MagicMock(returncode=0, stdout="")

    gh._run_git = MagicMock(side_effect=fake_run_git)

    gh.git_add_all()

    # The legitimate files must still be staged.
    assert "app/real_file.py" in staged_after_add, "legitimate file was wrongly unstaged"
    assert "tests/test_real.py" in staged_after_add, "legitimate test file was wrongly unstaged"

    # The MagicMock repr files must have been unstaged.
    assert (
        "<MagicMock id='1234567890'>" not in staged_after_add
    ), "MagicMock-named test artifact was not filtered"
    assert (
        "<MagicMock name='mock.obj' id='9876543210'>" not in staged_after_add
    ), "MagicMock-named test artifact was not filtered"

    # The standard test caches must have been unstaged.
    assert (
        "__pycache__/something.cpython-311.pyc" not in staged_after_add
    ), "__pycache__ artifact was not filtered"
    assert (
        ".pytest_cache/v/cache/lastfailed" not in staged_after_add
    ), ".pytest_cache artifact was not filtered"

    # Verify a reset HEAD -- was issued for each offending path.
    reset_calls = [c for c in call_log if c[0] == "reset"]
    assert (
        len(reset_calls) >= 4
    ), f"expected at least 4 reset calls for artifact paths, got {len(reset_calls)}: {reset_calls}"
