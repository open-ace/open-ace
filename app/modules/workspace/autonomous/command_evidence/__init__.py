"""Command-execution evidence package (#2046 Phase A).

Type-only re-exports here to avoid an import cycle (the recorder imports the
repository, which imports the database layer). Callers that need the recorder
should import it explicitly from
``app.modules.workspace.autonomous.command_evidence.recorder``.
"""

from app.modules.workspace.autonomous.command_evidence.types import (
    CommandExecutionEvidence,
    ExecutionVerdict,
    TerminalReason,
    bound_excerpt,
    compute_output_digest,
    derive_execution_verdict,
    derive_terminal_reason,
)

__all__ = [
    "CommandExecutionEvidence",
    "ExecutionVerdict",
    "TerminalReason",
    "bound_excerpt",
    "compute_output_digest",
    "derive_execution_verdict",
    "derive_terminal_reason",
]
