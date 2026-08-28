"""ChangeSet manifest producer, executed INSIDE the sandbox (Issue #2023).

This file is never imported by the control plane. It is read as text, uploaded
into the sandbox, and run there by ``OpenSandboxProvider.collect_changes`` —
which is why it has no imports outside the standard library and no dependency on
anything in this package. Keeping it a real module rather than an embedded
string means black, ruff and mypy check it like any other code.

No shebang: it is never executed from this repository. The provider uploads it
into the sandbox and runs it as ``python3 <path>``.

It writes to ``/tmp``, not ``/workspace``, so neither the script nor its output
can appear in the manifest it generates. ``.git`` is excluded at any depth: the
provider synthesises a real repository in the workspace, and a manifest that
walked it naively would carry hundreds of ``.git/objects`` entries — every one
rejected as ``repo_integrity``, and since apply is all-or-nothing, no ChangeSet
would ever be applicable.
"""

from __future__ import annotations

import hashlib
import json
import os

ROOT = "/workspace"
OUTPUT = "/tmp/openace-manifest.json"  # noqa: S108 - inside an ephemeral sandbox
EXCLUDED_DIRS = frozenset({".git", ".ssh"})
SYMLINK_MODE = 0o120000


def build() -> dict:
    """Walk the workspace and describe every regular file and in-tree symlink."""
    entries: list[dict] = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [name for name in dirnames if name not in EXCLUDED_DIRS]
        for name in filenames:
            absolute = os.path.join(dirpath, name)
            relative = os.path.relpath(absolute, ROOT)
            try:
                if os.path.islink(absolute):
                    entries.append(
                        {
                            "path": relative,
                            "mode": SYMLINK_MODE,
                            "size": 0,
                            "symlink_target": os.readlink(absolute),
                        }
                    )
                    continue
                with open(absolute, "rb") as handle:
                    data = handle.read()
                mode = os.stat(absolute).st_mode & 0o777
            except OSError:
                # Unreadable or vanished mid-walk: omit it rather than emitting
                # an entry the control plane would reject the whole ChangeSet for.
                continue
            entries.append(
                {
                    "path": relative,
                    "mode": mode,
                    "size": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            )
    # `deleted` is always empty from this producer: it reports what IS present.
    # The control plane derives removals by comparing against the trusted
    # worktree, because the sandbox has no baseline to diff against.
    return {"files": entries, "deleted": []}


if __name__ == "__main__":
    with open(OUTPUT, "w", encoding="utf-8") as out:
        json.dump(build(), out)
