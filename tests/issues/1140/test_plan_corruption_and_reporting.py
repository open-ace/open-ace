"""Tests for plan corruption fix and false-success reporting fixes.

P1-2: Plan output corruption (duplicated sections + leaked tool JSON)
P2-1: Dev phase false "Completed" with non-agent commit
P2-2: tests_run milestone "completed" while no tests ran
"""

import os
import sys
from unittest.mock import MagicMock, patch

_REMOTE_AGENT_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "remote-agent")
)
if _REMOTE_AGENT_DIR not in sys.path:
    sys.path.insert(0, _REMOTE_AGENT_DIR)


# ── P1-2a: ZCode event dedup prevents duplication ─────────────────────────


def test_forward_events_dedup_by_hash():
    """_forward_events must not forward the same event twice — prevents
    duplicated plan sections from the tail poll after turn.completed."""
    import json

    from zcode_app_server import ZCodeAppServerSession

    # Create a minimal session-like object to test _forward_events
    session = ZCodeAppServerSession.__new__(ZCodeAppServerSession)
    session._last_event_seq = 0
    session._forwarded_event_hashes = set()
    session.session_id = "test"
    session._stopped = MagicMock()
    session._stopped.is_set = lambda: False

    forwarded = []
    session.output_callback = lambda sid, data, stream, done: forwarded.append(data)

    # Simulate a model.streaming event without seq (the problematic case)
    streaming_event = {"type": "model.streaming", "payload": {"delta": "Hello "}}

    # First forward — should go through
    session._forward_events([streaming_event])
    assert len(forwarded) == 1

    # Second forward (tail poll re-delivery) — must be deduped
    session._forward_events([streaming_event])
    assert len(forwarded) == 1  # NOT 2


def test_forward_events_seq_still_works():
    """seq-based dedup must still function alongside hash dedup."""
    from zcode_app_server import ZCodeAppServerSession

    session = ZCodeAppServerSession.__new__(ZCodeAppServerSession)
    session._last_event_seq = 0
    session._forwarded_event_hashes = set()
    session.session_id = "test"
    session._stopped = MagicMock()
    session._stopped.is_set = lambda: False

    forwarded = []
    session.output_callback = lambda sid, data, stream, done: forwarded.append(data)

    event = {"seq": 1, "type": "model.streaming", "payload": {"delta": "Hi"}}
    session._forward_events([event])
    session._forward_events([event])  # same seq
    assert len(forwarded) == 1


def test_run_turn_resets_dedup_state():
    """_run_turn should clear dedup state so new turns aren't blocked."""
    from zcode_app_server import ZCodeAppServerSession

    session = ZCodeAppServerSession.__new__(ZCodeAppServerSession)
    session._forwarded_event_hashes = {"old_hash_1", "old_hash_2"}
    session._last_event_seq = 42

    # _run_turn resets at the top — we test by checking the reset lines exist
    import inspect

    source = inspect.getsource(ZCodeAppServerSession._run_turn)
    assert "_forwarded_event_hashes.clear()" in source
    assert "_last_event_seq = 0" in source


# ── P1-2b: Collector filters leaked tool-call JSON ────────────────────────


def test_collector_filters_tool_json_from_assistant_text():
    """on_output must not append tool-call JSON to assistant_text."""
    from app.modules.workspace.autonomous.agent_runner import _ZcodeResultCollector

    c = _ZcodeResultCollector()
    # Simulate a leaked tool invocation in assistant content
    c.on_output(
        "sid",
        '{"type":"assistant","message":{"content":"{\\"command\\":\\"ls\\",\\"subagent_type\\":\\"Explore\\"}"}}',
        "stdout",
        False,
    )
    # Must NOT appear in assistant_text
    assert "command" not in c.assistant_text
    assert len(c.event_log) == 0


def test_collector_keeps_normal_assistant_text():
    """Normal prose must still be accumulated."""
    from app.modules.workspace.autonomous.agent_runner import _ZcodeResultCollector

    c = _ZcodeResultCollector()
    c.on_output(
        "sid",
        '{"type":"assistant","message":{"content":"## Plan: Fix the bug"}}',
        "stdout",
        False,
    )
    assert "## Plan: Fix the bug" in c.assistant_text


def test_looks_like_tool_json_helper():
    """The _looks_like_tool_json helper correctly classifies text."""
    from app.modules.workspace.autonomous.agent_runner import _looks_like_tool_json

    # Tool JSON variants
    assert _looks_like_tool_json('{"command": "ls /tmp"}')
    assert _looks_like_tool_json('{"tool": "Read", "file_path": "/tmp/a.py"}')
    assert _looks_like_tool_json('{"type": "tool_use", "name": "Write"}')
    assert _looks_like_tool_json('{"subagent_type": "Explore", "prompt": "..."}')

    # Normal prose
    assert not _looks_like_tool_json("## Implementation Plan")
    assert not _looks_like_tool_json("First, we need to fix the backend.")
    assert not _looks_like_tool_json('{"type": "assistant"}')  # no tool markers
    assert not _looks_like_tool_json("")


# ── P2-1a: Dev completion comment skipped on failure ─────────────────────


def test_do_development_checks_status_before_comment():
    """_do_development must check workflow status after _run_development_agent
    and skip _post_dev_completion_comment if failed."""
    import inspect

    from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

    source = inspect.getsource(AutonomousOrchestrator._do_development)
    # The fix adds a status check before the comment call
    assert 'status") != "failed"' in source or 'status") == "failed"' in source, (
        "_do_development must guard _post_dev_completion_comment with a "
        "status check to avoid false 'Completed' comments on failure"
    )


# ── P2-1b: Branch fallback validates commit changed ──────────────────────


def test_branch_fallback_checks_commit_before():
    """The branch_has_changes_vs_base fallback must also verify commit_sha
    differs from commit_before — not just that the branch diverges from main."""
    import inspect

    from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

    source = inspect.getsource(AutonomousOrchestrator._run_development_agent)
    # The fix adds commit_sha != commit_before check in the fallback path
    assert "commit_sha != commit_before" in source or "commit_sha == commit_before" in source, (
        "Branch fallback must verify the commit advanced this session, "
        "not just that the branch diverges from origin/main"
    )


# ── P2-2: Test milestone corrected on skip ────────────────────────────────


def test_test_phase_corrects_milestone_on_skip():
    """_run_test_phase must update milestone status to 'failed' when tests
    are skipped, not leave it as 'completed'."""
    import inspect

    from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

    source = inspect.getsource(AutonomousOrchestrator._run_test_phase)
    # The fix adds a milestone update in the skip handling block
    # Look for the correction inside the tests_actually_skipped branch
    skip_section = source[source.index("tests_actually_skipped") :]
    assert "update_milestone" in skip_section, (
        "_run_test_phase must correct milestone status to 'failed' when "
        "tests are skipped, not leave it as 'completed'"
    )
