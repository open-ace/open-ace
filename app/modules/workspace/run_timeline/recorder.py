"""
Open ACE - Run Timeline recorder.

The recorder is the single integration seam between the existing remote-session
code and the persisted timeline. ``RemoteSessionManager`` holds one recorder
instance and calls into it from each lifecycle/output hook.

Contract (must hold for every implementation):
- Every method is **non-blocking and best-effort**: it must never raise to the
  caller. It runs on the ``process_session_output`` hot path, so an exception
  would interrupt stdout handling. DbRunRecorder wraps all DB work in
  try/except; a future RemoteApiRecorder must use an internal queue + background
  worker rather than synchronous network I/O.
- ``is_noop`` lets hot-path callers short-circuit before building metadata, so a
  disabled recorder costs essentially nothing.

Attribution is resolved per-run and cached (plan §2.3 source map):
- user_id / model / tool_name  <- agent_sessions (via SessionManager)
- machine_id                   <- session.context["remote_machine_id"]
- tenant_id                    <- remote_machines.tenant_id (per-run cached)
- provider                     <- derived from cli_tool
- key_id                       <- NULL phase 1 (not reported by the agent yet)
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Any

from app.modules.workspace.run_timeline.audit_bridge import maybe_log_audit
from app.modules.workspace.run_timeline.models import _dump_json
from app.repositories.run_timeline_repo import RunTimelineRepository

logger = logging.getLogger(__name__)

# Mirrors RemoteSessionManager._cli_tool_to_provider; duplicated here so the
# recorder stays self-contained and removable.
_PROVIDER_BY_CLI_TOOL = {
    "qwen-code-cli": "openai",
    "claude-code": "anthropic",
    "openclaw": "openai",
    "codex": "openai",
    "codex-cli": "openai",
    "zcode": "anthropic",
    "zcode-code": "anthropic",
}


def _provider_for_cli_tool(cli_tool: str | None) -> str | None:
    if not cli_tool:
        return None
    return _PROVIDER_BY_CLI_TOOL.get(cli_tool)


class RunRecorder:
    """Interface for run-timeline recording. See module docstring for contract."""

    is_noop: bool = False

    def record_session_created(
        self,
        session_id: str,
        *,
        user_id: int | None = None,
        tenant_id: int | None = None,
        machine_id: str | None = None,
        tool_name: str | None = None,
        provider: str | None = None,
        cli_tool: str | None = None,
        model: str | None = None,
    ) -> None:
        raise NotImplementedError

    def record_run_status(self, session_id: str, status: str) -> None:
        raise NotImplementedError

    def record_event(
        self,
        session_id: str,
        event_type: str,
        *,
        event_subtype: str | None = None,
        role: str | None = None,
        content: str | None = None,
        tool_name: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        raise NotImplementedError

    def record_usage(
        self,
        session_id: str,
        tokens: dict[str, int],
        requests: int = 1,
    ) -> None:
        raise NotImplementedError

    def record_approval_request(self, session_id: str, control_request: dict[str, Any]) -> None:
        raise NotImplementedError

    def record_approval_response(
        self,
        session_id: str,
        request_id: str | None,
        decision: str,
        *,
        decided_by: int | None = None,
        decided_by_name: str | None = None,
        message: str | None = None,
    ) -> None:
        raise NotImplementedError


class NullRunRecorder(RunRecorder):
    """No-op recorder used when the feature is disabled. Zero DB writes."""

    is_noop = True

    def record_session_created(self, session_id: str, **_: Any) -> None:
        return

    def record_run_status(self, session_id: str, status: str) -> None:
        return

    def record_event(self, session_id: str, event_type: str, **_: Any) -> None:
        return

    def record_usage(self, session_id: str, tokens: dict[str, int], requests: int = 1) -> None:
        return

    def record_approval_request(self, session_id: str, control_request: dict[str, Any]) -> None:
        return

    def record_approval_response(
        self,
        session_id: str,
        request_id: str | None,
        decision: str,
        *,
        decided_by: int | None = None,
        decided_by_name: str | None = None,
        message: str | None = None,
    ) -> None:
        return


class DbRunRecorder(RunRecorder):
    """Persists runs/events/approvals to the database. Non-blocking."""

    is_noop = False

    def __init__(self, repo: RunTimelineRepository | None = None):
        self._repo = repo or RunTimelineRepository()
        self._lock = threading.Lock()
        # session_id -> cached attribution dict (tenant_id etc. resolved once)
        self._attr_cache: dict[str, dict[str, Any]] = {}
        # session_ids whose agent_runs row has been ensured
        self._ensured: set[str] = set()

    # ── attribution resolution ─────────────────────────────────────

    def _session_obj(self, session_id: str):
        try:
            from app.modules.workspace.remote_session_manager import get_remote_session_manager

            sm = get_remote_session_manager()._session_manager
            return sm.get_session(session_id)
        except Exception as e:  # pragma: no cover - defensive
            logger.debug("run_timeline: session lookup failed for %s: %s", session_id[:8], e)
            return None

    def _machine_tenant_id(self, machine_id: str | None) -> int | None:
        if not machine_id:
            return None
        try:
            from app.modules.workspace.remote_agent_manager import get_remote_agent_manager

            machine = get_remote_agent_manager().get_machine(machine_id)
            if machine:
                tid = machine.get("tenant_id")
                if tid is not None:
                    return int(tid)
        except Exception as e:  # pragma: no cover - defensive
            logger.debug("run_timeline: tenant lookup failed for %s: %s", machine_id, e)
        logger.warning(
            "run_timeline: tenant_id unknown for machine %s, defaulting to 1", machine_id[:8]
        )
        return 1

    def _ensure_attribution(self, session_id: str) -> dict[str, Any]:
        """Resolve and cache attribution for a session from live session state."""
        with self._lock:
            cached = self._attr_cache.get(session_id)
        if cached:
            return cached

        attr: dict[str, Any] = {"run_id": session_id}
        session = self._session_obj(session_id)
        if session is not None:
            ctx = getattr(session, "context", None) or {}
            machine_id = ctx.get("remote_machine_id")
            cli_tool = ctx.get("cli_tool")
            attr["user_id"] = getattr(session, "user_id", None)
            attr["model"] = getattr(session, "model", None)
            attr["tool_name"] = getattr(session, "tool_name", None)
            attr["machine_id"] = machine_id
            attr["cli_tool"] = cli_tool
            attr["provider"] = _provider_for_cli_tool(cli_tool)
            # tenant_id requires a machine lookup; cache the result per run.
            if "tenant_id" not in attr:
                attr["tenant_id"] = self._machine_tenant_id(machine_id)

        with self._lock:
            # Merge with anything set earlier by record_session_created.
            existing = self._attr_cache.get(session_id, {})
            existing.update({k: v for k, v in attr.items() if v is not None or k not in existing})
            self._attr_cache[session_id] = existing
            return existing

    def _set_attribution(self, session_id: str, **attrs: Any) -> None:
        with self._lock:
            cached = self._attr_cache.setdefault(session_id, {"run_id": session_id})
            for k, v in attrs.items():
                if v is not None:
                    cached[k] = v

    # ── run lifecycle ──────────────────────────────────────────────

    def _ensure_run(self, session_id: str) -> dict[str, Any]:
        attr = self._ensure_attribution(session_id)
        if session_id not in self._ensured:
            try:
                self._repo.ensure_run(
                    run_id=session_id,
                    session_id=session_id,
                    user_id=attr.get("user_id"),
                    tenant_id=attr.get("tenant_id"),
                    machine_id=attr.get("machine_id"),
                    tool_name=attr.get("tool_name"),
                    provider=attr.get("provider"),
                    cli_tool=attr.get("cli_tool"),
                    model=attr.get("model"),
                    status="active",
                )
                self._ensured.add(session_id)
            except Exception as e:  # pragma: no cover - defensive
                logger.debug("run_timeline: ensure_run failed for %s: %s", session_id[:8], e)
        return attr

    def record_session_created(self, session_id: str, **attrs: Any) -> None:
        try:
            self._set_attribution(session_id, **attrs)
            attr = self._ensure_run(session_id)
            self._emit(session_id, attr, "session_created", content=attrs.get("title"))
        except Exception as e:  # pragma: no cover - contract: never raise
            logger.debug("run_timeline: record_session_created failed: %s", e)

    def record_run_status(self, session_id: str, status: str) -> None:
        try:
            attr = self._ensure_run(session_id)
            ended = datetime.utcnow() if status in ("completed", "stopped", "error") else None
            self._repo.update_run_status(session_id, status, ended_at=ended)
            self._emit(
                session_id,
                attr,
                status if status in ("stop", "error", "pause", "resume") else "stop",
            )
        except Exception as e:  # pragma: no cover - contract: never raise
            logger.debug("run_timeline: record_run_status failed: %s", e)

    def record_event(self, session_id: str, event_type: str, **kwargs: Any) -> None:
        try:
            attr = self._ensure_run(session_id)
            self._emit(session_id, attr, event_type, **kwargs)
        except Exception as e:  # pragma: no cover - contract: never raise
            logger.debug("run_timeline: record_event(%s) failed: %s", event_type, e)

    def record_usage(self, session_id: str, tokens: dict[str, int], requests: int = 1) -> None:
        try:
            attr = self._ensure_run(session_id)
            inp = int(tokens.get("input", 0) or 0)
            out = int(tokens.get("output", 0) or 0)
            total = inp + out
            meta = {"input": inp, "output": out, "requests": requests}
            self._emit(
                session_id,
                attr,
                "usage_reported",
                metadata=meta,
            )
            # Refresh cumulative snapshot on the run row.
            run = self._repo.get_run_by_session(session_id)
            if run:
                self._repo.update_run_usage(
                    session_id,
                    (run.total_tokens or 0) + total,
                    (run.total_input_tokens or 0) + inp,
                    (run.total_output_tokens or 0) + out,
                    (run.total_requests or 0) + requests,
                )
        except Exception as e:  # pragma: no cover - contract: never raise
            logger.debug("run_timeline: record_usage failed: %s", e)

    # ── approvals ──────────────────────────────────────────────────

    def record_approval_request(self, session_id: str, control_request: dict[str, Any]) -> None:
        try:
            attr = self._ensure_run(session_id)
            request = (
                control_request.get("request", {}) if isinstance(control_request, dict) else {}
            )
            if not isinstance(request, dict):
                request = {}
            request_id = request.get("request_id") or control_request.get("request_id")
            subtype = request.get("subtype") or control_request.get("subtype")
            tool_name = request.get("tool_name") or attr.get("tool_name")
            self._repo.upsert_approval_request(
                request_id=request_id or f"session:{session_id}:{datetime.utcnow().isoformat()}",
                run_id=session_id,
                session_id=session_id,
                tool_name=tool_name,
                request_subtype=subtype,
                request_details=control_request,
            )
            self._emit(
                session_id,
                attr,
                "permission_requested",
                event_subtype=subtype,
                tool_name=tool_name,
                content=_dump_json({"request_id": request_id}) if request_id else None,
            )
        except Exception as e:  # pragma: no cover - contract: never raise
            logger.debug("run_timeline: record_approval_request failed: %s", e)

    def record_approval_response(
        self,
        session_id: str,
        request_id: str | None,
        decision: str,
        *,
        decided_by: int | None = None,
        decided_by_name: str | None = None,
        message: str | None = None,
    ) -> None:
        try:
            attr = self._ensure_run(session_id)
            rid = request_id
            if not rid:
                pending = self._repo.get_latest_pending_approval(session_id)
                rid = pending.request_id if pending else None
            if rid:
                self._repo.update_approval_response(
                    request_id=rid,
                    decision=decision,
                    decided_by=decided_by,
                    decided_by_name=decided_by_name,
                    decision_metadata={"message": message} if message else None,
                )
            self._emit(
                session_id,
                attr,
                "permission_answered",
                content=decision,
                metadata={"request_id": rid, "decision": decision, "message": message},
            )
        except Exception as e:  # pragma: no cover - contract: never raise
            logger.debug("run_timeline: record_approval_response failed: %s", e)

    # ── low-level emit ─────────────────────────────────────────────

    def _emit(
        self,
        session_id: str,
        attr: dict[str, Any],
        event_type: str,
        *,
        event_subtype: str | None = None,
        role: str | None = None,
        content: str | None = None,
        tool_name: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._repo.append_event(
            run_id=session_id,
            session_id=session_id,
            event_type=event_type,
            event_subtype=event_subtype,
            role=role,
            content=content,
            tool_name=tool_name or attr.get("tool_name"),
            provider=provider or attr.get("provider"),
            model=model or attr.get("model"),
            key_id=None,  # phase 1: not reported by agent; "when available"
            user_id=attr.get("user_id"),
            tenant_id=attr.get("tenant_id"),
            machine_id=attr.get("machine_id"),
            metadata=metadata,
        )
        maybe_log_audit(
            event_type=event_type,
            run_id=session_id,
            user_id=attr.get("user_id"),
            session_id=session_id,
            details=metadata,
        )


_recorder_lock = threading.Lock()
_recorder_instance: RunRecorder | None = None


def get_run_recorder() -> RunRecorder:
    """Return the process-wide recorder (Db when enabled, Null otherwise).

    The choice is resolved once and cached; flipping the config flag requires a
    restart (same semantics as the autonomous scheduler).
    """
    global _recorder_instance
    if _recorder_instance is not None:
        return _recorder_instance
    with _recorder_lock:
        if _recorder_instance is None:
            from app.utils.config import is_run_timeline_enabled

            if is_run_timeline_enabled():
                _recorder_instance = DbRunRecorder()
            else:
                _recorder_instance = NullRunRecorder()
    return _recorder_instance


def reset_run_recorder_for_tests() -> None:
    """Clear the cached recorder singleton (tests only)."""
    global _recorder_instance
    with _recorder_lock:
        _recorder_instance = None


__all__ = [
    "RunRecorder",
    "DbRunRecorder",
    "NullRunRecorder",
    "get_run_recorder",
    "reset_run_recorder_for_tests",
]
