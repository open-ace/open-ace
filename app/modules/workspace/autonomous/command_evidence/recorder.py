"""Command-execution evidence recorder (#2046 Phase A).

Non-blocking, best-effort recorder that dual-writes ``CommandExecutionEvidence``
alongside the existing in-memory ``event_log`` heuristics. Mirrors the
``run_timeline`` recorder contract:

- Every method is non-blocking and never raises to the caller (it runs on the
  agent stdout hot path).
- DB I/O is handed to a background writer so a slow database cannot stall the
  agent runner; ``is_noop`` lets callers short-circuit when disabled.
- ``record_tool_use`` creates a pending evidence row; ``record_tool_result``
  upserts the terminal state for the same ``(session_id, command_id)``.

Phase A only writes evidence; it does not feed the test verdict (the existing
heuristics stay authoritative). Shadow comparison consumes the persisted rows
separately (see ``compare_verdicts``).
"""

from __future__ import annotations

import logging
import queue as queue_module
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from app.modules.workspace.autonomous.command_evidence.types import (
    CommandExecutionEvidence,
    TerminalReason,
    bound_excerpt,
    compute_output_digest,
    derive_terminal_reason,
)
from app.repositories.command_evidence_repo import CommandExecutionEvidenceRepository

logger = logging.getLogger(__name__)

_QUEUE_MAXSIZE = 10000
_SHUTDOWN_FLUSH_TIMEOUT = 5.0


class _EvidenceWriter:
    """Abstract sink for DB writes. ``submit`` must never raise to the caller."""

    def submit(self, fn: Callable[[], None]) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def flush(self, timeout: float | None = None) -> bool:  # pragma: no cover - interface
        return True


class _SyncEvidenceWriter(_EvidenceWriter):
    """Runs writes inline on the calling thread (unit tests)."""

    def submit(self, fn: Callable[[], None]) -> None:
        fn()

    def flush(self, timeout: float | None = None) -> bool:
        return True


class _AsyncEvidenceWriter(_EvidenceWriter):
    """Drains a bounded FIFO queue on a single background daemon thread.

    The worker is the sole DB writer, so per-session evidence ordering is
    preserved. On a wedged DB the bounded queue drops new writes (best-effort)
    and warns at a throttled rate; ``atexit`` flushes the tail on shutdown.
    """

    def __init__(self, maxsize: int = _QUEUE_MAXSIZE) -> None:
        import atexit

        self._queue: queue_module.Queue[Callable[[], None]] = queue_module.Queue(maxsize=maxsize)
        self._dropped = 0
        self._thread = threading.Thread(
            target=self._drain, name="command-evidence-writer", daemon=True
        )
        self._thread.start()
        atexit.register(self._shutdown_flush)

    def submit(self, fn: Callable[[], None]) -> None:
        try:
            self._queue.put_nowait(fn)
        except queue_module.Full:
            self._dropped += 1
            if self._dropped == 1 or self._dropped % 1000 == 0:
                logger.warning(
                    "command_evidence: write queue full (>%d); dropped %d evidence write(s)",
                    self._queue.maxsize,
                    self._dropped,
                )

    def flush(self, timeout: float | None = None) -> bool:
        if timeout is None:
            self._queue.join()
            return True
        joiner = threading.Thread(
            target=self._queue.join, name="command-evidence-flush", daemon=True
        )
        joiner.start()
        joiner.join(timeout)
        drained = not joiner.is_alive()
        if not drained:
            logger.warning("command_evidence: flush did not drain within %.1fs", timeout)
        return drained

    def _shutdown_flush(self) -> None:
        self.flush(timeout=_SHUTDOWN_FLUSH_TIMEOUT)

    def _drain(self) -> None:
        while True:
            fn = self._queue.get()
            try:
                fn()
            except Exception as e:  # pragma: no cover - contract: writer never raises
                logger.debug("command_evidence: background write failed: %s", e)
            finally:
                self._queue.task_done()


