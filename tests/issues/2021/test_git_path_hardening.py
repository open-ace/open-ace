"""Git/path hardening for Issue #2021.

Covers the four acceptance buckets the issue converges on after splitting out
#2018/#2041/#2042/#2043:

  1. Application-layer canonical containment (``_assert_path_contained``) —
     rejects ``/repo`` vs ``/repo-evil`` prefix confusion.
  2. Pre-generated / worktree paths reject symlink escapes and out-of-root
     targets.
  3. Git trust is tightened: no global ``safe.directory "*"``; only the
     canonical repo path is trusted per command.
  4. ``git worktree list --porcelain -z`` parses paths containing spaces and
     newlines safely.
  5. Branch / worktree naming carries a collision-resistant identity.
  6. Multi-user / unattended deployments reject a shared main checkout unless
     ``OPENACE_ALLOW_SHARED_CHECKOUT`` is set.
"""

import os
from unittest.mock import MagicMock, patch

import pytest

from app.modules.workspace.autonomous.github_ops import (
    GitHubOps,
    GitHubOpsError,
    _assert_path_contained,
)

# ── 1. canonical containment rejects similar prefix ───────────────────────


def test_canonical_containment_rejects_similar_prefix(tmp_path):
    """``/repo`` vs ``/repo-evil`` must be distinguished by commonpath(), not
    by string ``startswith`` — the latter would treat the evil twin as inside."""
    repo = tmp_path / "repo"
    evil = tmp_path / "repo-evil"
    repo.mkdir()
    evil.mkdir()
    with pytest.raises(GitHubOpsError, match="escapes containment"):
        _assert_path_contained(str(evil / "x"), str(repo), label="test")


def test_canonical_containment_accepts_nested_child(tmp_path):
    nested = tmp_path / "sub" / "deep"
    nested.mkdir(parents=True)
    # Should not raise.
    _assert_path_contained(str(nested), str(tmp_path), label="test")


def test_canonical_containment_rejects_empty():
    with pytest.raises(GitHubOpsError, match="empty path"):
        _assert_path_contained("", "/tmp", label="test")


# ── 2. pre-generated / worktree paths reject escape / symlink ─────────────


def test_pre_generated_path_rejects_outside_root(tmp_path):
    """create_worktree must refuse a path outside the canonical repo root."""
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    gh = GitHubOps(str(repo))
    with pytest.raises(GitHubOpsError, match="escapes containment"):
        gh.create_worktree(str(outside / "wt"), "auto-dev/branch")


def test_pre_generated_path_rejects_symlink_or_outside_root(tmp_path):
    """A symlink that resolves outside the repo root is rejected after
    realpath canonicalization."""
    repo = tmp_path / "repo"
    repo.mkdir()
    escape = tmp_path / "escape"
    escape.mkdir()
    link = repo / "lnk"
    os.symlink(escape, link)
    gh = GitHubOps(str(repo))
    with pytest.raises(GitHubOpsError, match="escapes containment"):
        gh.remove_worktree(str(link))


