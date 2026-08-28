"""Workspace transfer and ChangeSet validation for the OpenSandbox backend (#2023).

The issue's pipeline, and the reason each half exists:

    control plane prepares trusted base/worktree
      -> upload credential-free snapshot        (build_snapshot)
      -> agent edits inside the sandbox
      -> supervisor produces a manifest         (parse_manifest)
      -> control plane validates it             (validate_changeset)
      -> apply to the trusted worktree          (apply_changeset)
      -> GitHubOps commit/push                  (control plane only)

Two invariants carry the security weight.

**The sandbox never sees ``.git``.** Excluding it means the agent gets a working
tree rather than a repository, so there is no path from inside the sandbox to
the trusted Git common-dir, a stored credential helper, or a push token. The
cost is that a deletion is not derivable by diffing — hence the explicit
``deleted`` list in the manifest.

**Apply is additive plus explicit deletes, never a full sync.** A full sync
would treat "absent from the manifest" as "delete", and since ``.git`` can never
appear in a manifest, that would delete the trusted repository itself.
"""

from __future__ import annotations

import fnmatch
import json
import os
import shutil
import stat
import tempfile
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from app.modules.workspace.autonomous.sandbox.provider import SandboxError

if TYPE_CHECKING:  # pragma: no cover - annotations only
    from app.modules.workspace.autonomous.sandbox.opensandbox.config import ChangesetLimits

# Directories never uploaded, at any depth. ``.git`` keeps the trusted
# common-dir unreachable; ``.ssh`` keeps private keys out.
_EXCLUDED_DIRS = frozenset({".git", ".ssh"})

# The subset whose presence in a *returned* manifest is a repository-integrity
# problem rather than a credential one. Both are rejected either way, but the
# reason codes are not interchangeable: ".git/config" means the sandbox tried to
# rewrite the trusted repo, while ".ssh/id_rsa" means it tried to plant a
# credential, and an operator reading the audit event needs to tell them apart.
_REPO_INTEGRITY_DIRS = frozenset({".git"})

# Credential-bearing paths, matched against the POSIX-relative path or its
# basename. Used both to filter the upload and to reject a returned ChangeSet —
# an agent must not be able to write a credential file into the trusted worktree
# either.
_SECRET_GLOBS: tuple[str, ...] = (
    "*.pem",
    "*.key",
    "id_rsa*",
    "id_ecdsa*",
    "id_ed25519*",
    ".env",
    ".env.*",
    ".git-credentials",
    ".netrc",
    ".npmrc",
    ".pypirc",
    "*/.ssh/*",
    "*/.aws/credentials",
    "*/.config/gh/*",
)

# Modes that must never be applied to the trusted worktree.
_UNSAFE_MODE_BITS = stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX

_DEFAULT_FILE_MODE = 0o644
_DEFAULT_DIR_MODE = 0o755


@dataclass(frozen=True)
class SnapshotEntry:
    """One file to upload into a fresh sandbox."""

    path: str  # worktree-relative, POSIX separators
    data: bytes
    mode: int = _DEFAULT_FILE_MODE


@dataclass(frozen=True)
class ChangeSetEntry:
    """One file the supervisor reports as present after the agent's edits."""

    path: str
    mode: int
    size: int
    sha256: str = ""
    symlink_target: str = ""


@dataclass(frozen=True)
class ChangeSetRejection:
    """Why one manifest entry was refused.

    ``reason`` is a stable machine-readable code so the audit event and the
    operator guide's reason catalogue agree.
    """

    path: str
    reason: str
    detail: str = ""


@dataclass
class _Budget:
    """Running totals for the whole-manifest limits."""

    files: int = 0
    total_bytes: int = 0
    rejections: list[ChangeSetRejection] = field(default_factory=list)


def build_snapshot(worktree_path: str) -> Iterator[SnapshotEntry]:
    """Yield the credential-free snapshot of *worktree_path*.

    Skips ``.git``/``.ssh`` at any depth, credential-bearing names, and any
    symlink whose target resolves outside the worktree. Only regular files are
    yielded, all at :data:`_DEFAULT_FILE_MODE` — the source mode is deliberately
    not preserved, because a mode from the trusted worktree carries no meaning
    inside a container running as a different user.
    """
    root = Path(worktree_path).resolve()
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune in place so os.walk does not descend into excluded trees.
        dirnames[:] = [d for d in dirnames if d not in _EXCLUDED_DIRS]
        for filename in filenames:
            absolute = Path(dirpath) / filename
            relative = absolute.relative_to(root).as_posix()
            if _is_secret_path(relative):
                continue
            if absolute.is_symlink() and not _resolves_inside(absolute, root):
                continue
            if not absolute.is_file():
                continue
            try:
                data = absolute.read_bytes()
            except OSError:
                continue
            yield SnapshotEntry(path=relative, data=data, mode=_DEFAULT_FILE_MODE)


