"""Workspace snapshot + ChangeSet validation for the OpenSandbox backend (#2023).

The control plane never exposes the trusted Git common-dir to a sandbox: it
uploads a credential-free snapshot, and validates the manifest the supervisor
returns before a single byte lands in the trusted worktree.
"""

from __future__ import annotations

import json
import os

import pytest

from app.modules.workspace.autonomous.sandbox.opensandbox.config import ChangesetLimits
from app.modules.workspace.autonomous.sandbox.opensandbox.workspace import (
    ChangeSetEntry,
    apply_changeset,
    build_snapshot,
    derive_deletions,
    parse_manifest,
    validate_changeset,
)
from app.modules.workspace.autonomous.sandbox.provider import SandboxError

pytestmark = [pytest.mark.regression, pytest.mark.issue(2023)]


def _limits(**kw) -> ChangesetLimits:
    base = {"max_files": 10, "max_file_bytes": 100, "max_total_bytes": 1000}
    base.update(kw)
    return ChangesetLimits(**base)


def _worktree(tmp_path):
    """A worktree carrying exactly one legitimate file plus assorted secrets."""
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("[remote]", encoding="utf-8")
    (tmp_path / ".git" / "objects").mkdir()
    (tmp_path / ".git" / "objects" / "abc").write_text("obj", encoding="utf-8")
    (tmp_path / ".git-credentials").write_text("https://x:y@github.com", encoding="utf-8")
    (tmp_path / ".netrc").write_text("machine github.com", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET=1", encoding="utf-8")
    (tmp_path / ".env.local").write_text("SECRET=2", encoding="utf-8")
    (tmp_path / "deploy.pem").write_text("-----BEGIN", encoding="utf-8")
    (tmp_path / ".ssh").mkdir()
    (tmp_path / ".ssh" / "id_rsa").write_text("key", encoding="utf-8")
    nested = tmp_path / "vendor" / ".git"
    nested.mkdir(parents=True)
    (nested / "config").write_text("[remote]", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print(1)", encoding="utf-8")
    return tmp_path


# ── snapshot ──────────────────────────────────────────────────────────


def test_snapshot_excludes_git_credentials_ssh_and_env(tmp_path):
    # The .git exclusion is what makes the trusted common-dir unreachable from
    # the sandbox; the credential exclusions are what keep a push token out.
    root = _worktree(tmp_path)
    assert {entry.path for entry in build_snapshot(str(root))} == {"src/main.py"}


def test_snapshot_excludes_git_at_any_depth(tmp_path):
    root = _worktree(tmp_path)
    paths = {entry.path for entry in build_snapshot(str(root))}
    assert not any(part == ".git" for path in paths for part in path.split("/"))


def test_snapshot_refuses_symlink_pointing_outside_worktree(tmp_path):
    root = tmp_path / "tree"
    root.mkdir()
    (root / "ok.py").write_text("x", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    os.symlink(outside, root / "escape.txt")
    assert {entry.path for entry in build_snapshot(str(root))} == {"ok.py"}


def test_snapshot_uses_0644_for_files(tmp_path):
    root = tmp_path / "tree"
    root.mkdir()
    (root / "a.py").write_text("x", encoding="utf-8")
    (root / "a.py").chmod(0o600)
    entry = next(iter(build_snapshot(str(root))))
    assert entry.mode == 0o644


def test_snapshot_carries_file_bytes(tmp_path):
    root = tmp_path / "tree"
    root.mkdir()
    (root / "a.py").write_text("hello", encoding="utf-8")
    assert next(iter(build_snapshot(str(root)))).data == b"hello"


# ── manifest parsing ──────────────────────────────────────────────────


def test_parse_manifest_reads_files_and_deleted():
    payload = json.dumps(
        {
            "files": [{"path": "a.py", "mode": 0o644, "size": 3, "sha256": "d" * 64}],
            "deleted": ["b.py"],
        }
    )
    entries, deleted = parse_manifest(payload)
    assert [e.path for e in entries] == ["a.py"]
    assert deleted == ["b.py"]


def test_parse_manifest_rejects_non_json():
    with pytest.raises(SandboxError):
        parse_manifest("{not json")


# ── ChangeSet validation ──────────────────────────────────────────────


def test_changeset_rejects_absolute_path_symlink_escape_and_oversize_file(tmp_path):
    entries = [
        ChangeSetEntry(path="/etc/passwd", mode=0o644, size=1),
        ChangeSetEntry(path="../../outside.txt", mode=0o644, size=1),
        ChangeSetEntry(path="link", mode=0o120000, size=1, symlink_target="/etc/shadow"),
        ChangeSetEntry(path="big.bin", mode=0o644, size=999),
        ChangeSetEntry(path="setuid.sh", mode=0o104755, size=1),
        ChangeSetEntry(path="ok.py", mode=0o644, size=1),
    ]
    reasons = {
        r.path: r.reason for r in validate_changeset(entries, root=str(tmp_path), limits=_limits())
    }
    assert reasons["/etc/passwd"] == "absolute_path"
    assert reasons["../../outside.txt"] == "path_escape"
    assert reasons["link"] == "symlink_escape"
    assert reasons["big.bin"] == "file_too_large"
    assert reasons["setuid.sh"] == "unsafe_mode"
    assert "ok.py" not in reasons


def test_validate_returns_all_rejections_not_first_fail(tmp_path):
    # The audit event must list everything wrong, not just the first problem.
    entries = [
        ChangeSetEntry(path="/a", mode=0o644, size=1),
        ChangeSetEntry(path="/b", mode=0o644, size=1),
        ChangeSetEntry(path="/c", mode=0o644, size=1),
    ]
    assert len(validate_changeset(entries, root=str(tmp_path), limits=_limits())) == 3


def test_changeset_rejects_over_file_count(tmp_path):
    entries = [ChangeSetEntry(path=f"f{i}.py", mode=0o644, size=1) for i in range(11)]
    reasons = {r.reason for r in validate_changeset(entries, root=str(tmp_path), limits=_limits())}
    assert "too_many_files" in reasons


def test_changeset_rejects_over_total_size(tmp_path):
    # 11 x 99 = 1089 > max_total_bytes 1000, while each file stays under
    # max_file_bytes and the count stays under the raised max_files.
    entries = [ChangeSetEntry(path=f"f{i}.py", mode=0o644, size=99) for i in range(11)]
    reasons = {
        r.reason
        for r in validate_changeset(entries, root=str(tmp_path), limits=_limits(max_files=20))
    }
    assert "total_too_large" in reasons


@pytest.mark.parametrize(
    "path",
    [
        "deploy.pem",
        "server.key",
        "id_rsa",
        ".env",
        ".env.production",
        ".git-credentials",
        ".netrc",
        ".npmrc",
        ".pypirc",
        "home/.ssh/id_ed25519",
        "home/.aws/credentials",
    ],
)
def test_changeset_rejects_secret_bearing_paths(tmp_path, path):
    entries = [ChangeSetEntry(path=path, mode=0o644, size=1)]
    reasons = {r.reason for r in validate_changeset(entries, root=str(tmp_path), limits=_limits())}
    assert reasons == {"secret_path"}


def test_changeset_rejects_dot_git_paths(tmp_path):
    # A manifest entry under .git would let the sandbox rewrite the trusted
    # repository's refs or config.
    # A distinct code from a `..` traversal: ".git/config" means the sandbox
    # tried to rewrite the trusted repo, ".ssh/id_rsa" means it tried to plant a
    # credential, and an operator reading the audit event must tell them apart.
    entries = [ChangeSetEntry(path=".git/config", mode=0o644, size=1)]
    reasons = {r.reason for r in validate_changeset(entries, root=str(tmp_path), limits=_limits())}
    assert reasons == {"repo_integrity"}


def test_deleted_entries_get_the_same_path_checks(tmp_path):
    reasons = {
        r.path: r.reason
        for r in validate_changeset(
            [], root=str(tmp_path), limits=_limits(), deleted=["/etc/passwd", "../out", ".git/HEAD"]
        )
    }
    assert reasons["/etc/passwd"] == "absolute_path"
    assert reasons["../out"] == "path_escape"
    assert reasons[".git/HEAD"] == "repo_integrity"


def test_in_tree_symlink_is_allowed(tmp_path):
    entries = [ChangeSetEntry(path="link", mode=0o120000, size=0, symlink_target="src/main.py")]
    assert validate_changeset(entries, root=str(tmp_path), limits=_limits()) == []


# ── apply ─────────────────────────────────────────────────────────────


def test_apply_writes_nothing_when_any_entry_is_rejected(tmp_path):
    (tmp_path / "existing.py").write_text("old", encoding="utf-8")
    entries = [
        ChangeSetEntry(path="good.py", mode=0o644, size=3),
        ChangeSetEntry(path="/etc/passwd", mode=0o644, size=1),
    ]
    with pytest.raises(SandboxError):
        apply_changeset(entries, root=str(tmp_path), limits=_limits(), fetch=lambda path: b"new")
    assert not (tmp_path / "good.py").exists()
    assert (tmp_path / "existing.py").read_text(encoding="utf-8") == "old"


def test_apply_is_additive_plus_explicit_deletes_never_a_full_sync(tmp_path):
    # A full sync would delete the trusted repo's .git, because .git can never
    # appear in a manifest. Anything absent from the manifest is left alone.
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref", encoding="utf-8")
    (tmp_path / "untouched.py").write_text("keep", encoding="utf-8")
    (tmp_path / "gone.py").write_text("bye", encoding="utf-8")
    apply_changeset(
        [ChangeSetEntry(path="new.py", mode=0o644, size=3)],
        root=str(tmp_path),
        limits=_limits(),
        fetch=lambda path: b"new",
        deleted=["gone.py"],
    )
    assert (tmp_path / ".git" / "HEAD").exists()
    assert (tmp_path / "untouched.py").read_text(encoding="utf-8") == "keep"
    assert (tmp_path / "new.py").read_text(encoding="utf-8") == "new"
    assert not (tmp_path / "gone.py").exists()


def test_apply_creates_parent_directories(tmp_path):
    apply_changeset(
        [ChangeSetEntry(path="a/b/c.py", mode=0o644, size=1)],
        root=str(tmp_path),
        limits=_limits(),
        fetch=lambda path: b"x",
    )
    assert (tmp_path / "a" / "b" / "c.py").read_text(encoding="utf-8") == "x"


def test_apply_sets_the_declared_mode(tmp_path):
    apply_changeset(
        [ChangeSetEntry(path="run.sh", mode=0o755, size=1)],
        root=str(tmp_path),
        limits=_limits(),
        fetch=lambda path: b"x",
    )
    assert (tmp_path / "run.sh").stat().st_mode & 0o777 == 0o755


def test_apply_deleting_a_missing_path_is_not_an_error(tmp_path):
    apply_changeset(
        [], root=str(tmp_path), limits=_limits(), fetch=lambda p: b"", deleted=["nope.py"]
    )


# ── apply verifies delivered bytes, not the manifest's self-report (B6) ──


def test_apply_rejects_content_larger_than_the_declared_size(tmp_path):
    # Validation bounds the manifest's SELF-REPORTED size. Without re-checking
    # the delivered bytes, a supervisor that declares 10 and returns 10 GB
    # defeats both limits — and the party being bounded is the reporter.
    with pytest.raises(SandboxError, match="declared"):
        apply_changeset(
            [ChangeSetEntry(path="a.py", mode=0o644, size=3)],
            root=str(tmp_path),
            limits=_limits(),
            fetch=lambda path: b"x" * 5000,
        )
    assert not (tmp_path / "a.py").exists()


def test_apply_rejects_a_sha256_mismatch(tmp_path):
    with pytest.raises(SandboxError, match="sha256"):
        apply_changeset(
            [ChangeSetEntry(path="a.py", mode=0o644, size=1, sha256="d" * 64)],
            root=str(tmp_path),
            limits=_limits(),
            fetch=lambda path: b"x",
        )


def test_apply_accepts_a_matching_sha256(tmp_path):
    import hashlib

    digest = hashlib.sha256(b"x").hexdigest()
    apply_changeset(
        [ChangeSetEntry(path="a.py", mode=0o644, size=1, sha256=digest)],
        root=str(tmp_path),
        limits=_limits(),
        fetch=lambda path: b"x",
    )
    assert (tmp_path / "a.py").read_bytes() == b"x"


# ── symlinks are applied as symlinks (B16) ────────────────────────────


def test_apply_creates_an_in_tree_symlink_as_a_symlink(tmp_path):
    # validate_changeset has a dedicated symlink branch; applying the entry as a
    # regular file would leave the worktree structurally different from what was
    # validated and silently corrupt a repo that legitimately uses symlinks.
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("x", encoding="utf-8")
    apply_changeset(
        [ChangeSetEntry(path="link.py", mode=0o120000, size=0, symlink_target="src/main.py")],
        root=str(tmp_path),
        limits=_limits(),
        fetch=lambda path: pytest.fail("a symlink entry must not be fetched"),
    )
    assert (tmp_path / "link.py").is_symlink()
    assert os.readlink(tmp_path / "link.py") == "src/main.py"


# ── staging (B23) ─────────────────────────────────────────────────────


def test_staging_directory_is_not_left_inside_the_worktree(tmp_path):
    root = tmp_path / "tree"
    root.mkdir()
    apply_changeset(
        [ChangeSetEntry(path="a.py", mode=0o644, size=1)],
        root=str(root),
        limits=_limits(),
        fetch=lambda path: b"x",
    )
    assert not any(p.name.startswith(".openace-changeset-") for p in root.iterdir())


def test_envrc_is_treated_as_a_secret(tmp_path):
    reasons = {
        r.reason
        for r in validate_changeset(
            [ChangeSetEntry(path=".envrc", mode=0o644, size=1)],
            root=str(tmp_path),
            limits=_limits(),
        )
    }
    assert reasons == {"secret_path"}


# ── deletions are derived control-plane side (N5) ─────────────────────


def test_derive_deletions_finds_files_the_sandbox_no_longer_has(tmp_path):
    (tmp_path / "kept.py").write_text("x", encoding="utf-8")
    (tmp_path / "gone.py").write_text("x", encoding="utf-8")
    entries = [ChangeSetEntry(path="kept.py", mode=0o644, size=1)]
    assert derive_deletions(entries, worktree_path=str(tmp_path)) == ["gone.py"]


def test_derive_deletions_never_reports_git_or_secrets_as_deleted(tmp_path):
    # These are absent from the manifest because build_snapshot never uploaded
    # them. Treating that as a deletion would delete the trusted repository.
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET=1", encoding="utf-8")
    (tmp_path / "deploy.pem").write_text("key", encoding="utf-8")
    (tmp_path / "kept.py").write_text("x", encoding="utf-8")
    entries = [ChangeSetEntry(path="kept.py", mode=0o644, size=1)]
    assert derive_deletions(entries, worktree_path=str(tmp_path)) == []


def test_derive_deletions_handles_nested_paths(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "old.py").write_text("x", encoding="utf-8")
    assert derive_deletions([], worktree_path=str(tmp_path)) == ["src/old.py"]


def test_an_unreadable_file_is_never_proposed_for_deletion(tmp_path):
    # It is absent from the manifest because build_snapshot could not upload it.
    # Proposing it as a deletion would remove a file from the user's trusted
    # worktree that the agent never saw.
    unreadable = tmp_path / "locked.py"
    unreadable.write_text("x", encoding="utf-8")
    unreadable.chmod(0o000)
    try:
        assert derive_deletions([], worktree_path=str(tmp_path)) == []
        assert {e.path for e in build_snapshot(str(tmp_path))} == set()
    finally:
        unreadable.chmod(0o644)


def test_an_out_of_tree_symlink_is_never_proposed_for_deletion(tmp_path):
    root = tmp_path / "tree"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    os.symlink(outside, root / "escape.txt")
    (root / "kept.py").write_text("x", encoding="utf-8")
    entries = [ChangeSetEntry(path="kept.py", mode=0o644, size=1)]
    assert derive_deletions(entries, worktree_path=str(root)) == []


def test_deletion_and_snapshot_agree_on_what_is_uploadable(tmp_path):
    # The symmetry is structural: anything build_snapshot yields is a candidate
    # for deletion, and nothing else ever is.
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref", encoding="utf-8")
    (tmp_path / ".env").write_text("S=1", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x", encoding="utf-8")
    uploaded = {e.path for e in build_snapshot(str(tmp_path))}
    assert set(derive_deletions([], worktree_path=str(tmp_path))) == uploaded


def test_directories_emptied_by_a_deletion_are_pruned(tmp_path):
    (tmp_path / "src" / "legacy").mkdir(parents=True)
    (tmp_path / "src" / "legacy" / "old.py").write_text("x", encoding="utf-8")
    (tmp_path / "src" / "keep.py").write_text("x", encoding="utf-8")
    apply_changeset(
        [],
        root=str(tmp_path),
        limits=_limits(),
        fetch=lambda p: b"",
        deleted=["src/legacy/old.py"],
    )
    assert not (tmp_path / "src" / "legacy").exists()
    assert (tmp_path / "src" / "keep.py").exists()


def test_a_linked_worktrees_gitfile_is_never_uploaded(tmp_path):
    """A real `git worktree` checkout — .git is a FILE, not a directory.

    Every autonomous run is checked out this way. Excluding `.git` only from
    os.walk's dirnames uploaded the gitfile verbatim: it leaks the control
    plane's absolute filesystem layout into the sandbox, and the `git init`
    synthesis that follows then finds a .git pointing at a host path that does
    not exist there.
    """
    common_dir = tmp_path / "bare" / ".git" / "worktrees" / "wt"
    common_dir.mkdir(parents=True)
    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / ".git").write_text(f"gitdir: {common_dir}\n", encoding="utf-8")
    (worktree / "app.py").write_text("x = 1", encoding="utf-8")

    uploaded = {e.path for e in build_snapshot(str(worktree))}
    assert uploaded == {"app.py"}
    assert not any(str(common_dir) in e.data.decode() for e in build_snapshot(str(worktree)))


def test_an_ssh_file_is_excluded_the_same_way_a_dot_ssh_dir_is(tmp_path):
    (tmp_path / ".ssh").write_text("not-a-dir", encoding="utf-8")
    (tmp_path / "app.py").write_text("x", encoding="utf-8")
    assert {e.path for e in build_snapshot(str(tmp_path))} == {"app.py"}


def test_deletions_never_propose_removing_the_gitfile(tmp_path):
    """The symmetry that matters: what is not uploaded is never deleted.

    A .git file the snapshot skips is necessarily absent from the sandbox's
    manifest. If derive_deletions did not skip it too, the first apply would
    delete the linked worktree's own gitdir pointer and detach the checkout
    from its repository.
    """
    (tmp_path / ".git").write_text("gitdir: /host/path\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("x", encoding="utf-8")
    assert derive_deletions([], worktree_path=str(tmp_path)) == ["app.py"]