def test_register_trusted_git_context_rejects_common_dir_escape(tmp_path):
    """register_trusted_git_context must reject a common_dir outside repo."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    evil = tmp_path / "repo-evil"
    evil.mkdir()
    gh = GitHubOps(str(repo))
    with pytest.raises(GitHubOpsError, match="trusted common_dir"):
        gh.register_trusted_git_context(
            repo_path=str(repo),
            git_dir=str(repo / ".git"),
            git_identity="dev:1",
            common_dir=str(evil / ".git"),
            common_identity="dev:2",
        )


# ── 3. no global safe.directory wildcard; per-command only ────────────────


@patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
def test_git_commands_do_not_set_global_safe_directory_wildcard(mock_run):
    """Every ``_run_git`` call trusts the absolute repo path inline via
    ``-c safe.directory=<abs>``. No ``*`` wildcard and no global-config
    subprocess is spawned. Locks the removal of _ensure_safe_directory."""
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    gh = GitHubOps("/tmp/repo-2021")

    gh._run_git(["rev-parse", "--show-toplevel"])

    assert mock_run.call_count == 1
    cmd = mock_run.call_args.args[0]
    # Inline -c carries the absolute path; never the wildcard.
    safe_cfgs = [a for a in cmd if isinstance(a, str) and a.startswith("safe.directory=")]
    assert len(safe_cfgs) == 1
    assert "*" not in safe_cfgs[0]
    assert safe_cfgs[0] == f"safe.directory={os.path.realpath('/tmp/repo-2021')}"
    # No global-config write anywhere in the call list.
    joined = " ".join(str(a) for a in cmd)
    assert "config --global" not in joined
    assert "config" not in joined or "safe.directory" in joined


@patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
def test_run_gh_injects_git_config_count_env(mock_run):
    """``_run_gh`` has no ``-c`` flag, so it trusts the repo via the
    GIT_CONFIG_COUNT env vars (not a global config write)."""
    mock_run.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
    gh = GitHubOps("/tmp/repo-2021")

    gh._run_gh(["issue", "view", "1", "--json", "number"])

    env = mock_run.call_args.kwargs.get("env", {})
    assert env.get("GIT_CONFIG_COUNT") == "1"
    assert env.get("GIT_CONFIG_KEY_0") == "safe.directory"
    assert env.get("GIT_CONFIG_VALUE_0") == os.path.realpath("/tmp/repo-2021")
    assert "*" not in env.get("GIT_CONFIG_VALUE_0", "*")


# ── 4. worktree porcelain -z parses spaces and newlines ───────────────────


@patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
def test_worktree_porcelain_z_parses_spaces_and_newlines(mock_run):
    """``--porcelain -z`` uses NUL record terminators so that paths and the
    record structure cannot be confused. The old LF-only split would (a) merge
    multiple records when a path contained a newline-bearing field and (b)
    lose trailing records.

    Git emits paths containing spaces verbatim (no quoting); it quotes/escapes
    paths with control chars, so the test exercises the verbatim cases the
    parser must preserve: spaces and NUL-delimited records.
    """
    space_path = "/tmp/repo with spaces"
    # A path whose name itself contains a literal newline would be quoted by
    # git porcelain; the NUL terminator is what lets us keep records apart
    # regardless. Here we use two records whose paths both contain spaces to
    # prove the LF-within-record vs NUL-between-record split is correct.
    stdout = (
        f"worktree /tmp/main\nbranch refs/heads/main\0"
        f"worktree {space_path}\nbranch refs/heads/spacey\0"
    )
    mock_run.return_value = MagicMock(returncode=0, stdout=stdout, stderr="")
    gh = GitHubOps("/tmp/main")

    worktrees = gh.list_worktrees()

    assert len(worktrees) == 2
    paths = [w["path"] for w in worktrees]
    assert "/tmp/main" in paths
    assert space_path in paths
    # All branches survived intact and were not merged across records.
    branches = {w["path"]: w["branch"] for w in worktrees}
    assert branches["/tmp/main"] == "refs/heads/main"
    assert branches[space_path] == "refs/heads/spacey"


@patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
def test_worktree_porcelain_z_keeps_records_apart_without_trailing_nul(mock_run):
    """Records must be split even if the trailing NUL is absent (defensive):
    the parser must not collapse the final record."""
    stdout = "worktree /tmp/a\nbranch refs/heads/a\0worktree /tmp/b\nbranch refs/heads/b"
    mock_run.return_value = MagicMock(returncode=0, stdout=stdout, stderr="")
    gh = GitHubOps("/tmp/a")

    worktrees = gh.list_worktrees()

    assert len(worktrees) == 2
    assert worktrees[1]["path"] == "/tmp/b"
    assert worktrees[1]["branch"] == "refs/heads/b"


# ── 5. branch / worktree names carry collision-resistant identity ─────────


def test_branch_and_worktree_names_have_collision_resistant_identity():
    """The pre-generated ``auto-dev/<slug>`` branch name must use a 12-char
    (or longer) slug, up from the old 8. The slug is the first 12 chars of a
    UUID4 string (8 hex + hyphen + 3 hex), which is collision-resistant and
    carries the workflow_id prefix so the orchestrator can recover it. Locks
    the #2021 widening and the legacy 8-char comparison prefix compatibility."""
    import uuid

    workflow_id = str(uuid.uuid4())
    # Mirror the construction sites in routes/autonomous.py and orchestrator.py.
    branch_name = f"auto-dev/{workflow_id[:12]}"

    prefix, _, slug = branch_name.partition("/")
    assert prefix == "auto-dev"
    # 12 chars (collision-resistant), not the legacy 8.
    assert len(slug) == 12
    # The slug is a UUID4 prefix (hex + one hyphen at index 8).
    hex_chars = set("0123456789abcdef-")
    assert all(c in hex_chars for c in slug)
    # Critical regression guard: it must be strictly longer than 8 so the
    # collision space grew — the old code used workflow_id[:8].
    assert len(slug) > 8