def snapshot_upload_mode(is_directory: bool = False) -> int:
    """The mode the provider uploads with.

    execd may run as root, so a root-owned file under a restrictive mode would
    leave the agent unable to edit its own workspace. Group/other read (and
    directory traverse) keep the tree usable whatever uid the container runs as.
    """
    return _DEFAULT_DIR_MODE if is_directory else _DEFAULT_FILE_MODE


def parse_manifest(payload: bytes | str) -> tuple[list[ChangeSetEntry], list[str]]:
    """Parse the supervisor's manifest into ``(entries, deleted)``.

    Shape: ``{"files": [{path, mode, size, sha256, symlink_target}],
    "deleted": [path]}``. The explicit ``deleted`` list exists because the
    sandbox has no ``.git`` baseline to diff against, so a removal is otherwise
    invisible to the control plane.
    """
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8", errors="replace")
    try:
        raw = json.loads(payload)
    except ValueError as exc:
        raise SandboxError(f"malformed ChangeSet manifest: {exc}") from exc
    if not isinstance(raw, dict):
        raise SandboxError("ChangeSet manifest must be a JSON object")

    entries: list[ChangeSetEntry] = []
    for item in raw.get("files") or []:
        if not isinstance(item, dict):
            raise SandboxError("ChangeSet manifest 'files' entries must be objects")
        entries.append(
            ChangeSetEntry(
                path=str(item.get("path") or ""),
                mode=int(item.get("mode") or _DEFAULT_FILE_MODE),
                size=int(item.get("size") or 0),
                sha256=str(item.get("sha256") or ""),
                symlink_target=str(item.get("symlink_target") or ""),
            )
        )
    deleted = [str(p) for p in (raw.get("deleted") or [])]
    return entries, deleted


def validate_changeset(
    entries: Sequence[ChangeSetEntry],
    *,
    root: str,
    limits: ChangesetLimits,
    deleted: Sequence[str] = (),
) -> list[ChangeSetRejection]:
    """Return **every** reason this ChangeSet may not be applied.

    All rejections are collected rather than stopping at the first, so the audit
    event lists everything wrong with one manifest instead of revealing problems
    one redeploy at a time.

    Runs entirely control-plane side and touches nothing: a caller must treat a
    non-empty result as "apply nothing".
    """
    resolved_root = Path(root).resolve()
    budget = _Budget()

    if len(entries) > limits.max_files:
        budget.rejections.append(
            ChangeSetRejection(
                path="",
                reason="too_many_files",
                detail=f"{len(entries)} entries exceeds max_files={limits.max_files}",
            )
        )

    for entry in entries:
        _validate_entry(entry, resolved_root, limits, budget)

    if budget.total_bytes > limits.max_total_bytes:
        budget.rejections.append(
            ChangeSetRejection(
                path="",
                reason="total_too_large",
                detail=f"{budget.total_bytes} bytes exceeds max_total_bytes={limits.max_total_bytes}",
            )
        )

    for path in deleted:
        reason = _path_rejection(path, resolved_root)
        if reason is not None:
            budget.rejections.append(ChangeSetRejection(path=path, reason=reason))

    return budget.rejections


