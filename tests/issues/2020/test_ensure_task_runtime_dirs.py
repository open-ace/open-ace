"""``ensure_task_runtime_dirs`` creation guard (Issue #2020 Phase A).

On the same-user launch path (dev/local; the launcher is not invoked) the
Python runner is authoritative for the agent's HOME/TMP, so it must create the
per-task tree itself. On the cross-user path the root launcher creates it; the
runner cannot (root-owned /run). ``ensure_task_runtime_dirs`` therefore creates
the tree when the root is writable and returns None when it is not, so the
env builder can fall back to the legacy shared HOME instead of pointing the
agent at a non-existent directory.
"""

from __future__ import annotations

import os
from pathlib import Path

from app.modules.workspace.autonomous.task_isolation import ensure_task_runtime_dirs


def test_creates_tree_under_writable_root(tmp_path):
    dirs = ensure_task_runtime_dirs("abc-123", str(tmp_path))
    assert dirs is not None
    assert (tmp_path / "abc-123" / "home").is_dir()
    assert (tmp_path / "abc-123" / "tmp").is_dir()
    assert (tmp_path / "abc-123" / "cache").is_dir()
    assert dirs["home"] == str(tmp_path / "abc-123" / "home")


def test_returns_none_when_root_not_writable(tmp_path):
    # Simulate a root-owned /run that the service user cannot create under.
    ro = tmp_path / "runroot"
    ro.mkdir(mode=0o755)
    os.chmod(ro, 0o555)  # no write for owner either
    try:
        assert ensure_task_runtime_dirs("x", str(ro)) is None
    finally:
        os.chmod(ro, 0o755)  # restore so tmp_path cleanup works


def test_existing_tree_is_reused(tmp_path):
    first = ensure_task_runtime_dirs("t1", str(tmp_path))
    second = ensure_task_runtime_dirs("t1", str(tmp_path))
    assert first == second
    assert (tmp_path / "t1" / "home").is_dir()


def test_two_task_ids_get_distinct_trees(tmp_path):
    a = ensure_task_runtime_dirs("aa", str(tmp_path))
    b = ensure_task_runtime_dirs("bb", str(tmp_path))
    assert a is not None and b is not None
    assert a["home"] != b["home"]
