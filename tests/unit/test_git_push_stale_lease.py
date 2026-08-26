"""``git_push(force_with_lease=True)`` recovers from a stale-lease rejection.

Root cause (reproducer: ee678c63 / #2499 reset path): when a workflow worktree
is recreated after a failure cleanup, its remote-tracking ref
(``refs/remotes/origin/auto-dev/<id>``) is stale relative to the actual remote
tip. ``git push --force-with-lease`` then rejects with ``! [rejected] ... (stale
info)``. That rejection is (correctly) classified transient at the orchestrator
Layer-2 (``_TRANSIENT_ORCHESTRATOR_KEYWORDS`` in constants.py, whose docstring
says the retry should "re-read the remote ref"), but neither Layer-1
(``_run_git``) nor Layer-2 actually runs the ``git fetch`` that refreshes the
lease — so each retry re-runs the identical push and loops to exhaustion
(``TRANSIENT_RETRY_MAX``), failing the workflow. It is mislabeled "transient
network error".

Fix: ``git_push`` catches a stale-lease ``GitHubOpsError``, runs a targeted
``git fetch <remote> <branch>`` to refresh the lease, and retries the push
once. Safe because ``force_with_lease`` is already refused unless the branch is
managed by a workflow (single-workflow-owned, local-authoritative), so overwriting
the remote after a fresh fetch is the intended semantics. Network errors are not
recovered here — fetching cannot fix them and the orchestrator Layer-2 retry
remains the backstop.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.modules.workspace.autonomous.github_ops import GitHubOps, GitHubOpsError


def _result(returncode: int, stderr: str = "", stdout: str = "") -> MagicMock:
    return MagicMock(returncode=returncode, stdout=stdout, stderr=stderr)


class TestGitPushStaleLeaseRecovery:
    """``--force-with-lease`` stale-lease rejection → fetch + retry once."""

    def setup_method(self):
        self.gh = GitHubOps("/tmp/test-repo")

    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_stale_info_rejection_fetches_and_retries(self, mock_run):
        """A stale-lease rejection triggers a fetch then a successful retry."""
        push_fail = _result(
            1,
            stderr=(
                "To https://github.com/open-ace/open-ace.git\n"
                " ! [rejected]            auto-dev/abc12345 -> auto-dev/abc12345 (stale info)\n"
            ),
        )
        fetch_ok = _result(0)
        push_ok = _result(0)
        mock_run.side_effect = [push_fail, fetch_ok, push_ok]

        # Must not raise — fetch refreshed the lease, retry succeeded.
        self.gh.git_push(branch="auto-dev/abc12345", force_with_lease=True)

        # Exactly three git invocations: push (fail) → fetch → push (ok).
        assert mock_run.call_count == 3
        cmds = [c[0][0] for c in mock_run.call_args_list]
        assert "push" in cmds[0]
        assert "fetch" in cmds[1]
        assert "auto-dev/abc12345" in cmds[1]
        assert "push" in cmds[2]

    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_fetch_first_rejection_also_recovers(self, mock_run):
        """``fetch first`` (remote ahead) is the same recoverable lease class."""
        push_fail = _result(
            1, stderr=" ! [rejected] auto-dev/abc12345 -> auto-dev/abc12345 (fetch first)\n"
        )
        mock_run.side_effect = [push_fail, _result(0), _result(0)]
        self.gh.git_push(branch="auto-dev/abc12345", force_with_lease=True)
        cmds = [c[0][0] for c in mock_run.call_args_list]
        assert "fetch" in cmds[1]

    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_non_stale_error_propagates_without_fetch(self, mock_run):
        """A non-lease error (e.g. permission) must not trigger a fetch."""
        push_fail = _result(1, stderr=" remote: Permission denied (publickey)\n")
        mock_run.side_effect = [push_fail]
        with pytest.raises(GitHubOpsError):
            self.gh.git_push(branch="auto-dev/abc12345", force_with_lease=True)
        # Only the push ran — no recovery fetch.
        assert mock_run.call_count == 1
        assert "fetch" not in mock_run.call_args_list[0][0][0]

    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_retry_failure_propagates(self, mock_run):
        """If the post-fetch retry still fails, the error propagates."""
        push_fail = _result(1, stderr=" ! [rejected] auto-dev/abc12345 (stale info)\n")
        fetch_ok = _result(0)
        push_fail_again = _result(1, stderr=" remote: barred by branch protection\n")
        mock_run.side_effect = [push_fail, fetch_ok, push_fail_again]
        with pytest.raises(GitHubOpsError):
            self.gh.git_push(branch="auto-dev/abc12345", force_with_lease=True)
        assert mock_run.call_count == 3  # push, fetch, retry-push — no further fetch

    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_fetch_failure_propagates_original_push_error(self, mock_run):
        """If the lease-refresh fetch itself fails, the ORIGINAL push error rises.

        The caller must see the push rejection (so the orchestrator Layer-2
        transient classifier still matches it), not the fetch error — otherwise
        a fetch glitch would mask the recoverable lease rejection.
        """
        push_fail = _result(1, stderr=" ! [rejected] auto-dev/abc12345 (stale info)\n")
        fetch_fail = _result(1, stderr=" fatal: bad revision 'auto-dev/abc12345'\n")
        mock_run.side_effect = [push_fail, fetch_fail]
        with pytest.raises(GitHubOpsError) as exc:
            self.gh.git_push(branch="auto-dev/abc12345", force_with_lease=True)
        # Original push error propagates; the fetch error does not.
        assert "stale info" in str(exc.value)
        assert "bad revision" not in str(exc.value)
        assert mock_run.call_count == 2  # push, failed fetch — no retry push

    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_plain_push_no_force_does_not_recover(self, mock_run):
        """Recovery is force-with-lease specific; a plain push failure just raises."""
        mock_run.side_effect = [_result(1, stderr=" ! [rejected] x (stale info)\n")]
        with pytest.raises(GitHubOpsError):
            self.gh.git_push(branch="auto-dev/abc12345")
        assert mock_run.call_count == 1