class CommandEvidenceRecorder:
    """Records structured command-execution evidence for autonomous sessions.

    Phase A dual-write entry point. The agent runner calls ``record_tool_use``
    on a ``tool_use`` event and ``record_tool_result`` on the paired
    ``tool_result``; the recorder persists a ``CommandExecutionEvidence`` row
    keyed by ``(session_id, command_id)``.
    """

    is_noop: bool = False

    def __init__(
        self,
        repo: CommandExecutionEvidenceRepository | None = None,
        *,
        writer: _EvidenceWriter | None = None,
    ) -> None:
        self._repo = repo
        self._writer = writer or _SyncEvidenceWriter()

    @property
    def repo(self) -> CommandExecutionEvidenceRepository:
        if self._repo is None:
            self._repo = CommandExecutionEvidenceRepository()
        return self._repo

    def flush(self, timeout: float | None = None) -> bool:
        """Block until pending writes are durable (tests / shutdown)."""
        return self._writer.flush(timeout)

    def record_tool_use(
        self,
        *,
        command_id: str,
        session_id: str,
        workflow_id: str = "",
        milestone_id: str = "",
        tool_name: str = "",
        shell_command: str | None = None,
        argv: list[str] | None = None,
        cwd: str = "",
        execution_profile: str = "",
        started_at: datetime | None = None,
        tenant_id: int = 1,
        sandbox_id: str | None = None,
        sandbox_generation: int | None = None,
    ) -> None:
        """Record (or seed) the evidence row for a command invocation."""
        if self.is_noop or not command_id or not session_id:
            return

        evidence = CommandExecutionEvidence(
            command_id=command_id,
            workflow_id=workflow_id,
            session_id=session_id,
            milestone_id=milestone_id,
            sandbox_id=sandbox_id,
            sandbox_generation=sandbox_generation,
            tool_name=tool_name,
            shell_command=shell_command,
            argv=argv,
            cwd=cwd,
            execution_profile=execution_profile,
            started_at=started_at or datetime.now(timezone.utc),
            tenant_id=tenant_id,
        )

        def _write() -> None:
            self.repo.upsert(evidence)

        self._submit(_write)

    def record_tool_result(
        self,
        *,
        command_id: str,
        session_id: str,
        exit_code: int | None = None,
        signal: int | None = None,
        timed_out: bool = False,
        cancelled: bool = False,
        output_excerpt: str | None = None,
        completed_at: datetime | None = None,
        sandbox_id: str | None = None,
        sandbox_generation: int | None = None,
    ) -> None:
        """Upsert the terminal state for a command's evidence row."""
        if self.is_noop or not command_id or not session_id:
            return

        excerpt = bound_excerpt(output_excerpt)
        terminal_reason = derive_terminal_reason(
            exit_code=exit_code,
            signal=signal,
            timed_out=timed_out,
            cancelled=cancelled,
            has_result=True,
        )
        evidence = CommandExecutionEvidence(
            command_id=command_id,
            session_id=session_id,
            sandbox_id=sandbox_id,
            sandbox_generation=sandbox_generation,
            exit_code=exit_code,
            signal=signal,
            timed_out=timed_out,
            cancelled=cancelled,
            terminal_reason=terminal_reason.value,
            completed_at=completed_at or datetime.now(timezone.utc),
            output_excerpt=excerpt,
            stdout_digest=compute_output_digest(excerpt),
        )

        def _write() -> None:
            self.repo.upsert(evidence)

        self._submit(_write)

    def emit_from_event_log(
        self,
        *,
        session_id: str,
        workflow_id: str = "",
        milestone_id: str = "",
        event_log: list[dict[str, Any]],
        sandbox_id: str | None = None,
        sandbox_generation: int | None = None,
    ) -> None:
        """Walk an agent task's ``event_log`` and persist command evidence.

        Single dual-write entry point (#2046 Phase A): reads the same unified
        ``{type: tool_use|tool_result, ...}`` events the existing heuristics
        consume and writes one ``CommandExecutionEvidence`` row per command.
        ``tool_use`` seeds the row; the paired ``tool_result`` upserts the
        terminal state. Unpaired ``tool_use`` events become ``missing_result``
        rows at the end of the pass so shadow comparison can see them.

        Best-effort: never raises; a malformed event is skipped.
        """
        if self.is_noop or not session_id or not event_log:
            return

        pending: dict[str, dict[str, Any]] = {}
        results: dict[str, dict[str, Any]] = {}
        order: list[str] = []

        for event in event_log:
            try:
                if not isinstance(event, dict):
                    continue
                etype = event.get("type")
                command_id = event.get("tool_use_id") or event.get("id")
                if etype == "tool_use":
                    if not command_id:
                        continue
                    pending[command_id] = event
                    if command_id not in order:
                        order.append(command_id)
                    tool_input = event.get("tool_input") or {}
                    shell_command = None
                    if isinstance(tool_input, dict):
                        shell_command = tool_input.get("command") or tool_input.get("cmd")
                    self.record_tool_use(
                        command_id=command_id,
                        session_id=session_id,
                        workflow_id=workflow_id,
                        milestone_id=milestone_id,
                        tool_name=event.get("tool_name") or "",
                        shell_command=shell_command if isinstance(shell_command, str) else None,
                        sandbox_id=sandbox_id,
                        sandbox_generation=sandbox_generation,
                    )
                elif etype == "tool_result":
                    if not command_id:
                        continue
                    results[command_id] = event
                    if command_id not in order:
                        order.append(command_id)
                    self.record_tool_result(
                        command_id=command_id,
                        session_id=session_id,
                        exit_code=event.get("exit_code"),
                        output_excerpt=event.get("text"),
                        sandbox_id=sandbox_id,
                        sandbox_generation=sandbox_generation,
                    )
            except Exception as e:  # pragma: no cover - never raise to caller
                logger.debug("command_evidence: event emit failed: %s", e)

        # tool_use events that never paired with a tool_result → missing_result.
        for command_id in order:
            if command_id in pending and command_id not in results:
                try:
                    self._mark_missing_result(command_id, session_id)
                except Exception as e:  # pragma: no cover
                    logger.debug("command_evidence: missing-result mark failed: %s", e)

    def _mark_missing_result(self, command_id: str, session_id: str) -> None:
        """Flag an unpaired command's evidence row as ``missing_result``."""
        if self.is_noop:
            return
        evidence = CommandExecutionEvidence(
            command_id=command_id,
            session_id=session_id,
            terminal_reason=TerminalReason.MISSING_RESULT.value,
        )

        def _write() -> None:
            self.repo.upsert(evidence)

        self._submit(_write)

    def _submit(self, fn: Callable[[], None]) -> None:
        try:
            self._writer.submit(fn)
        except Exception as e:  # pragma: no cover - contract: never raise to caller
            logger.debug("command_evidence: submit failed: %s", e)


