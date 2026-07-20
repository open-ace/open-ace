# mypy: disable-error-code="return-value,arg-type"
"""
Open ACE - Run Timeline data models.

Dataclasses for the persisted run/event/approval provenance chain of remote
agent sessions. These models are storage records (dicts read from the DB are
coerced into these dataclasses); they carry no behaviour beyond (de)serialising
their JSON ``metadata`` columns.

Attribution fields follow the agreed source map (see plan §2.3):
- ``user_id``      <- agent_sessions.user_id
- ``tenant_id``    <- remote_machines.tenant_id (per-run cached in recorder)
- ``machine_id``   <- agent_sessions.remote_machine_id
- ``model``        <- agent_sessions.model
- ``provider``     <- derived from cli_tool (cached at run creation)
- ``tool_name``    <- agent_sessions.tool_name
- ``key_id``       <- NOT available phase 1 (agent does not report which key it
                      used); persisted as NULL and surfaced "when available".
"""

from __future__ import annotations




import json
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from app.repositories.database import is_postgresql


def _utcnow_naive() -> datetime:
    """Return a timezone-naive UTC now (matches the rest of the codebase)."""
    return datetime.utcnow()


def _parse_json(value: Any) -> Any:
    """Best-effort parse a JSON TEXT column into Python, never raising."""
    if value is None or value == "":
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return None
    return None


def _dump_json(value: Any) -> str | None:
    """Serialise a Python value to a JSON string, or None when empty."""
    if value is None:
        return None
    try:
        if isinstance(value, (dict, list)) and len(value) == 0:
            return None
    except TypeError:
        pass
    return json.dumps(value, ensure_ascii=False, default=str)


@dataclass
class AgentRun:
    """A single persisted run: 1:1 with a remote agent_sessions row."""

    run_id: str
    session_id: str
    user_id: int | None = None
    tenant_id: int | None = None
    machine_id: str | None = None
    tool_name: str | None = None
    provider: str | None = None
    cli_tool: str | None = None
    model: str | None = None
    status: str = "active"
    started_at: str | None = None
    ended_at: str | None = None
    total_tokens: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_requests: int = 0
    metadata: dict[str, Any] | None = None
    created_at: str | None = None
    updated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["metadata"] = self.metadata or {}
        return data

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> AgentRun:
        return cls(
            run_id=row.get("run_id"),
            session_id=row.get("session_id"),
            user_id=row.get("user_id"),
            tenant_id=row.get("tenant_id"),
            machine_id=row.get("machine_id"),
            tool_name=row.get("tool_name"),
            provider=row.get("provider"),
            cli_tool=row.get("cli_tool"),
            model=row.get("model"),
            status=row.get("status", "active"),
            started_at=_iso(row.get("started_at")),
            ended_at=_iso(row.get("ended_at")),
            total_tokens=row.get("total_tokens") or 0,
            total_input_tokens=row.get("total_input_tokens") or 0,
            total_output_tokens=row.get("total_output_tokens") or 0,
            total_requests=row.get("total_requests") or 0,
            metadata=_parse_json(row.get("metadata")) or {},
            created_at=_iso(row.get("created_at")),
            updated_at=_iso(row.get("updated_at")),
        )


@dataclass
class RunEvent:
    """One append-only entry in a run's timeline."""

    id: int | None = None
    run_id: str = ""
    session_id: str = ""
    event_type: str = ""
    event_subtype: str | None = None
    role: str | None = None
    content: str | None = None
    tool_name: str | None = None
    provider: str | None = None
    model: str | None = None
    key_id: str | None = None
    user_id: int | None = None
    tenant_id: int | None = None
    machine_id: str | None = None
    metadata: dict[str, Any] | None = None
    event_ts: str | None = None
    created_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["metadata"] = self.metadata or {}
        return data

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> RunEvent:
        return cls(
            id=row.get("id"),
            run_id=row.get("run_id"),
            session_id=row.get("session_id"),
            event_type=row.get("event_type", ""),
            event_subtype=row.get("event_subtype"),
            role=row.get("role"),
            content=row.get("content"),
            tool_name=row.get("tool_name"),
            provider=row.get("provider"),
            model=row.get("model"),
            key_id=row.get("key_id"),
            user_id=row.get("user_id"),
            tenant_id=row.get("tenant_id"),
            machine_id=row.get("machine_id"),
            metadata=_parse_json(row.get("metadata")) or {},
            event_ts=_iso(row.get("event_ts")),
            created_at=_iso(row.get("created_at")),
        )


@dataclass
class AgentApproval:
    """A durable permission request + its response, keyed by request_id."""

    id: int | None = None
    request_id: str = ""
    run_id: str = ""
    session_id: str = ""
    tool_name: str | None = None
    request_subtype: str | None = None
    request_details: dict[str, Any] | None = None
    status: str = "pending"
    decision: str | None = None
    decided_by: int | None = None
    decided_by_name: str | None = None
    decision_metadata: dict[str, Any] | None = None
    requested_at: str | None = None
    decided_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["request_details"] = self.request_details or {}
        data["decision_metadata"] = self.decision_metadata or {}
        return data

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> AgentApproval:
        return cls(
            id=row.get("id"),
            request_id=row.get("request_id"),
            run_id=row.get("run_id"),
            session_id=row.get("session_id"),
            tool_name=row.get("tool_name"),
            request_subtype=row.get("request_subtype"),
            request_details=_parse_json(row.get("request_details")) or {},
            status=row.get("status", "pending"),
            decision=row.get("decision"),
            decided_by=row.get("decided_by"),
            decided_by_name=row.get("decided_by_name"),
            decision_metadata=_parse_json(row.get("decision_metadata")) or {},
            requested_at=_iso(row.get("requested_at")),
            decided_at=_iso(row.get("decided_at")),
            created_at=_iso(row.get("created_at")),
            updated_at=_iso(row.get("updated_at")),
        )


def _iso(value: Any) -> str | None:
    """Normalise a timestamp-ish DB value to an ISO string (or None)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


__all__ = [
    "AgentRun",
    "RunEvent",
    "AgentApproval",
    "_utcnow_naive",
    "_dump_json",
    "_parse_json",
    "is_postgresql",
]
