"""
Open ACE - Run Timeline recorder.

The recorder is the single integration seam between the existing remote-session
code and the persisted timeline. ``RemoteSessionManager`` holds one recorder
instance and funnels every lifecycle/output hook through ``_timeline`` into it.

Contract (must hold for every implementation):
- Every method is **non-blocking and best-effort**: it must never raise to the
  caller. It runs on the ``process_session_output`` hot path, so an exception
  would interrupt stdout handling. ``DbRunRecorder`` resolves attribution on the
  calling thread (cheap, cached) and hands all DB I/O to a background writer, so
  a slow/hung database cannot stall stdout processing. The writer swallows every
  error; a future RemoteApiRecorder would use the same seam with a remote queue.
- ``is_noop`` lets hot-path callers short-circuit before building metadata, so a
  disabled recorder costs essentially nothing.

Attribution is resolved per-run and cached (plan §2.3 source map):
- user_id / model / tool_name  <- agent_sessions (via SessionManager)
- machine_id                   <- session.context["remote_machine_id"]
- tenant_id                    <- remote_machines.tenant_id (per-run cached; NULL
                                   on lookup failure rather than a wrong guess)
- provider                     <- derived from cli_tool
- key_id                       <- NULL phase 1 (not reported by the agent yet)
"""

from __future__ import annotations




import logging
import queue as queue_module
import threading
from collections.abc import Callable
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


_QUEUE_MAXSIZE = 10000
_SHUTDOWN_FLUSH_TIMEOUT = 5.0


class _RunWriter:
    """Abstract sink for DB writes. submit() must never raise to the caller."""

    def submit(self, fn: Callable[[], None]) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def flush(self, timeout: float | None = None) -> bool:  # pragma: no cover - interface
        """Block until all submitted writes are durable (tests / shutdown).

        Returns True if the queue drained within ``timeout`` (or when timeout is
        None). A bounded timeout lets shutdown best-effort flush without hanging.
        """
        return True


class _SyncRunWriter(_RunWriter):
    """Runs writes inline on the calling thread.

    The default for directly-constructed recorders (i.e. unit tests): keeps the
    record-then-assert sequence synchronous with no thread plumbing.
    """

    def submit(self, fn: Callable[[], None]) -> None:
        fn()

    def flush(self, timeout: float | None = None) -> bool:
        return True


