from unittest.mock import MagicMock, patch

import pytest

from app.modules.workspace.autonomous.github_ops import GitHubOps, GitHubOpsError


def _result(returncode: int, stderr: str = "", stdout: str = "") -> MagicMock:
    return MagicMock(returncode=returncode, stdout=stdout, stderr=stderr)


class TestDeletedRemoteBranchPushRecovery:
    def setup_method(self):
        self.gh = GitHubOps("/tmp/test-repo")

    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_stale_info_with_missing_remote_ref_plain_pushes_validated_auto_dev_branch(
        self, mock_run
    ):
        push_fail = _result(
            1,
            stderr=(
                "To https://github.com/open-ace/open-ace.git\n"
                " ! [rejected] auto-dev/abc12345 -> auto-dev/abc12345 (stale info)\n"
            ),
        )
        fetch_missing = _result(
            128,
            stderr="fatal: couldn't find remote ref auto-dev/abc12345\n",
        )
        plain_push_ok = _result(
            0, stderr=" * [new branch] auto-dev/abc12345 -> auto-dev/abc12345\n"
        )
        mock_run.side_effect = [push_fail, fetch_missing, plain_push_ok]

        self.gh.git_push(branch="auto-dev/abc12345", force_with_lease=True)

        cmds = [call_args[0][0] for call_args in mock_run.call_args_list]
        assert cmds[0][-1] == "--force-with-lease"
        assert "fetch" in cmds[1]
        assert "auto-dev/abc12345" in cmds[1]
        assert "push" in cmds[2]
        assert "auto-dev/abc12345" in cmds[2]
        assert "--force-with-lease" not in cmds[2]

    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_stale_info_with_fetch_success_keeps_force_with_lease_retry(self, mock_run):
        mock_run.side_effect = [
            _result(1, stderr=" ! [rejected] auto-dev/abc12345 (stale info)\n"),
            _result(0),
            _result(0),
        ]

        self.gh.git_push(branch="auto-dev/abc12345", force_with_lease=True)

        cmds = [call_args[0][0] for call_args in mock_run.call_args_list]
        assert "fetch" in cmds[1]
        assert cmds[2][-1] == "--force-with-lease"

    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_network_error_still_raises_without_deleted_ref_fallback(self, mock_run):
        network_failure = _result(
            1,
            stderr=(
                "fatal: unable to access "
                "'https://github.com/open-ace/open-ace.git/': connection timed out\n"
            ),
        )
        mock_run.side_effect = [network_failure, network_failure, network_failure]

        with pytest.raises(GitHubOpsError, match="connection timed out"):
            self.gh.git_push(branch="auto-dev/abc12345", force_with_lease=True)

        assert mock_run.call_count == 3
        assert all("fetch" not in call_args[0][0] for call_args in mock_run.call_args_list)

    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_non_auto_dev_force_with_lease_is_still_refused_before_push(self, mock_run):
        with pytest.raises(GitHubOpsError, match="non-auto-dev branch 'main'"):
            self.gh.git_push(branch="main", force_with_lease=True)

        mock_run.assert_not_called()
