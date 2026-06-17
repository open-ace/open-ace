"""
Open ACE Remote Agent - ZCode app-server persistent session.

Drives the ZCode CLI in persistent ``app-server`` mode over stdio. The app-server
speaks the *ZCode Protocol*: newline-delimited JSON where each message is either

  * a *request*  with an ``id`` field  -> ``{id, method, params}``,
  * a *response* with an ``id`` field  -> ``{id, result}`` or ``{id, error}``, or
  * a *notification* (no ``id``)       -> ``{method, params}`` pushed asynchronously
    (e.g. ``state.updated`` as the agent runs).

A single long-lived ``node <engine> app-server`` process backs one agent session.
On start we ``session/create`` a workspace-scoped session; each user turn is a
``session/send`` (which returns immediately with ``accepted:true`` while the agent
works in the background). A reader thread drains stdout, dispatching responses to
per-request futures and forwarding notifications + assistant events to the same
``output_callback`` / ``usage_callback`` the generic ``SessionProcess`` uses, so
the rest of the pipeline (agent.py -> server SSE) is unchanged.

Verified protocol surface (against zcode 0.14.5):
  session/create  {workspace:{workspacePath,workspaceKey}} -> sessionId
  session/send    {sessionId, content} -> {accepted, stateRevision}  (async)
  session/events  {sessionId} -> {events:[...]}                      (poll)
  session/usage   {sessionId} -> {totalTokens, inputTokens, ...}     (camelCase)
  session/resume  {sessionId} -> {messages, projection, runtime}
  session/list    {} -> {sessions:[...]}
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import threading
import time
import uuid
from typing import Any, Callable

logger = logging.getLogger(__name__)

# Default interval for polling session/events while a turn is running.
_EVENTS_POLL_INTERVAL = 0.4
# How long to wait for a turn to finish (session/send is async).
_TURN_TIMEOUT = 600.0


class ZCodeAppServerSession:
    """A persistent ZCode app-server process backing one agent session.

    Mimics the subset of ``SessionProcess`` that ``ProcessExecutor`` and
    ``agent.py`` rely on: ``send_message``, ``stop``, ``is_running``, ``pid``,
    plus the same callback signatures so output/usage flow identically.
    """

    def __init__(
        self,
        session_id: str,
        process: subprocess.Popen,
        project_path: str,
        output_callback: Callable[[str, str, str, bool], None],
        usage_callback: Callable[[str, dict[str, int]], None] | None = None,
        permission_callback: Callable[[str, dict], None] | None = None,
        model: str | None = None,
        permission_mode: str | None = None,
        env: dict[str, str] | None = None,
    ):
        self.session_id = session_id
        self.process = process
        self.project_path = project_path
        self.cli_tool = "zcode"
        self.output_callback = output_callback
        self.usage_callback = usage_callback
        self.permission_callback = permission_callback
        self.model = model
        self.permission_mode = permission_mode
        self.env = env
        self.allowed_tools: list[str] = []
        self._paused = False  # parity with SessionProcess for meta persistence
        self._restart_lock = threading.Lock()  # parity with SessionProcess

        # ZCode's internal sessionId (sess_...), captured after session/create.
        self._cli_session_id: str | None = None
        self._created = threading.Event()
        self._stopped = threading.Event()

        # Request/response correlation: id -> (Event, result holder).
        self._lock = threading.Lock()
        self._pending: dict[str | int, dict[str, Any]] = {}

        # Event polling state: last consumed event seq per session.
        self._last_event_seq = 0
        self._turn_done = threading.Event()

        self._reader_thread = threading.Thread(
            target=self._read_loop,
            name=f"zcode-{session_id[:8]}",
            daemon=True,
        )

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    @property
    def pid(self) -> int | None:
        return self.process.pid if self.process.returncode is None else None

    @property
    def is_running(self) -> bool:
        return self.process.returncode is None

    def start(self, model: str | None = None, permission_mode: str | None = None) -> bool:
        """Start the reader thread and create the ZCode session."""
        self._reader_thread.start()
        workspace = os.path.abspath(os.path.expanduser(self.project_path))
        create_params: dict[str, Any] = {
            "workspace": {"workspacePath": workspace, "workspaceKey": workspace},
        }
        # Build a fallback config so app-server can reach a model provider even
        # without a pre-existing ~/.zcode/cli/config.json on the remote machine.
        result = self._request("session/create", create_params, timeout=20.0)
        if not result or "session" not in result:
            logger.error("ZCode session/create failed for %s: %s", self.session_id[:8], result)
            return False
        self._cli_session_id = result["session"].get("sessionId")
        if not self._cli_session_id:
            logger.error("ZCode session/create returned no sessionId for %s", self.session_id[:8])
            return False
        self._created.set()
        logger.info(
            "ZCode session created for %s (cli session: %s)",
            self.session_id[:8],
            self._cli_session_id[:8],
        )
        return True

    def start_readers(self) -> None:  # noqa: D401 - parity with SessionProcess API
        """No-op: the reader is started in ``start``.

        Kept for API parity so ``ProcessExecutor`` can call the same method name.
        """
        return None

    # ------------------------------------------------------------------ #
    # Sending a user turn
    # ------------------------------------------------------------------ #

    def send_message(self, content: str) -> bool:
        """Send a user message and block until the agent turn completes.

        ``session/send`` is asynchronous (returns ``accepted`` immediately), so we
        poll ``session/events`` until the projection flips back to ``idle`` and
        then fetch final usage. Assistant text/tool events are forwarded to the
        output callback as they arrive so the frontend streams in real time.
        """
        if not self._cli_session_id or not self.is_running:
            logger.warning("Cannot send to inactive ZCode session %s", self.session_id[:8])
            return False

        self._turn_done.clear()
        send_result = self._request(
            "session/send",
            {"sessionId": self._cli_session_id, "content": content},
            timeout=30.0,
        )
        if not send_result or not send_result.get("accepted"):
            logger.error("ZCode session/send rejected for %s: %s", self.session_id[:8], send_result)
            return False

        # Poll events until the turn finishes (status returns to idle) or timeout.
        self._drain_events_until_idle(_TURN_TIMEOUT)

        # Report final token usage for this turn.
        self._report_usage()
        # Signal stdout EOF so the SSE stream flushes a turn boundary.
        self.output_callback(self.session_id, "", "stdout", True)
        return True

    def _drain_events_until_idle(self, timeout: float) -> None:
        """Poll session/events, forwarding assistant content, until the turn ends.

        The reliable completion signal is a ``turn.completed`` event (emitted once
        the agent finishes its response). We also accept a ``state.updated``
        notification with ``status: idle`` as a fallback.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not self._stopped.is_set():
            result = self._request(
                "session/events",
                {"sessionId": self._cli_session_id},
                timeout=10.0,
            )
            if not result:
                break
            events = result.get("events", []) or []
            completed = self._forward_events(events)
            if completed:
                # Drain any trailing events (e.g. final session.updated).
                tail = self._request(
                    "session/events", {"sessionId": self._cli_session_id}, timeout=10.0
                )
                if tail:
                    self._forward_events(tail.get("events", []) or [])
                self._turn_done.set()
                return
            time.sleep(_EVENTS_POLL_INTERVAL)
        logger.warning(
            "ZCode turn did not complete within %.0fs for %s",
            timeout,
            self.session_id[:8],
        )
        self._turn_done.set()

    def _forward_events(self, events: list[dict[str, Any]]) -> bool:
        """Forward new events to the output callback. Returns True if turn done."""
        done = False
        for ev in events:
            seq = ev.get("seq", 0)
            if seq and seq <= self._last_event_seq:
                continue
            if seq:
                self._last_event_seq = seq
            done = self._forward_one_event(ev) or done
        return done

    def _forward_one_event(self, ev: dict[str, Any]) -> bool:
        """Translate a single ZCode event into the pipeline's stream-json shape.

        Verified event types (zcode 0.14.5):
          model.streaming    -> assistant text deltas (payload.delta)
          turn.completed     -> turn finished (reliable completion signal)
          turn.started       -> informational
          session.updated    -> may carry usage/content; informational
          session.titleUpdated -> informational
          tool.*             -> tool call/result
        """
        etype = ev.get("type", "")
        payload = ev.get("payload", {}) or {}

        # Streaming assistant text delta.
        if etype == "model.streaming":
            delta = payload.get("delta")
            if delta:
                msg = {"type": "assistant", "message": {"role": "assistant", "content": delta}}
                self.output_callback(self.session_id, json.dumps(msg), "stdout", False)
            return False

        # Turn finished — the authoritative completion signal. Carries final usage.
        if etype == "turn.completed":
            usage = payload.get("usage") or {}
            if usage and self.usage_callback:
                self.usage_callback(
                    self.session_id,
                    {
                        "input": usage.get("inputTokens", 0),
                        "output": usage.get("outputTokens", 0),
                        "cache_read": usage.get("cacheReadTokens", 0),
                        "reasoning": usage.get("reasoningTokens", 0),
                        "model_requests": usage.get("modelRequestCount", 0),
                    },
                )
            return True

        # session.updated may carry the final content + usage block.
        if etype == "session.updated":
            usage = payload.get("usage")
            if usage and self.usage_callback:
                self.usage_callback(
                    self.session_id,
                    {
                        "input": usage.get("inputTokens", 0),
                        "output": usage.get("outputTokens", 0),
                        "cache_read": (
                            usage.get("cacheReadTokens", 0)
                            if isinstance(usage.get("cacheReadTokens"), int)
                            else 0
                        ),
                    },
                )
            return False

        # state.updated (from notifications) — emit request_state; idle ends turn.
        if etype == "state.updated":
            patch = payload.get("patch", {}) or {}
            status = patch.get("status")
            if status:
                self.output_callback(
                    self.session_id,
                    json.dumps({"type": "request_state", "data": {"status": status}}),
                    "stdout",
                    False,
                )
            return status == "idle"

        # Tool events.
        if etype.startswith("tool."):
            if etype == "tool.permission_request" and self.permission_callback:
                self.permission_callback(self.session_id, payload)
            else:
                self.output_callback(
                    self.session_id,
                    json.dumps({"type": etype, "data": payload}),
                    "stdout",
                    False,
                )
            return False

        # Informational events we can ignore.
        if etype in {"turn.started", "session.titleUpdated", "session.resumed"}:
            return False

        # Generic fallback for any future event type.
        self.output_callback(
            self.session_id,
            json.dumps({"type": etype or "zcode_event", "data": payload}),
            "stdout",
            False,
        )
        return False

        # Tool call events -> permission or info lines.
        if etype in {"tool.call", "tool.result", "permission.request"}:
            if etype == "permission.request" and self.permission_callback:
                self.permission_callback(self.session_id, payload)
            else:
                self.output_callback(
                    self.session_id,
                    json.dumps({"type": etype, "data": payload}),
                    "stdout",
                    False,
                )
            return False

        # Generic fallback: forward raw event for completeness.
        self.output_callback(
            self.session_id,
            json.dumps({"type": etype or "zcode_event", "data": payload}),
            "stdout",
            False,
        )
        return False

    def _report_usage(self) -> None:
        if not self.usage_callback or not self._cli_session_id:
            return
        usage = self._request("session/usage", {"sessionId": self._cli_session_id}, timeout=10.0)
        if not usage:
            return
        self.usage_callback(
            self.session_id,
            {
                "input": usage.get("inputTokens", 0),
                "output": usage.get("outputTokens", 0),
                "cache_read": usage.get("cacheReadTokens", 0),
                "reasoning": usage.get("reasoningTokens", 0),
                "model_requests": usage.get("modelRequestCount", 0),
            },
        )

    # ------------------------------------------------------------------ #
    # Stdio reader + request/response correlation
    # ------------------------------------------------------------------ #

    def _read_loop(self) -> None:
        """Read stdout lines, dispatch responses to waiters, forward notifications."""
        stream = self.process.stdout
        if stream is None:
            return
        try:
            for raw in stream:
                if self._stopped.is_set():
                    break
                line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    # Forward non-JSON lines (rare diagnostics) to the callback.
                    self.output_callback(self.session_id, line, "stdout", False)
                    continue
                self._handle_message(msg)
        except (OSError, ValueError) as e:
            if not self._stopped.is_set():
                logger.debug("ZCode reader ended for %s: %s", self.session_id[:8], e)
        finally:
            if not self._stopped.is_set():
                self.output_callback(self.session_id, "", "stdout", True)

    def _handle_message(self, msg: dict[str, Any]) -> None:
        """Route a parsed message: response (has id) vs notification (no id)."""
        msg_id = msg.get("id")
        if msg_id is not None:
            # Response to a pending request.
            with self._lock:
                holder = self._pending.pop(msg_id, None)
            if holder is not None:
                if "error" in msg:
                    holder["error"] = msg["error"]
                else:
                    holder["result"] = msg.get("result")
                holder["event"].set()
            return
        # Notification (no id): forward as a live event if it carries state.
        method = msg.get("method", "")
        params = msg.get("params", {}) or {}
        if method == "state.updated":
            # Inject as an event-like dict so _forward_one_event handles it.
            self._forward_one_event({"type": "state.updated", "payload": params})

    def _request(
        self, method: str, params: dict[str, Any], timeout: float = 30.0
    ) -> dict[str, Any] | None:
        """Send a request and wait for its correlated response."""
        if not self.is_running:
            return None
        msg_id = str(uuid.uuid4())
        holder: dict[str, Any] = {"event": threading.Event()}
        with self._lock:
            self._pending[msg_id] = holder
        payload = json.dumps({"id": msg_id, "method": method, "params": params}) + "\n"
        try:
            self.process.stdin.write(payload.encode("utf-8"))
            self.process.stdin.flush()
        except (OSError, BrokenPipeError, AttributeError) as e:
            with self._lock:
                self._pending.pop(msg_id, None)
            logger.error("ZCode request %s failed for %s: %s", method, self.session_id[:8], e)
            return None
        if not holder["event"].wait(timeout=timeout):
            with self._lock:
                self._pending.pop(msg_id, None)
            logger.warning("ZCode request %s timed out for %s", method, self.session_id[:8])
            return None
        if "error" in holder:
            logger.warning(
                "ZCode request %s errored for %s: %s", method, self.session_id[:8], holder["error"]
            )
            return None
        return holder.get("result")

    # ------------------------------------------------------------------ #
    # Teardown
    # ------------------------------------------------------------------ #

    def stop(self) -> None:
        """Terminate the app-server process."""
        if self._stopped.is_set():
            return
        self._stopped.set()
        try:
            if self.process.returncode is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
        except Exception as e:  # noqa: BLE001
            logger.debug("Error stopping ZCode session %s: %s", self.session_id[:8], e)

    def pause(self) -> None:
        """Pause the process (SIGSTOP) for the pause/resume feature."""
        if self.is_running and os.name != "nt" and self.process.pid:
            try:
                os.kill(self.process.pid, signal.SIGSTOP)
            except OSError:
                pass

    def resume(self) -> None:
        """Resume the process (SIGCONT)."""
        if self.is_running and os.name != "nt" and self.process.pid:
            try:
                os.kill(self.process.pid, signal.SIGCONT)
            except OSError:
                pass

    def wait_for_exit(self, timeout: float = 1.0) -> int | None:
        try:
            return self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None

    # Parity helpers expected by the executor's restart path.
    def send_sdk_init(self) -> bool:  # noqa: D401
        return True  # No SDK control plane; session/create handles init.

    def wait_sdk_initialized(self, timeout: float = 15.0) -> bool:  # noqa: D401
        return self._created.wait(timeout=timeout)
