"""Recorder captures argv and synthesizes a command from it (#2401 c).

The recognizer reading argv (test_tool_name_contract) is only half of (c): the
authoritative verdict reads persisted ``CommandExecutionEvidence`` rows, and the
recorder — the sole writer of those rows — previously stored only
``command``/``cmd``. An argv-only provider therefore left ``shell_command`` empty
*and* ``argv`` null, so both recognition and framework routing
(``_resolve_framework`` reads ``shell_command``) were blind to it → the evidence
was mis-judged NOT_RUN. This fixes it at the single write point: extract argv and,
when there is no joined command, synthesize one from it.
"""

from __future__ import annotations

from app.modules.workspace.autonomous.command_evidence import CommandExecutionEvidence
from app.modules.workspace.autonomous.command_evidence.recorder import CommandEvidenceRecorder


class _CapturingRepo:
    """Minimal repo double: records every evidence passed to ``upsert``."""

    def __init__(self) -> None:
        self.upserts: list[CommandExecutionEvidence] = []

    def upsert(self, evidence: CommandExecutionEvidence) -> int:
        self.upserts.append(evidence)
        return len(self.upserts)


def _seed_row(repo: _CapturingRepo, command_id: str) -> CommandExecutionEvidence:
    # The tool_use seed row carries the identity fields (tool_name/argv/command);
    # the tool_result upsert carries none of them. Pick the seed.
    return next(e for e in repo.upserts if e.command_id == command_id and e.tool_name)


def _emit(event_log: list[dict]) -> _CapturingRepo:
    repo = _CapturingRepo()
    recorder = CommandEvidenceRecorder(repo=repo)
    recorder.emit_from_event_log(
        session_id="sess-argv",
        workflow_id="wf-1",
        milestone_id="ms-1",
        event_log=event_log,
    )
    return repo


def test_argv_only_tool_use_is_persisted_and_synthesized_into_command():
    repo = _emit(
        [
            {
                "type": "tool_use",
                "tool_use_id": "tu1",
                "tool_name": "Bash",
                "tool_input": {"argv": ["pytest", "tests/", "-k", "auth"]},
            },
            {"type": "tool_result", "tool_use_id": "tu1", "exit_code": 0, "text": "3 passed"},
        ]
    )
    seed = _seed_row(repo, "tu1")
    assert seed.argv == ["pytest", "tests/", "-k", "auth"]
    # Synthesized so _resolve_framework / recognition (which read shell_command)
    # can see the argv-only command.
    assert seed.shell_command == "pytest tests/ -k auth"


def test_args_key_is_also_captured():
    repo = _emit(
        [
            {
                "type": "tool_use",
                "tool_use_id": "tu1",
                "tool_name": "run_shell_command",
                "tool_input": {"args": ["vitest", "run"]},
            },
        ]
    )
    seed = _seed_row(repo, "tu1")
    assert seed.argv == ["vitest", "run"]
    assert seed.shell_command == "vitest run"


def test_command_string_is_preferred_but_argv_is_still_stored():
    repo = _emit(
        [
            {
                "type": "tool_use",
                "tool_use_id": "tu1",
                "tool_name": "Bash",
                "tool_input": {"command": "pytest tests/", "argv": ["ignored"]},
            },
        ]
    )
    seed = _seed_row(repo, "tu1")
    # The joined command the provider actually executed wins for shell_command...
    assert seed.shell_command == "pytest tests/"
    # ...but the raw argv is preserved for observability.
    assert seed.argv == ["ignored"]


def test_command_only_tool_use_leaves_argv_none():
    repo = _emit(
        [
            {
                "type": "tool_use",
                "tool_use_id": "tu1",
                "tool_name": "Bash",
                "tool_input": {"command": "pytest tests/"},
            },
        ]
    )
    seed = _seed_row(repo, "tu1")
    assert seed.shell_command == "pytest tests/"
    assert seed.argv is None
