"""
Open ACE - Run Timeline Module

Persisted provenance timeline for remote agent sessions: one durable run
record per session plus an append-only event stream and durable permission
approvals. The whole feature is self-contained so it can be removed or
re-implemented behind an external API with minimal scarring in existing code
(see plan 0, 2.1).
"""

from app.modules.workspace.run_timeline.models import AgentApproval, AgentRun, RunEvent
from app.modules.workspace.run_timeline.recorder import (
    DbRunRecorder,
    NullRunRecorder,
    RunRecorder,
    get_run_recorder,
)


__all__ = [
    "AgentRun",
    "RunEvent",
    "AgentApproval",
    "RunRecorder",
    "DbRunRecorder",
    "NullRunRecorder",
    "get_run_recorder",
]