def apply_changeset(
    entries: Sequence[ChangeSetEntry],
    *,
    root: str,
    limits: ChangesetLimits,
    fetch: Callable[[str], bytes],
    deleted: Sequence[str] = (),
) -> None:
    """Apply a ChangeSet to the trusted worktree, or apply nothing.

    Validates the whole manifest and raises **before** the first write, then
    stages every file in a temporary directory and moves them into place, so a
    mid-way I/O failure cannot leave a half-applied tree.

    Additive plus explicit deletes: a path absent from the manifest is left
    untouched. See the module docstring for why a full sync would be
    catastrophic.
    """
    rejections = validate_changeset(entries, root=root, limits=limits, deleted=deleted)
    if rejections:
        summary = ", ".join(f"{r.path or '<manifest>'}:{r.reason}" for r in rejections[:10])
        raise SandboxError(
            f"ChangeSet rejected ({len(rejections)} problem(s)): {summary}"
            + ("…" if len(rejections) > 10 else "")
        )

    resolved_root = Path(root).resolve()
    staging = Path(tempfile.mkdtemp(prefix=".openace-changeset-", dir=str(resolved_root)))
    try:
        staged: list[tuple[Path, Path, int]] = []
        for entry in entries:
            staged_path = staging / entry.path
            staged_path.parent.mkdir(parents=True, exist_ok=True)
            staged_path.write_bytes(fetch(entry.path))
            staged.append((staged_path, resolved_root / entry.path, entry.mode & 0o777))

        for staged_path, destination, mode in staged:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(staged_path), str(destination))
            os.chmod(destination, mode)

        for path in deleted:
            target = resolved_root / path
            try:
                target.unlink()
            except FileNotFoundError:
                # Deleting an already-absent path is the desired end state, not
                # an error — a retried apply must stay idempotent.
                continue
            except IsADirectoryError:
                shutil.rmtree(target, ignore_errors=True)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


# ── helpers ───────────────────────────────────────────────────────────


def _validate_entry(
    entry: ChangeSetEntry, root: Path, limits: ChangesetLimits, budget: _Budget
) -> None:
    reason = _path_rejection(entry.path, root)
    if reason is not None:
        budget.rejections.append(ChangeSetRejection(path=entry.path, reason=reason))
        return

    if stat.S_ISLNK(entry.mode) or entry.symlink_target:
        if not _symlink_target_inside(entry.path, entry.symlink_target, root):
            budget.rejections.append(
                ChangeSetRejection(
                    path=entry.path,
                    reason="symlink_escape",
                    detail=f"target {entry.symlink_target!r} resolves outside the worktree",
                )
            )
        return

    if entry.mode & _UNSAFE_MODE_BITS or not _is_regular_mode(entry.mode):
        budget.rejections.append(
            ChangeSetRejection(
                path=entry.path, reason="unsafe_mode", detail=f"mode={oct(entry.mode)}"
            )
        )
        return

    if entry.size > limits.max_file_bytes:
        budget.rejections.append(
            ChangeSetRejection(
                path=entry.path,
                reason="file_too_large",
                detail=f"{entry.size} bytes exceeds max_file_bytes={limits.max_file_bytes}",
            )
        )
        return

    budget.files += 1
    budget.total_bytes += max(entry.size, 0)


def _path_rejection(path: str, root: Path) -> str | None:
    """Return a rejection reason for *path*, or ``None`` when it is acceptable."""
    if not path:
        return "path_escape"
    if os.path.isabs(path) or (len(path) > 1 and path[1] == ":"):
        return "absolute_path"
    parts = Path(path).parts
    if any(part == ".." for part in parts):
        return "path_escape"
    # ``.git`` inside a manifest would let the sandbox rewrite the trusted
    # repository's refs, config or hooks — the exact thing excluding it from the
    # snapshot was meant to prevent.
    if any(part in _REPO_INTEGRITY_DIRS for part in parts):
        return "path_escape"
    try:
        resolved = (root / path).resolve()
    except OSError:
        return "path_escape"
    if resolved != root and root not in resolved.parents:
        return "path_escape"
    if _is_secret_path(path):
        return "secret_path"
    return None


def _symlink_target_inside(path: str, target: str, root: Path) -> bool:
    if not target:
        return False
    if os.path.isabs(target):
        return False
    try:
        resolved = (root / Path(path).parent / target).resolve()
    except OSError:
        return False
    return resolved == root or root in resolved.parents


def _is_regular_mode(mode: int) -> bool:
    """True when *mode* denotes a regular file (or carries no type bits at all).

    A manifest may report a bare permission mode (``0o644``) or a full
    ``st_mode``; both are accepted, anything else (fifo, socket, device) is not.
    """
    file_type = stat.S_IFMT(mode)
    return file_type in (0, stat.S_IFREG)


def _is_secret_path(relative_path: str) -> bool:
    name = os.path.basename(relative_path)
    probe = f"/{relative_path}"
    return any(
        fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(probe, pattern)
        for pattern in _SECRET_GLOBS
    )


def _resolves_inside(path: Path, root: Path) -> bool:
    try:
        resolved = path.resolve()
    except OSError:
        return False
    return resolved == root or root in resolved.parents
