"""Per-task runtime directory layout (Issue #2020 Phase A).

The autonomous agent launcher and the Python runner must agree on a single
deterministic layout for a task's private HOME/TMP/XDG directories, keyed by
``task_id`` (per-attempt), so that:

  * concurrent attempts on the same Agent UID do not share HOME/TMP/cache;
  * the launcher and the runner derive identical paths from the same task_id.

These tests pin the pure layout/sanitization helpers before any production
code wires them in.
"""

from __future__ import annotations

import pytest

from app.modules.workspace.autonomous.task_isolation import (
    DEFAULT_TASK_ROOT,
    sanitize_task_id,
    task_runtime_dirs,
)


class TestTaskRuntimeDirs:
    def test_layout_places_each_var_under_root_taskid(self):
        dirs = task_runtime_dirs("abc-123", "/run/openace-agent-tasks")

        assert dirs["root"] == "/run/openace-agent-tasks/abc-123"
        assert dirs["home"] == "/run/openace-agent-tasks/abc-123/home"
        assert dirs["tmp"] == "/run/openace-agent-tasks/abc-123/tmp"
        assert dirs["cache"] == "/run/openace-agent-tasks/abc-123/cache"
        assert dirs["config"] == "/run/openace-agent-tasks/abc-123/config"
        assert dirs["data"] == "/run/openace-agent-tasks/abc-123/data"

    def test_default_root_is_under_run(self):
        # Default root lives under /run (ephemeral, root-owned) so per-task
        # state never persists across reboot and never lands in a user HOME.
        dirs = task_runtime_dirs("t1")
        assert dirs["root"] == f"{DEFAULT_TASK_ROOT}/t1"

    def test_two_different_task_ids_get_disjoint_dirs(self):
        a = task_runtime_dirs("task-a")
        b = task_runtime_dirs("task-b")
        for key in ("root", "home", "tmp", "cache", "config", "data"):
            assert a[key] != b[key]


class TestSanitizeTaskId:
    def test_passes_through_safe_charset(self):
        assert sanitize_task_id("abc-123_X.4") == "abc-123_X.4"

    def test_replaces_unsafe_chars(self):
        # session ids may contain '/' (encoded project roots) or spaces; the
        # value becomes a path component, so it must be filesystem-safe.
        out = sanitize_task_id("a/b c:d")
        assert "/" not in out and " " not in out and ":" not in out
        assert out.startswith("a")

    def test_rejects_empty(self):
        with pytest.raises(ValueError):
            sanitize_task_id("")

    def test_caps_absurd_length(self):
        out = sanitize_task_id("x" * 5000)
        assert len(out) <= 128