class _NoopEvidenceRecorder(CommandEvidenceRecorder):
    """Zero-cost recorder used when the feature is disabled."""

    is_noop = True

    def __init__(self) -> None:  # noqa: D401 - simple no-op construction
        # Bypass repo/writer construction entirely. ``is_noop`` short-circuits
        # every record_* / emit path before ``self.repo`` is touched; keep the
        # attributes defined so attribute access never raises if a caller
        # forgets the noop guard.
        self._repo = None
        self._writer = _SyncEvidenceWriter()

    def flush(self, timeout: float | None = None) -> bool:
        return True


def get_command_evidence_recorder() -> CommandEvidenceRecorder:
    """Return the process-wide recorder (async writer) or a no-op.

    Resolves once from the ``autonomous.command_evidence`` config flag; tests
    use ``reset_command_evidence_recorder_for_tests`` to swap implementations.
    """
    return _resolve_recorder()


_recorder_singleton: CommandEvidenceRecorder | None = None
_recorder_lock = threading.Lock()


def _is_evidence_enabled() -> bool:
    try:
        from app.utils.config import get_config_value

        return bool(get_config_value("autonomous", "command_evidence", True))
    except Exception:
        # Default on: Phase A wants dual-write everywhere so shadow comparison
        # has data. A failure reading config must not break the agent runner.
        return True