class _AsyncRunWriter(_RunWriter):
    """Drains a bounded FIFO queue on a single background daemon thread.

    Used by the production recorder (via ``get_run_recorder``) so the hot path
    only enqueues — DB latency never blocks stdout handling. The worker is the
    sole DB writer, so per-session event ordering is preserved; writes across
    sessions are interleaved but independent.

    Robustness under a wedged database: the queue is bounded (``maxsize``); when
    full, ``submit`` drops the new event (best-effort — losing one event beats
    unbounded memory growth, since each item may carry large metadata) and warns
    at a throttled rate. On shutdown an ``atexit`` flush drains pending writes
    within a timeout so restarts don't lose the tail (daemon thread otherwise
    dies immediately).
    """

    def __init__(self, maxsize: int = _QUEUE_MAXSIZE) -> None:
        import atexit

        self._queue: queue_module.Queue[Callable[[], None]] = queue_module.Queue(maxsize=maxsize)
        self._dropped = 0
        self._thread = threading.Thread(target=self._drain, name="run-timeline-writer", daemon=True)
        self._thread.start()
        atexit.register(self._shutdown_flush)

    def submit(self, fn: Callable[[], None]) -> None:
        try:
            self._queue.put_nowait(fn)
        except queue_module.Full:
            # DB wedged / consumer can't keep up: drop and warn (throttled).
            self._dropped += 1
            if self._dropped == 1 or self._dropped % 1000 == 0:
                logger.warning(
                    "run_timeline: write queue full (>%d); dropped %d event(s)",
                    self._queue.maxsize,
                    self._dropped,
                )

    def flush(self, timeout: float | None = None) -> bool:
        # queue.Queue.join() has no timeout; run it on a throwaway daemon thread
        # joined with our deadline so a wedged worker can't hang shutdown.
        if timeout is None:
            self._queue.join()
            return True
        joiner = threading.Thread(target=self._queue.join, name="run-timeline-flush", daemon=True)
        joiner.start()
        joiner.join(timeout)
        drained = not joiner.is_alive()
        if not drained:
            logger.warning("run_timeline: flush did not drain within %.1fs", timeout)
        return drained

    def _shutdown_flush(self) -> None:
        self.flush(timeout=_SHUTDOWN_FLUSH_TIMEOUT)

    def _drain(self) -> None:
        while True:
            fn = self._queue.get()
            try:
                fn()
            except Exception as e:  # pragma: no cover - contract: writer never raises
                logger.debug("run_timeline: background write failed: %s", e)
            finally:
                self._queue.task_done()


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

    def flush(self, timeout: float | None = None) -> bool:
        """No-op default; only the DB recorder actually has queued work."""
        return True


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
    """Persists runs/events/approvals to the database. Non-blocking.

    Attribution is resolved on the calling thread and cached per run; every DB
    write is handed to ``self._writer`` (a background queue+worker in production,
    inline in tests). Callers therefore never block on the database.
    """

    is_noop = False

    def __init__(
        self,
        repo: RunTimelineRepository | None = None,
        *,
        async_writer: bool = False,
    ):
        self._repo = repo or RunTimelineRepository()
        self._lock = threading.Lock()
        # session_id -> cached attribution dict (tenant_id etc. resolved once)
        self._attr_cache: dict[str, dict[str, Any]] = {}
        # session_ids whose agent_runs row has been ensured
        self._ensured: set[str] = set()
        self._writer: _RunWriter = _AsyncRunWriter() if async_writer else _SyncRunWriter()

    def flush(self, timeout: float | None = None) -> bool:
        """Block until all queued writes are durable."""
        return self._writer.flush(timeout=timeout)

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
        """Resolve a machine's tenant_id. Returns None when unknown.

        A compliance/provenance feature must never silently attribute a run to
        the wrong tenant: when the machine lookup fails we store NULL (matching
        the phase-1 key_id handling) rather than guessing tenant 1.
        """
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
            logger.debug("run_timeline: tenant lookup failed for %s: %s", machine_id[:8], e)
        logger.warning(
            "run_timeline: tenant_id unknown for machine %s; recording NULL", machine_id[:8]
        )
        return None

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
        """Resolve attribution and ensure the run row exists (once per session).

        Attribution is resolved on the calling thread (live state, cached). The
        ensure_run INSERT is enqueued exactly once — guarded by ``_ensured``
        under the lock so concurrent first-events for a session don't double-up
        (the row itself is also idempotent via ``ON CONFLICT DO NOTHING``).
        """
        attr = self._ensure_attribution(session_id)
        need_ensure = False
        with self._lock:
            if session_id not in self._ensured:
                self._ensured.add(session_id)
                need_ensure = True
        if need_ensure:
            self._writer.submit(lambda: self._ensure_run_row(session_id, attr))
        return attr

    def _ensure_run_row(self, session_id: str, attr: dict[str, Any]) -> None:
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
        except Exception:
            # Allow a later event to retry ensuring the run row.
            with self._lock:
                self._ensured.discard(session_id)
            raise

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
            # Run rows keep the precise status (completed/stopped/error/pause/
            # resume); the event stream normalises to its smaller enum so the
            # timeline UI has one badge per lifecycle kind.
            normalized = status if status in ("stop", "error", "pause", "resume") else "stop"
            self._writer.submit(
                lambda: self._repo.update_run_status(session_id, status, ended_at=ended)
            )
            self._emit(session_id, attr, normalized)
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
            # Atomic increment — one UPDATE, no SELECT, no read-modify-write race.
            self._writer.submit(
                lambda: self._repo.increment_run_usage(session_id, total, inp, out, requests)
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
            resolved_id = request_id or f"session:{session_id}:{datetime.utcnow().isoformat()}"
            self._writer.submit(
                lambda: self._repo.upsert_approval_request(
                    request_id=resolved_id,
                    run_id=session_id,
                    session_id=session_id,
                    tool_name=tool_name,
                    request_subtype=subtype,
                    request_details=control_request,
                )
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

            def _write_response(rid: str | None) -> None:
                if not rid:
                    return
                self._repo.update_approval_response(
                    request_id=rid,
                    decision=decision,
                    decided_by=decided_by,
                    decided_by_name=decided_by_name,
                    decision_metadata={"message": message} if message else None,
                )

            rid = request_id
            if not rid:
                # Resolve latest pending synchronously: the fallback join key is
                # needed to emit the event, so it can't be deferred to the worker.
                pending = self._repo.get_latest_pending_approval(session_id)
                rid = pending.request_id if pending else None
            self._writer.submit(lambda: _write_response(rid))
            self._emit(
                session_id,
                attr,
                "permission_answered",
                content=decision,
                metadata={"request_id": rid, "decision": decision, "message": message},
                audit_username=decided_by_name,
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
        audit_username: str | None = None,
    ) -> None:
        self._writer.submit(
            lambda: self._emit_now(
                session_id,
                attr,
                event_type,
                event_subtype=event_subtype,
                role=role,
                content=content,
                tool_name=tool_name,
                provider=provider,
                model=model,
                metadata=metadata,
                audit_username=audit_username,
            )
        )

    def _emit_now(
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
        audit_username: str | None = None,
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
            username=audit_username,
            session_id=session_id,
            details=metadata,
        )


_recorder_lock = threading.Lock()
_recorder_instance: RunRecorder | None = None


def get_run_recorder() -> RunRecorder:
    """Return the process-wide recorder (Db when enabled, Null otherwise).

    The choice is resolved once and cached; flipping the config flag requires a
    restart (same semantics as the autonomous scheduler). The production Db
    recorder uses the background async writer so the hot path never blocks on DB.
    """
    global _recorder_instance
    if _recorder_instance is not None:
        return _recorder_instance
    with _recorder_lock:
        if _recorder_instance is None:
            from app.utils.config import is_run_timeline_enabled

            if is_run_timeline_enabled():
                _recorder_instance = DbRunRecorder(async_writer=True)
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
