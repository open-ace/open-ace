"""#2022 P4 ② — recorder stamps sandbox attribution on evidence rows.

The #2046-A ``CommandExecutionEvidence`` schema defers ``sandbox_id`` /
``sandbox_generation`` to "#2022's normalized provider events". The provider's
``collect_execution_evidence`` fills them at the contract level (tested in
``test_legacy_posix_provider.py``); these tests lock the runner→recorder wiring
that stamps them onto the persisted per-tool_use rows.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.modules.workspace.autonomous.command_evidence.recorder import CommandEvidenceRecorder


def _capturing_recorder() -> tuple[CommandEvidenceRecorder, list]:
    captured: list = []
    recorder = CommandEvidenceRecorder(
        repo=SimpleNamespace(upsert=lambda ev: captured.append(ev) or 1)
    )
    return recorder, captured


def test_record_tool_use_stamps_sandbox_attribution():
    recorder, captured = _capturing_recorder()
    recorder.record_tool_use(
        command_id="c1", session_id="s1", sandbox_id="sb-9", sandbox_generation=3
    )
    assert captured
    assert captured[0].sandbox_id == "sb-9"
    assert captured[0].sandbox_generation == 3


def test_record_tool_result_stamps_sandbox_attribution():
    # The terminal upsert also carries sandbox attribution so it survives even
    # if the seed row was written by a path that omitted it.
    recorder, captured = _capturing_recorder()
    recorder.record_tool_result(
        command_id="c1", session_id="s1", exit_code=0, sandbox_id="sb-9", sandbox_generation=3
    )
    assert captured[0].sandbox_id == "sb-9"
    assert captured[0].sandbox_generation == 3


def test_emit_from_event_log_propagates_sandbox_to_rows():
    recorder, captured = _capturing_recorder()
    recorder.emit_from_event_log(
        session_id="s1",
        workflow_id="w1",
        event_log=[
            {"type": "tool_use", "tool_use_id": "c1", "tool_name": "Bash"},
            {"type": "tool_result", "tool_use_id": "c1", "exit_code": 0, "text": "ok"},
        ],
        sandbox_id="sb-9",
        sandbox_generation=3,
    )
    stamped = [r for r in captured if r.command_id == "c1"]
    assert stamped
    assert all(r.sandbox_id == "sb-9" and r.sandbox_generation == 3 for r in stamped)