def _resolve_recorder() -> CommandEvidenceRecorder:
    global _recorder_singleton
    if _recorder_singleton is not None:
        return _recorder_singleton
    with _recorder_lock:
        if _recorder_singleton is None:
            if _is_evidence_enabled():
                _recorder_singleton = CommandEvidenceRecorder(writer=_AsyncEvidenceWriter())
            else:
                _recorder_singleton = _NoopEvidenceRecorder()
    return _recorder_singleton


def reset_command_evidence_recorder_for_tests(
    recorder: CommandEvidenceRecorder | None = None,
) -> None:
    """Replace (or clear) the singleton recorder; for tests only."""
    global _recorder_singleton
    with _recorder_lock:
        _recorder_singleton = recorder


def emit_command_evidence(
    *,
    session_id: str,
    workflow_id: str = "",
    milestone_id: str = "",
    event_log: list[dict[str, Any]],
    sandbox_id: str | None = None,
    sandbox_generation: int | None = None,
) -> None:
    """Dual-write command evidence from an agent task's event log.

    Convenience entry point for the orchestrator: persists a
    ``CommandExecutionEvidence`` row per command alongside the existing
    in-memory heuristics (Phase A shadow). Best-effort, never raises.
    ``sandbox_id`` / ``sandbox_generation`` (#2022) attribute every row to the
    SandboxProvider sandbox that ran the task — the fields #2046-A deferred to
    the provider.
    """
    try:
        get_command_evidence_recorder().emit_from_event_log(
            session_id=session_id,
            workflow_id=workflow_id,
            milestone_id=milestone_id,
            event_log=event_log,
            sandbox_id=sandbox_id,
            sandbox_generation=sandbox_generation,
        )
    except Exception as e:  # pragma: no cover - never raise to caller
        logger.debug("command_evidence: emit failed: %s", e)


def compare_verdicts(
    *,
    heuristic_passed: bool,
    evidence_rows: list[CommandExecutionEvidence],
) -> dict[str, Any]:
    """Compare the heuristic test verdict against the structured evidence.

    Phase A shadow comparison: returns a dict describing whether the
    structured verdict (derived purely from evidence facts) agrees with the
    existing heuristic verdict. ``divergence`` is True when they disagree.

    A missing-evidence run with a heuristic pass is always a divergence — the
    core #2046/#1967 invariant that agent prose alone must not mark a pass.
    """
    from app.modules.workspace.autonomous.command_evidence import derive_execution_verdict

    if not evidence_rows:
        structured = "not_run"
        divergence = heuristic_passed  # pass without evidence = divergence
        return {
            "heuristic_verdict": "passed" if heuristic_passed else "not_passed",
            "structured_verdict": structured,
            "divergence": divergence,
            "reason": "no command execution evidence recorded",
        }

    # A run passes structurally only if every recorded command passed.
    verdicts = [
        derive_execution_verdict(
            exit_code=row.exit_code, terminal_reason=row.terminal_reason or "missing_result"
        ).value
        for row in evidence_rows
    ]
    # verdicts is non-empty here (the empty case returns above). A run passes
    # structurally only when every recorded command passed.
    structured_passed = all(v == "passed" for v in verdicts)
    divergence = structured_passed != heuristic_passed
    return {
        "heuristic_verdict": "passed" if heuristic_passed else "not_passed",
        "structured_verdict": "passed" if structured_passed else "failed",
        "divergence": divergence,
        "command_verdicts": verdicts,
    }
