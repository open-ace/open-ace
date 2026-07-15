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
  session/resume  {sessionId} -> {messages, projection, runtime, session, settings, ...}
#                  (session.sessionId confirms the resumed id; same shape as create)
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
from pathlib import Path
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
        # Pending server requests waiting for user response (interaction/requestUserInput, interaction/requestPermission)
        self._pending_server_requests: dict[str | int, str] = {}

        # Event polling state: last consumed event seq per session.
        self._last_event_seq = 0
        # Cross-poll hash dedup for events without seq. _prior_event_hashes
        # holds hashes from previous poll cycles; _current_event_hashes
        # accumulates during the current cycle. At the start of each poll,
        # current → prior, current is cleared. This prevents re-delivery of
        # the same streaming delta across polls (including the tail poll)
        # while allowing legitimate same-content deltas within a single batch.
        self._prior_event_hashes: set[str] = set()
        self._current_event_hashes: set[str] = set()
        # Set = no turn in progress (idle); cleared while a turn runs.
        self._turn_done = threading.Event()
        self._turn_done.set()
        # Wall-clock budget for draining events in a single turn. Set by
        # send_message from the caller's timeout; defaults to _TURN_TIMEOUT.
        self._turn_timeout: float = _TURN_TIMEOUT
        self._worker: threading.Thread | None = None
        self.last_send_error: str | None = None

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

    # Built-in provider baseURLs. zcode does not expose these through the
    # model catalog (source="builtin"), so we maintain them here. Only the
    # providers we actually use for autonomous workflows need entries.
    _PROVIDER_BASE_URLS = {
        "zai": "https://api.z.ai/api/anthropic",
        "bigmodel": "https://open.bigmodel.cn/api/anthropic",
    }

    def _build_runtime_model(self) -> dict | None:
        """Build a runtimeModel payload for session/resume.

        This forces zcode's resume handler to call B7 → apt, which clears the
        restoreWarning that would otherwise make session/send reject every
        message with ZCODE_RUNTIME_MODEL_UNAVAILABLE. Returns None when the
        model string cannot be parsed (caller skips the runtimeModel param).

        The provider must include apiKey from ~/.zcode/cli/config.json —
        runtimeModel replaces the config provider entirely, so omitting the
        key causes "Model provider is missing an API key" on the first call.
        """
        if not self.model:
            return None
        model_str = self.model
        if "/" in model_str:
            provider_id, model_id = model_str.split("/", 1)
        else:
            provider_id, model_id = "zai", model_str
        base_url = self._PROVIDER_BASE_URLS.get(provider_id)
        if not base_url:
            logger.warning("Unknown provider %s — cannot build runtimeModel baseURL", provider_id)
            return None
        # Read the API key from config.json so the runtimeModel provider is
        # fully self-contained (zcode uses runtimeModel's provider in place of
        # the config provider for resumed sessions).
        api_key = self._read_provider_api_key(provider_id)
        if not api_key:
            logger.warning(
                "No API key for provider %s in config.json — resumed session "
                "may fail with 'missing an API key'",
                provider_id,
            )
        provider: dict[str, Any] = {
            "providerId": provider_id,
            "kind": "anthropic",
            "source": "builtin",
            "baseURL": base_url,
            "models": [{"modelId": model_id}],
        }
        if api_key:
            provider["apiKey"] = {"source": "inline", "value": api_key}
        return {
            "revision": "0",
            "generatedAt": int(time.time() * 1000),
            "model": {"providerId": provider_id, "modelId": model_id},
            "provider": provider,
        }

    @staticmethod
    def _read_provider_api_key(provider_id: str) -> str | None:
        """Read a provider's API key from ~/.zcode/cli/config.json."""
        try:
            config_path = Path.home() / ".zcode" / "cli" / "config.json"
            with config_path.open() as f:
                config = json.load(f)
            return config.get("provider", {}).get(provider_id, {}).get("options", {}).get("apiKey")
        except (OSError, json.JSONDecodeError, KeyError):
            return None

    def start(
        self,
        model: str | None = None,
        permission_mode: str | None = None,
        resume_session_id: str | None = None,
    ) -> bool:
        """Start the reader thread and create/resume the ZCode session.

        When *resume_session_id* is given (crash recovery), the prior ZCode
        session is resumed via ``session/resume`` so conversation history is
        preserved; otherwise a fresh session is created via ``session/create``.
        Both methods return the same envelope, including a top-level
        ``session`` object whose ``sessionId`` confirms the active id (verified
        against zcode 0.14.5). If resume fails or the response lacks a session
        id, we fall back to ``session/create``.
        """
        self._reader_thread.start()
        workspace = os.path.abspath(os.path.expanduser(self.project_path))
        workspace_param = {"workspacePath": workspace, "workspaceKey": workspace}
        resumed = False

        if resume_session_id:
            # Pass runtimeModel so zcode's session/resume handler calls B7,
            # which clears restoreWarning (ZCODE_RUNTIME_MODEL_UNAVAILABLE).
            # Without this, the resumed session is flagged unavailable because
            # a fresh app-server process has an empty workspaceModelCatalogs;
            # session/send then rejects every message. The runtimeModel must
            # carry the provider baseURL (e.g. https://api.z.ai/api/anthropic
            # for zai) — builtin providers don't expose it via the catalog.
            resume_params = {
                "sessionId": resume_session_id,
                "workspace": workspace_param,
            }
            runtime_model = self._build_runtime_model()
            if runtime_model:
                resume_params["runtimeModel"] = runtime_model
            result = self._request(
                "session/resume",
                resume_params,
                timeout=20.0,
            )
            # Both create and resume return a `session` envelope; the resumed id
            # is confirmed under session.sessionId.
            session_obj = result.get("session") if isinstance(result, dict) else None
            if session_obj and session_obj.get("sessionId"):
                self._cli_session_id = session_obj["sessionId"]
                resumed = True
            else:
                logger.warning(
                    "ZCode session/resume failed for %s, falling back to create",
                    self.session_id[:8],
                )

        if not self._cli_session_id:
            result = self._request("session/create", {"workspace": workspace_param}, timeout=20.0)
            if not result or "session" not in result:
                logger.error("ZCode session/create failed for %s: %s", self.session_id[:8], result)
                return False
            self._cli_session_id = result["session"].get("sessionId")

        if not self._cli_session_id:
            logger.error(
                "ZCode session/create returned no sessionId for %s: %s",
                self.session_id[:8],
                result,
            )
            return False

        # Set the session mode via protocol. The --mode CLI flag is ignored
        # by app-server (sessions always start in "build" mode). build mode
        # auto-approves read-only tools but stalls on write tools
        # (tool-approval-request with no human to answer). session/setMode
        # is the only reliable way to control permission behavior.
        # Verified against zcode 0.14.5: session/setMode {sessionId, mode}
        # immediately changes the session's tool-gate without approval prompts.
        if self.permission_mode:
            setmode_result = self._request(
                "session/setMode",
                {"sessionId": self._cli_session_id, "mode": self.permission_mode},
                timeout=10.0,
            )
            if not setmode_result or "error" in setmode_result:
                logger.warning(
                    "ZCode session/setMode(%s) failed for %s — "
                    "session will run in default build mode: %s",
                    self.permission_mode,
                    self.session_id[:8],
                    setmode_result,
                )
            else:
                logger.info(
                    "ZCode session mode set to %s for %s",
                    self.permission_mode,
                    self.session_id[:8],
                )

        # On resumed sessions, the model from the prior session may be stale
        # (ZCODE_RUNTIME_MODEL_UNAVAILABLE). Re-set the model explicitly so
        # session/send doesn't reject with "model unavailable".
        # session/setModel expects {modelId, providerId} format.
        if resumed and self.model:
            # Parse "glm-5.2" or "zai/glm-5.2" into {modelId, providerId}
            model_str = self.model
            if "/" in model_str:
                provider_id, model_id = model_str.split("/", 1)
            else:
                provider_id, model_id = "zai", model_str
            setmodel_result = self._request(
                "session/setModel",
                {
                    "sessionId": self._cli_session_id,
                    "model": {"modelId": model_id, "providerId": provider_id},
                },
                timeout=10.0,
            )
            if not setmodel_result or "error" in setmodel_result:
                logger.warning(
                    "ZCode session/setModel(%s) failed for %s: %s",
                    self.model,
                    self.session_id[:8],
                    setmodel_result,
                )
            else:
                logger.info(
                    "ZCode session model set to %s for %s",
                    self.model,
                    self.session_id[:8],
                )

        self._created.set()
        logger.info(
            "ZCode session ready for %s (cli session: %s, resumed=%s)",
            self.session_id[:8],
            self._cli_session_id[:8],
            resumed,
        )
        return True

    def _recreate_fresh_session(self) -> bool:
        """Create a fresh session (no resume) when the resumed session is
        unusable (e.g. stale model binding). Re-applies setMode + setModel.
        Returns True if the new session is ready for session/send.

        Must be called from the turn worker thread (before
        ``_drain_events_until_idle``). The new session has no conversation
        history, so prior context is lost — callers that can surface this to
        the user (e.g. ``_run_turn``) emit a visible warning after fallback.
        ``_cli_session_id`` is mutated under ``_lock`` because the reader
        thread and ``stop()`` read it concurrently.
        """
        workspace_param = {
            "workspacePath": self.project_path,
            "workspaceKey": self.project_path,
        }
        result = self._request("session/create", {"workspace": workspace_param}, timeout=20.0)
        session_obj = result.get("session") if isinstance(result, dict) else None
        if not (session_obj and session_obj.get("sessionId")):
            logger.error("ZCode fallback session/create failed for %s", self.session_id[:8])
            return False
        with self._lock:
            self._cli_session_id = session_obj["sessionId"]
            # Reset dedup/seq state so events from the fresh session are not
            # dropped as duplicates of the poisoned session's stream. This
            # mirrors the reset at the top of _run_turn; it is repeated here
            # because the new session's event seq restarts from 0 and the
            # poisoned session's accumulated _last_event_seq must be cleared.
            self._last_event_seq = 0
            self._prior_event_hashes = set()
            self._current_event_hashes = set()
        logger.info(
            "ZCode fresh session created for %s (cli session: %s) — prior context lost",
            self.session_id[:8],
            session_obj["sessionId"][:8],
        )

        # Re-apply mode and model on the fresh session. A setMode failure here
        # leaves the session in build mode (write tools stall), so warn rather
        # than silently succeed.
        if self.permission_mode:
            setmode_result = self._request(
                "session/setMode",
                {"sessionId": self._cli_session_id, "mode": self.permission_mode},
                timeout=10.0,
            )
            if not setmode_result or "error" in setmode_result:
                logger.warning(
                    "ZCode fresh session/setMode(%s) failed for %s: %s",
                    self.permission_mode,
                    self.session_id[:8],
                    setmode_result,
                )
        if self.model:
            # Always bind the model here, even though start() only binds on
            # `resumed` sessions. The fresh session was created precisely
            # because the old session's model binding was unavailable; leaving
            # it unbound would re-trip MODEL_UNAVAILABLE on the next send.
            model_str = self.model
            if "/" in model_str:
                provider_id, model_id = model_str.split("/", 1)
            else:
                provider_id, model_id = "zai", model_str
            setmodel_result = self._request(
                "session/setModel",
                {
                    "sessionId": self._cli_session_id,
                    "model": {"modelId": model_id, "providerId": provider_id},
                },
                timeout=10.0,
            )
            if not setmodel_result or "error" in setmodel_result:
                logger.warning(
                    "ZCode fresh session/setModel(%s) failed for %s: %s",
                    self.model,
                    self.session_id[:8],
                    setmodel_result,
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

    def send_message(self, content: str, timeout: float = _TURN_TIMEOUT) -> bool:
        """Send a user message and return immediately (non-blocking).

        ``session/send`` is asynchronous. We dispatch it on a dedicated worker
        thread so the caller (the agent's single command-dispatch thread) is not
        blocked - heartbeats and other commands (stop/pause/permission) keep
        flowing while the turn runs. The worker streams assistant events through
        the existing callbacks and signals completion via ``_turn_done``.

        *timeout* is the wall-clock budget for the worker to drain events from
        this turn (passed through to ``_run_turn``). It should match the
        ``wait_turn`` timeout so the worker does not give up before the caller.
        """
        if not self._cli_session_id or not self.is_running:
            self.last_send_error = "ZCode session is not active"
            logger.warning("Cannot send to inactive ZCode session %s", self.session_id[:8])
            return False

        if not self._turn_done.is_set():
            self.last_send_error = "A turn is already in progress for this session"
            logger.warning("ZCode session %s already has a turn in progress", self.session_id[:8])
            return False

        self.last_send_error = None
        self._turn_done.clear()
        self._turn_timeout = timeout
        self._worker = threading.Thread(
            target=self._run_turn,
            args=(content,),
            name=f"zcode-turn-{self.session_id[:8]}",
            daemon=True,
        )
        self._worker.start()
        return True

    def wait_turn(self, timeout: float = _TURN_TIMEOUT) -> bool:
        """Block until the current turn finishes (used by tests)."""
        return self._turn_done.wait(timeout=timeout)

    def _run_turn(self, content: str) -> None:
        """Worker-thread body: send the message and stream events until done."""
        # Reset per-turn dedup state so hashes from prior turns don't block
        # legitimate re-delivery of identical content in a new turn.
        self._prior_event_hashes = set()
        self._current_event_hashes = set()
        self._last_event_seq = 0
        try:
            send_result = self._request(
                "session/send",
                {"sessionId": self._cli_session_id, "content": content},
                timeout=30.0,
            )
            # If the model is unavailable on a resumed session, create a fresh
            # session and retry. This happens when the resumed session's model
            # binding is stale (ZCODE_RUNTIME_MODEL_UNAVAILABLE) and
            # session/setModel didn't fix it (the session itself is polluted).
            if isinstance(send_result, dict) and "error" in send_result:
                err_code = ""
                err_data = send_result.get("error", {}).get("data", {})
                if isinstance(err_data, dict):
                    err_code = err_data.get("code", "")
                if err_code == "ZCODE_RUNTIME_MODEL_UNAVAILABLE":
                    logger.warning(
                        "ZCode session/send failed with MODEL_UNAVAILABLE for %s — "
                        "creating fresh session and retrying",
                        self.session_id[:8],
                    )
                    if self._recreate_fresh_session():
                        # Surface the context loss to the orchestrator layer so
                        # the resulting plan/dev is understood to be produced
                        # without prior conversation history.
                        self.output_callback(
                            self.session_id,
                            json.dumps(
                                {
                                    "type": "warning",
                                    "data": {
                                        "message": (
                                            "Resumed ZCode session was unavailable; "
                                            "retried in a fresh session without prior context."
                                        )
                                    },
                                }
                            ),
                            "stderr",
                            False,
                        )
                        send_result = self._request(
                            "session/send",
                            {"sessionId": self._cli_session_id, "content": content},
                            timeout=30.0,
                        )
                        # If the fresh session *also* hits MODEL_UNAVAILABLE,
                        # this is an account/config-level problem (not single
                        # session pollution). Flag it distinctly so ops can
                        # tell it apart from an ordinary send rejection.
                        if isinstance(send_result, dict) and "error" in send_result:
                            retry_data = send_result.get("error", {}).get("data", {})
                            retry_code = (
                                retry_data.get("code", "") if isinstance(retry_data, dict) else ""
                            )
                            if retry_code == "ZCODE_RUNTIME_MODEL_UNAVAILABLE":
                                logger.error(
                                    "ZCode MODEL_UNAVAILABLE persists on fresh session "
                                    "for %s — model %s likely unavailable at account level, "
                                    "not session pollution",
                                    self.session_id[:8],
                                    self.model,
                                )

            if not send_result or not send_result.get("accepted"):
                # Surface send failures (e.g. model unavailable) to the user via
                # the error stream rather than failing silently.
                err_msg = "ZCode rejected the message"
                if isinstance(send_result, dict):
                    err = send_result.get("error") or {}
                    err_msg = err.get("message") or err_msg
                logger.error(
                    "ZCode session/send rejected for %s: %s",
                    self.session_id[:8],
                    send_result,
                )
                self.output_callback(
                    self.session_id,
                    json.dumps({"type": "error", "data": {"message": err_msg}}),
                    "stderr",
                    False,
                )
                return
            self._drain_events_until_idle(self._turn_timeout)
            self._report_usage()
        except Exception:  # noqa: BLE001
            logger.exception("ZCode turn failed for %s", self.session_id[:8])
            self.output_callback(
                self.session_id,
                json.dumps({"type": "error", "data": {"message": "ZCode turn failed"}}),
                "stderr",
                False,
            )
        finally:
            self.output_callback(self.session_id, "", "stdout", True)
            self._turn_done.set()

    def _drain_events_until_idle(self, timeout: float) -> None:
        """Poll session/events, forwarding assistant content, until the turn ends.

        The reliable completion signal is a ``turn.completed`` event (emitted once
        the agent finishes its response). Polling backs off from 0.4s up to 2s so
        long turns don't hammer the app-server.
        """
        deadline = time.monotonic() + timeout
        interval = _EVENTS_POLL_INTERVAL
        while time.monotonic() < deadline and not self._stopped.is_set():
            # Rotate hash dedup sets: prior ← current, clear current.
            # This allows same-content events in THIS poll batch to be
            # forwarded, while blocking re-delivery from prior batches.
            self._prior_event_hashes = self._current_event_hashes
            self._current_event_hashes = set()
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
                tail = self._request(
                    "session/events", {"sessionId": self._cli_session_id}, timeout=10.0
                )
                if tail:
                    self._forward_events(tail.get("events", []) or [])
                return
            time.sleep(interval)
            interval = min(interval * 1.5, 2.0)
        logger.warning(
            "ZCode turn did not complete within %.0fs for %s",
            timeout,
            self.session_id[:8],
        )

    def _forward_events(self, events: list[dict[str, Any]]) -> bool:
        """Forward new events to the output callback. Returns True if turn done.

        Two-layer dedup: (1) seq-based for events that carry a monotonic seq,
        (2) cross-poll-cycle hash for events without seq (e.g. model.streaming
        deltas). The hash set persists across poll cycles within a turn but
        is NOT cleared between forward calls — so the first occurrence of a
        given delta in any poll is forwarded, but re-deliveries in subsequent
        polls (including the tail poll) are skipped. Legitimate same-content
        deltas within a single poll batch are still forwarded because the hash
        is checked against prior cycles, not the current batch.
        """
        done = False
        for ev in events:
            seq = ev.get("seq", 0)
            if seq and seq <= self._last_event_seq:
                continue
            if seq:
                self._last_event_seq = seq
            # Cross-poll hash dedup for events without seq (streaming deltas).
            # Only skip if we've seen this EXACT event in a PRIOR poll cycle.
            ev_hash = json.dumps(ev, sort_keys=True, default=str)
            if ev_hash in self._prior_event_hashes:
                continue
            self._current_event_hashes.add(ev_hash)
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
        """Route a parsed message: response (has id) vs notification (no id).

        Also handles server→client requests (messages with both ``id`` and
        ``method``): these require a response back on stdin. The key case is
        ``interaction/requestUserInput`` (triggered by the AskUserQuestion tool
        in plan mode). In an unattended workflow nobody is present to answer,
        so we auto-decline with a clear reason — the agent receives the deny
        decision and proceeds using its own judgment instead of stalling
        forever waiting for input that never arrives.
        """
        msg_id = msg.get("id")
        method = msg.get("method")
        if msg_id is not None and method is not None:
            # Server→client request: respond automatically.
            self._handle_server_request(msg_id, method, msg.get("params", {}))
            return
        if msg_id is not None:
            # Response to a pending request we sent.
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
        if method == "state.updated":
            # Inject as an event-like dict so _forward_one_event handles it.
            self._forward_one_event({"type": "state.updated", "payload": msg.get("params", {})})

    def _handle_server_request(self, msg_id: str, method: str, params: dict[str, Any]) -> None:
        """Handle server→client requests.

        In user-interactive mode (permission_callback set), forward requests
        to the frontend for human decision. In unattended mode, auto-respond.
        """
        # User-interactive mode: forward to frontend for human decision
        if method == "interaction/requestUserInput" and self.permission_callback:
            logger.info(
                "ZCode interaction/requestUserInput forwarded to frontend for %s",
                self.session_id[:8],
            )
            # Store pending request for later response
            with self._lock:
                self._pending_server_requests[msg_id] = method
            # Forward to frontend via permission_callback
            self.permission_callback(self.session_id, {
                "type": "interaction_request",
                "method": method,
                "id": msg_id,
                "params": params,
            })
            return

        if method == "interaction/requestPermission" and self.permission_callback:
            tool_name = params.get("toolName", "?")
            logger.info(
                "ZCode interaction/requestPermission forwarded to frontend for %s (tool=%s)",
                self.session_id[:8],
                tool_name,
            )
            # Store pending request for later response
            with self._lock:
                self._pending_server_requests[msg_id] = method
            # Forward to frontend via permission_callback
            self.permission_callback(self.session_id, {
                "type": "interaction_request",
                "method": method,
                "id": msg_id,
                "params": params,
            })
            return

        # Unattended mode: auto-respond
        if method == "interaction/requestUserInput":
            # AskUserQuestion (or similar) is waiting for a human answer.
            # Decline so the agent knows to proceed with its own judgment.
            logger.info(
                "ZCode interaction/requestUserInput auto-declined for %s "
                "(unattended workflow) — agent should proceed with best judgment",
                self.session_id[:8],
            )
            self._send_response(
                msg_id,
                {
                    "action": "decline",
                    "reason": (
                        "This is an unattended autonomous workflow. No human is "
                        "available to answer. Use your best judgment and proceed."
                    ),
                },
            )
            return
        if method == "interaction/requestPermission":
            # A tool call needs permission approval. In unattended mode there
            # is no human to approve/deny — auto-allow so the agent can proceed.
            # plan mode already restricts the tool set to read-only tools; if a
            # permission request still fires (e.g. reading outside cwd), the
            # safest option for an autonomous workflow is to allow it rather
            # than stall the turn indefinitely.
            tool_name = params.get("toolName", "?")
            logger.info(
                "ZCode interaction/requestPermission auto-allowed for %s "
                "(tool=%s, unattended workflow)",
                self.session_id[:8],
                tool_name,
            )
            self._send_response(msg_id, {"decision": "allow"})
            return
        # Unknown server request: respond with a generic error so it doesn't
        # hang forever.
        logger.warning("ZCode unhandled server request %s for %s", method, self.session_id[:8])
        self._send_response(
            msg_id,
            None,
            error={
                "code": -32601,
                "message": f"Unhandled server request: {method}",
            },
        )

    def send_interaction_response(self, msg_id: str, response: dict[str, Any]) -> bool:
        """Send a user's response back to the ZCode app-server.

        Args:
            msg_id: The message ID from the original interaction request.
            response: The user's response (action: answer/decline or decision: allow/deny).

        Returns:
            True if the response was sent successfully, False otherwise.
        """
        with self._lock:
            if msg_id not in self._pending_server_requests:
                logger.warning(
                    "Unknown interaction request %s for %s",
                    msg_id[:8] if msg_id else "N/A",
                    self.session_id[:8],
                )
                return False
            del self._pending_server_requests[msg_id]

        logger.info(
            "Sending interaction response for %s, msg_id=%s",
            self.session_id[:8],
            msg_id[:8] if msg_id else "N/A",
        )
        self._send_response(msg_id, response)
        return True

    def _send_response(self, msg_id: str, result: Any, error: dict[str, Any] | None = None) -> None:
        """Write a server→client response back to the app-server stdin."""
        if not self.is_running:
            return
        msg: dict[str, Any] = {"id": msg_id}
        if error is not None:
            msg["error"] = error
        else:
            msg["result"] = result
        try:
            self.process.stdin.write(json.dumps(msg) + "\n")
            self.process.stdin.flush()
        except (OSError, BrokenPipeError):
            logger.debug("ZCode stdin write failed for %s", self.session_id[:8])

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
            # Return the error dict (not None) so callers can inspect the error
            # code and decide whether to retry with a different strategy (e.g.
            # MODEL_UNAVAILABLE → fresh session).
            return {"error": holder["error"]}
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
