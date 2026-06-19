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


# ── P1-2a: ZCode event dedup prevents cross-poll duplication ─────────────


def _make_test_session():
    """Create a minimal ZCodeAppServerSession for testing _forward_events."""
    from zcode_app_server import ZCodeAppServerSession

    session = ZCodeAppServerSession.__new__(ZCodeAppServerSession)
    session._last_event_seq = 0
    session._prior_event_hashes = set()
    session._current_event_hashes = set()
    session.session_id = "test"
    session._stopped = MagicMock()
    session._stopped.is_set = lambda: False
    return session


def test_forward_events_dedup_across_polls():
    """_forward_events must not forward the same event across poll cycles —
    prevents duplicated plan sections from the tail poll after turn.completed."""
    session = _make_test_session()
    forwarded = []
    session.output_callback = lambda sid, data, stream, done: forwarded.append(data)

    streaming_event = {"type": "model.streaming", "payload": {"delta": "Hello "}}

    # Simulate poll cycle 1: prior←current, clear current, then forward
    session._prior_event_hashes = session._current_event_hashes
    session._current_event_hashes = set()
    session._forward_events([streaming_event])
    assert len(forwarded) == 1

    # Simulate poll cycle 2 (tail poll): same event re-delivered
    session._prior_event_hashes = session._current_event_hashes
    session._current_event_hashes = set()
    session._forward_events([streaming_event])
    assert len(forwarded) == 1  # NOT 2 — deduped across polls


def test_forward_events_allows_same_content_in_one_batch():
    """Two identical events in the SAME poll batch must BOTH be forwarded —
    this covers legitimate repeated streaming deltas (e.g. two '\\n' tokens)."""
    session = _make_test_session()
    forwarded = []
    session.output_callback = lambda sid, data, stream, done: forwarded.append(data)

    # Two identical streaming events in one batch
    delta_event = {"type": "model.streaming", "payload": {"delta": "\n"}}
    session._prior_event_hashes = set()  # first poll, no prior
    session._current_event_hashes = set()
    session._forward_events([delta_event, delta_event])
    assert len(forwarded) == 2  # Both forwarded — same batch, not cross-poll


def test_forward_events_seq_still_works():
    """seq-based dedup must still function alongside hash dedup."""
    session = _make_test_session()
    forwarded = []
    session.output_callback = lambda sid, data, stream, done: forwarded.append(data)

    event = {"seq": 1, "type": "model.streaming", "payload": {"delta": "Hi"}}
    session._forward_events([event])
    session._forward_events([event])  # same seq, same batch
    assert len(forwarded) == 1


def test_run_turn_resets_dedup_state_behavioral():
    """_run_turn should clear dedup state so new turns aren't blocked.

    Behavioral test: populate dedup sets, call _run_turn (mocked internals),
    then verify the sets were cleared at the top of the method.
    """
    from zcode_app_server import ZCodeAppServerSession

    session = _make_test_session()
    session._prior_event_hashes = {"old_hash_1", "old_hash_2"}
    session._current_event_hashes = {"old_hash_3"}
    session._last_event_seq = 42
    session._cli_session_id = "sess_test"
    session._turn_done = MagicMock()
    session._turn_done.clear = MagicMock()
    session._turn_done.set = MagicMock()
    session._turn_done.is_set = lambda: False
    session.output_callback = MagicMock()
    session._request = MagicMock(return_value=None)
    session._report_usage = MagicMock()
    session._drain_events_until_idle = MagicMock()
    session._stopped = MagicMock()
    session._stopped.is_set = lambda: False

    session._run_turn("test prompt")

    # Dedup state must be cleared by _run_turn
    assert len(session._prior_event_hashes) == 0
    assert len(session._current_event_hashes) == 0
    assert session._last_event_seq == 0


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
    """The _looks_like_tool_json helper correctly classifies text.

    Uses full JSON parse + top-level key check (not substring), so prose
    containing JSON snippets is NOT filtered — only actual tool-call blobs."""
    from app.modules.workspace.autonomous.agent_runner import _looks_like_tool_json

    # Tool-call JSON (valid JSON with tool keys at top level)
    assert _looks_like_tool_json('{"command": "ls /tmp"}')
    assert _looks_like_tool_json('{"tool": "Read", "file_path": "/tmp/a.py"}')
    assert _looks_like_tool_json('{"subagent_type": "Explore", "prompt": "..."}')

    # NOT tool JSON: valid JSON but no tool keys at top level
    assert not _looks_like_tool_json('{"type": "assistant"}')
    assert not _looks_like_tool_json('{"name": "Write"}')

    # NOT tool JSON: prose that happens to contain JSON-like text
    assert not _looks_like_tool_json("## Implementation Plan")
    assert not _looks_like_tool_json("First, we need to fix the backend.")
    assert not _looks_like_tool_json('Run this: {"command": "make test"}')  # prose prefix
    assert not _looks_like_tool_json("")

    # NOT tool JSON: invalid JSON (prose with braces but not parseable)
    assert not _looks_like_tool_json("{some prose with braces}")


# ── P2-1a: Dev completion comment skipped on failure ─────────────────────


def test_do_development_skips_comment_on_failure():
    """_do_development must check workflow status after _run_development_agent
    and skip _post_dev_completion_comment if failed.

    Static assertion: _do_development is deeply coupled to the orchestrator's
    internal state (_gh, repo, workflow property), making a pure behavioral
    test impractical without a full integration harness. The source guard
    catches regressions to the unconditional comment call.
    """
    import inspect

    from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

    source = inspect.getsource(AutonomousOrchestrator._do_development)
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