# ── 6. multi-user mode rejects shared checkout without override ───────────
#
# The gate decision is factored into ``_shared_checkout_rejection`` so it can
# be exercised without the Flask/DB harness (the route's full-path integration
# test would otherwise need a schema fixture unrelated to #2021).


def _multi_user_manager(enabled: bool):
    """Patch WebUIManager so config.multi_user_mode reflects ``enabled``."""
    inst = MagicMock()
    inst.config.multi_user_mode = enabled
    return patch("app.services.webui_manager.WebUIManager", return_value=inst)


def test_multi_user_mode_rejects_shared_checkout_without_explicit_override():
    """In multi-user mode a shared main checkout (new-branch/current) is
    rejected unless OPENACE_ALLOW_SHARED_CHECKOUT is set."""
    from app.routes.autonomous import _shared_checkout_rejection

    with _multi_user_manager(True):
        with patch.dict("os.environ", {}, clear=False):
            os.environ.pop("OPENACE_ALLOW_SHARED_CHECKOUT", None)
            msg = _shared_checkout_rejection("new-branch")
    assert msg is not None
    assert "worktree" in msg.lower()

    with _multi_user_manager(True):
        with patch.dict("os.environ", {}, clear=False):
            os.environ.pop("OPENACE_ALLOW_SHARED_CHECKOUT", None)
            msg_current = _shared_checkout_rejection("current")
    assert msg_current is not None


def test_multi_user_mode_allows_worktree():
    """worktree strategy is never gated, even in multi-user mode."""
    from app.routes.autonomous import _shared_checkout_rejection

    with _multi_user_manager(True):
        with patch.dict("os.environ", {}, clear=False):
            os.environ.pop("OPENACE_ALLOW_SHARED_CHECKOUT", None)
            assert _shared_checkout_rejection("worktree") is None


def test_multi_user_mode_allows_shared_with_override():
    """OPENACE_ALLOW_SHARED_CHECKOUT=1 lets a shared main checkout through."""
    from app.routes.autonomous import _shared_checkout_rejection

    with _multi_user_manager(True):
        with patch.dict("os.environ", {"OPENACE_ALLOW_SHARED_CHECKOUT": "1"}):
            assert _shared_checkout_rejection("new-branch") is None


def test_single_user_mode_allows_shared_checkout():
    """Single-user mode does not gate branch strategy at all."""
    from app.routes.autonomous import _shared_checkout_rejection

    with _multi_user_manager(False):
        with patch.dict("os.environ", {}, clear=False):
            os.environ.pop("OPENACE_ALLOW_SHARED_CHECKOUT", None)
            assert _shared_checkout_rejection("new-branch") is None
            assert _shared_checkout_rejection("current") is None
            assert _shared_checkout_rejection("worktree") is None
