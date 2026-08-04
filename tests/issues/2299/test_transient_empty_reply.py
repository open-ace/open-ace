"""Transient git-error classifier must catch "Empty reply from server" (#2299).

git emits libcurl's ``Empty reply from server`` verbatim (in English, even under
a non-C host locale) on a transient TLS/connection drop to the remote. Without
it in the transient-keyword lists, the exit-128 empty-reply GitHubOpsError is
classified NON-transient → wrapped as RuntimeError → the workflow permanent-fails
instead of Layer-2 retrying. Workflow 212 hit this twice on ``git push``.
"""

from __future__ import annotations

from app.modules.workspace.autonomous.constants import (
    _TRANSIENT_ORCHESTRATOR_KEYWORDS,
    _is_transient_git_error,
)
from app.modules.workspace.autonomous.github_ops import (
    _TRANSIENT_ERROR_KEYWORDS,
    GitHubOpsError,
    _is_transient_error,
)


def test_layer1_classifies_empty_reply_as_transient():
    """_is_transient_error (Layer-1 in-process retry) must catch empty-reply."""
    assert _is_transient_error("fatal: 无法访问: Empty reply from server", 128)
    assert _is_transient_error("error: empty response from server", 128)


def test_layer2_classifies_empty_reply_githubopserror_as_transient():
    """_is_transient_git_error (Layer-2 advance retry) must catch empty-reply
    on a GitHubOpsError so it propagates (not wrapped) + retries."""
    assert _is_transient_git_error(GitHubOpsError("git push failed: Empty reply from server"))
    assert _is_transient_git_error(GitHubOpsError("... empty response ..."))


def test_both_keyword_lists_in_sync_for_empty_reply():
    """The Layer-1 + Layer-2 lists are documented mirrors — empty-reply must be
    in BOTH so a transient empty-reply is retried at both layers."""
    assert "empty reply" in _TRANSIENT_ERROR_KEYWORDS
    assert "empty reply" in _TRANSIENT_ORCHESTRATOR_KEYWORDS


def test_non_transient_git_error_still_not_classified_transient():
    """Regression guard: a genuinely permanent git error is still non-transient."""
    assert not _is_transient_error("fatal: not a git repository", 128)
    assert not _is_transient_error("error: pathspec 'foo' did not match", 1)
    assert not _is_transient_git_error(GitHubOpsError("merge conflict"))


def test_transient_still_requires_nonzero_exit():
    """_is_transient_error never classifies a successful (exit 0) run as transient."""
    assert not _is_transient_error("empty reply from server", 0)
