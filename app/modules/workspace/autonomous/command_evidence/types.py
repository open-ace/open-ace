"""Structured command-execution evidence for autonomous workflows (#2046 Phase A).

This is distinct from :mod:`app.modules.workspace.autonomous.evidence`, which
(#2045) verifies *external Git signals* (commit availability, ancestry, remote
branch state) before irreversible git ops. This module captures *command
execution facts* — what ran, where, how it terminated — so the test gate can
stop reverse-engineering pass/fail from free-form agent prose and ``head``/
``tail`` command variants (#1967).

Phase A scope (this module):
- Define the stable ``CommandExecutionEvidence`` schema.
- Provide a deterministic ``derive_terminal_reason`` that maps the raw signals
  the agent runner already produces (``exit_code``, ``is_error``, plus the new
  ``signal``/``timed_out``/``cancelled`` flags) to a single terminal reason.
- Provide a structured ``ExecutionVerdict`` (passed/failed/not_run/inconclusive)
  derived from evidence facts, **not** agent text. Phase A uses this only for
  shadow comparison; the authoritative test verdict stays on the existing
  heuristics until #2022 lands (Phase B).

The DB persistence, recorder, and dual-write plumbing live in sibling modules
(``command_evidence_repo``, the recorder, and the agent-runner tap points).
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
from typing import Any


class TerminalReason(str, Enum):
    """Why a command stopped running.

    Mutually exclusive categories so the test gate can distinguish a real
    failure from a timeout, a crash, a user cancel, or a missing result —
    none of which may be judged ``passed`` (#2046 acceptance: timeout/missing
    result/crash must never count as a pass).
    """

    COMPLETED = "completed"  # process exited normally (exit_code is authoritative)
    TIMEOUT = "timeout"  # killed by a deadline
    SIGNAL = "signal"  # terminated by a signal other than timeout/cancel
    CANCELLED = "cancelled"  # aborted by the orchestrator/user
    CRASH = "crash"  # process died without a usable exit code
    MISSING_RESULT = "missing_result"  # tool_use never paired with a tool_result


class ExecutionVerdict(str, Enum):
    """Structured pass/fail derived from command evidence, not agent prose.

    Used for shadow comparison in Phase A. ``NOT_RUN`` means no evidence row
    exists for the command; ``INCONCLUSIVE`` means a terminal state could not
    be classified (e.g. exit code unavailable and no terminal reason).
    """

    PASSED = "passed"
    FAILED = "failed"
    NOT_RUN = "not_run"
    INCONCLUSIVE = "inconclusive"


def derive_terminal_reason(
    *,
    exit_code: int | None,
    signal: int | None = None,
    timed_out: bool = False,
    cancelled: bool = False,
    has_result: bool = True,
) -> TerminalReason:
    """Map raw execution signals to a single :class:`TerminalReason`.

    Precedence (most specific first): cancelled → timeout → signal → missing
    result → crash (exit code None with a result) → completed. ``completed``
    keeps ``exit_code`` as the authoritative pass/fail signal rather than
    collapsing a non-zero exit into a different category.
    """
    if cancelled:
        return TerminalReason.CANCELLED
    if timed_out:
        return TerminalReason.TIMEOUT
    if signal:
        return TerminalReason.SIGNAL
    if not has_result:
        return TerminalReason.MISSING_RESULT
    if exit_code is None:
        return TerminalReason.CRASH
    return TerminalReason.COMPLETED


def derive_execution_verdict(
    *,
    exit_code: int | None,
    terminal_reason: TerminalReason | str,
) -> ExecutionVerdict:
    """Derive a structured verdict from evidence facts.

    A command is ``PASSED`` only when it ``COMPLETED`` with exit code 0. Any
    non-zero exit, timeout, signal, cancel, crash, or missing result is
    ``FAILED``. An unresolvable state (``COMPLETED`` but exit code missing) is
    ``INCONCLUSIVE`` — never silently promoted to passed.
    """
    reason = (
        terminal_reason.value if isinstance(terminal_reason, TerminalReason) else terminal_reason
    )
    if reason == TerminalReason.COMPLETED.value:
        if exit_code == 0:
            return ExecutionVerdict.PASSED
        if exit_code is None:
            return ExecutionVerdict.INCONCLUSIVE
        return ExecutionVerdict.FAILED
    if reason == TerminalReason.MISSING_RESULT.value:
        return ExecutionVerdict.NOT_RUN
    # timeout / signal / cancelled / crash are all failures for gate purposes.
    return ExecutionVerdict.FAILED


def compute_output_digest(output_excerpt: str) -> str | None:
    """Return a sha256 digest of the output excerpt, or None if empty.

    Phase A stores only a bounded excerpt + digest; full stdout/stderr artifacts
    are referenced by path and produced by #2022's SandboxProvider. Note this
    digests the **truncated excerpt** (see :func:`bound_excerpt`), not the full
    output — two outputs sharing the same head+tail excerpt collide. Treat it
    as an excerpt fingerprint, not a full-output hash.
    """
    if not output_excerpt:
        return None
    return hashlib.sha256(output_excerpt.encode("utf-8", errors="replace")).hexdigest()


@dataclass
class CommandExecutionEvidence:
    """Authoritative facts about one command execution (#2046 Phase A schema).

    ``command_id`` reuses the provider's ``tool_use_id`` so evidence pairs 1:1
    with the existing transcript ``tool_use``/``tool_result`` blocks. The
    schema is intentionally compatible with the dataclass sketched in the
    issue; fields the current agent-runner parsers cannot populate yet
    (``sandbox_id``, ``sandbox_generation``, ``stderr_*``, ``signal``) default
    to None and are filled by #2022's normalized provider events.
    """

    command_id: str
    workflow_id: str = ""
    session_id: str = ""
    milestone_id: str = ""
    sandbox_id: str | None = None
    sandbox_generation: int | None = None
    tool_name: str = ""
    argv: list[str] | None = None
    shell_command: str | None = None
    cwd: str = ""
    execution_profile: str = ""
    started_at: datetime | None = None
    completed_at: datetime | None = None
    exit_code: int | None = None
    signal: int | None = None
    timed_out: bool = False
    cancelled: bool = False
    terminal_reason: str = ""
    stdout_digest: str | None = None
    stderr_digest: str | None = None
    stdout_artifact: str | None = None
    stderr_artifact: str | None = None
    output_excerpt: str = ""
    # Row id assigned by the store on upsert; None for in-memory evidence.
    id: int | None = None
    tenant_id: int = 1
    created_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-friendly dict for storage/API/metadata."""
        data = asdict(self)
        for key in ("started_at", "completed_at", "created_at"):
            value = data.get(key)
            data[key] = value.isoformat() if isinstance(value, datetime) else value
        return data

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> CommandExecutionEvidence:
        """Build an instance from a DB row dict (JSON fields already decoded)."""
        argv = row.get("argv")
        if isinstance(argv, str):
            import json

            try:
                argv = json.loads(argv) if argv else None
            except (TypeError, ValueError):
                argv = None
        return cls(
            id=row.get("id"),
            command_id=row.get("command_id") or "",
            workflow_id=row.get("workflow_id") or "",
            session_id=row.get("session_id") or "",
            milestone_id=row.get("milestone_id") or "",
            sandbox_id=row.get("sandbox_id"),
            sandbox_generation=row.get("sandbox_generation"),
            tool_name=row.get("tool_name") or "",
            argv=argv,
            shell_command=row.get("shell_command"),
            cwd=row.get("cwd") or "",
            execution_profile=row.get("execution_profile") or "",
            started_at=_parse_dt(row.get("started_at")),
            completed_at=_parse_dt(row.get("completed_at")),
            exit_code=row.get("exit_code"),
            signal=row.get("signal"),
            timed_out=bool(row.get("timed_out")),
            cancelled=bool(row.get("cancelled")),
            terminal_reason=row.get("terminal_reason") or "",
            stdout_digest=row.get("stdout_digest"),
            stderr_digest=row.get("stderr_digest"),
            stdout_artifact=row.get("stdout_artifact"),
            stderr_artifact=row.get("stderr_artifact"),
            output_excerpt=row.get("output_excerpt") or "",
            tenant_id=int(row.get("tenant_id") or 1),
            created_at=_parse_dt(row.get("created_at")),
        )


def _parse_dt(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


# Bounded excerpt kept alongside the digest so the test gate / UI can show
# context without the full output artifact (#2022 owns full artifact storage).
OUTPUT_EXCERPT_MAX = 4096
# Head/tail split so the excerpt keeps both the collection/header (head) and
# the verdict/traceback (tail) — test summaries and failures live at the end.
_EXCERPT_HALF = OUTPUT_EXCERPT_MAX // 2


def bound_excerpt(text: str | None) -> str:
    """Truncate output text to the persisted excerpt size (head + tail).

    Test runs put framework banners and the collected-test list at the top but
    the pass/fail summary and failure tracebacks at the bottom. A head-only
    truncation loses the verdict on long runs, so keep the first and last
    ``_EXCERPT_HALF`` characters joined by an ellipsis marker.
    """
    if not text:
        return ""
    if len(text) <= OUTPUT_EXCERPT_MAX:
        return text
    return text[:_EXCERPT_HALF] + "\n...[truncated]...\n" + text[-_EXCERPT_HALF:]
