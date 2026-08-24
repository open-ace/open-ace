"""Issue #2335: the same-user verifier directory allocator, for real (#2429).

Split out of the legacy ``tests/issues/2335/test_verifier_checkout.py`` when the
mocked items moved to ``tests/unit/``: the same-user allocation path runs real
``mkdir -p`` / ``mkdir -m 700`` subprocesses and asserts the resulting on-disk
permission bits, so it crosses the subprocess + filesystem boundary
(docs/TEST_LAYERS.md) and belongs in the integration layer.
"""

from __future__ import annotations

import os
import stat

import pytest

from app.modules.workspace.autonomous.github_ops import GitHubOps

pytestmark = [
    pytest.mark.regression,
    pytest.mark.issue(2335),
]


def test_same_user_verifier_directory_is_private_and_owner_writable(tmp_path):
    """The real allocator creates a traversable/writable 0700 dir for its owner."""
    gh = GitHubOps(str(tmp_path))
    path = gh.create_verification_worktree_dir(str(tmp_path))
    try:
        mode = stat.S_IMODE(os.stat(path).st_mode)
        assert mode == 0o700
        assert os.access(path, os.X_OK | os.W_OK)
    finally:
        gh.remove_verification_worktree_dir(path, str(tmp_path))
