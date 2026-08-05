"""RemoteMachineProvider — SandboxProvider over autonomous remote execution (#2022 P4).

Wraps the autonomous remote-agent execution surface (``RemoteSessionManager``:
``create_remote_session`` / ``send_message`` / ``get_session_status`` /
``stop_session``) behind the :class:`SandboxProvider` contract, so the
orchestrator treats local and remote uniformly through create/exec/stream/stop/
destroy. The CLI-protocol specifics (``_normalize_remote_messages``, message
collection via the local ``session_manager``) stay in ``agent_runner`` and
consume this provider's lifecycle.

Scope (#2022 comment 2026-07-26): autonomous-only. This provider MUST NOT
change ``app/routes/remote.py`` or the ordinary remote-session lifecycle in
``remote_session_manager.py`` — it only calls the manager's existing methods for
the autonomous remote-agent path.

Remote has no local ``Popen``: ``exec`` maps to ``create_remote_session`` +
``send_message``, ``stream`` polls ``get_session_status``, ``stop`` maps to
``stop_session``. There is no ``get_process`` (that Legacy-only escape hatch
does not apply). ``pause``/``resume`` are best-effort no-ops (a remote CLI
session has no SIGSTOP analogue); the wall-clock timeout stays runner-side via
``_wait_for_completion`` until a follow-up binds it to ``exec_policy``.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.modules.workspace.autonomous.command_evidence.types import (
    CommandExecutionEvidence,
    TerminalReason,
    derive_terminal_reason,
)
from app.modules.workspace.autonomous.sandbox.provider import (
    SandboxError,
    validate_spec_capabilities,
)
from app.modules.workspace.autonomous.sandbox.types import (
    ExecHandle,
    SandboxCapability,
    SandboxEvent,
    SandboxEventKind,
    SandboxHandle,
    SandboxSpec,
    SandboxStatus,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.modules.workspace.remote_session_manager import RemoteSessionManager

# Remote provides NO verifiable isolation today (#2078 review P1#1): the
# remote-agent executor (remote-agent/executor.py::_build_env) launches the CLI
# from ``dict(os.environ)`` with a plain Popen — no per-task HOME/TMP/XDG, no
# openace-run-as ACL, no cgroup quota, and the proxy-token injection does not
# scrub the rest of the inherited env (so CREDENTIAL_TOKEN_BINDING is not
# honestly held either). Declaring these would let a spec require them and have
# Remote silently accept without enforcing — a fail-closed violation. Remote
# therefore declares an empty set: a spec requiring ANY isolation cap fails
# closed here (current autonomous remote specs require none, so this is honest
# and non-breaking). When a remote deployment gains verifiable isolation it
# should be reported via a machine capability handshake, not a static constant.
_REMOTE_CAPS: frozenset[SandboxCapability] = frozenset()


@dataclass(frozen=True)
class RemoteTurnSpec:
    """Per-turn invocation params for a remote agent execution (#2022 P4).

    Carried via ``exec(exec_policy=...)`` (the P1-reserved ``Any`` slot): the
    remote ``exec`` is ``create_remote_session`` + ``send_message(prompt)``, so
    the prompt + model/permission/allowed_tools travel here rather than through
    ``command`` (which is Legacy's argv shape and unused for remote).
    """

    prompt: str
    model: str = ""
    permission_mode: str = "auto-edit"
    allowed_tools: tuple[str, ...] | None = None


class RemoteMachineProvider:
    """SandboxProvider over autonomous remote-agent execution."""

    def __init__(
        self,
        remote_session_manager: RemoteSessionManager,
        *,
        poll_interval: float = 5.0,
        poll_timeout: float = 3600.0,
    ) -> None:
        self._rsm = remote_session_manager
        self._status: dict[str, SandboxStatus] = {}
        # sandbox_id -> remote_session_id (the manager's row id). command_id on
        # the ExecHandle IS the remote_session_id so stop/destroy map directly.
        self._remote_sid: dict[str, str] = {}
        # sandbox_id -> last exit_code observed by stream()'s poll, so
        # collect_execution_evidence reports the real outcome (#2078 review 🟡B)
        # instead of a hardcoded 0. Prod remote does not call stream() today
        # (the runner has its own poll loop); this fills when stream runs.
        self._last_exit_code: dict[str, int] = {}
        # sandbox_id -> the terminal event kind stream() reached (COMMAND_COMPLETED
        # / COMMAND_TIMED_OUT / SANDBOX_ERROR), so collect_execution_evidence maps
        # to an honest terminal_reason instead of assuming completed (#2078 P1#3).
        self._last_terminal_kind: dict[str, SandboxEventKind] = {}
        self._poll_interval = poll_interval
        self._poll_timeout = poll_timeout

    def capabilities(self) -> frozenset[SandboxCapability]:
        return _REMOTE_CAPS

    def create(self, spec: SandboxSpec) -> SandboxHandle:
        validate_spec_capabilities(_REMOTE_CAPS, spec)
        if not spec.machine_id:
            raise SandboxError("RemoteMachineProvider requires spec.machine_id")
        if spec.user_id is None:
            raise SandboxError("RemoteMachineProvider requires spec.user_id")
        sandbox_id = uuid.uuid4().hex
        self._status[sandbox_id] = SandboxStatus.CREATED
        return SandboxHandle(
            sandbox_id=sandbox_id,
            generation=1,
            provider_name="remote_machine",
            spec=spec,
            initial_status=SandboxStatus.CREATED,
        )

    def exec(
        self,
        handle: SandboxHandle,
        command: list[str],
        env: dict[str, str] | None,
        exec_policy: Any | None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> ExecHandle:
        if not isinstance(exec_policy, RemoteTurnSpec):
            raise SandboxError("remote exec requires a RemoteTurnSpec as exec_policy")
        spec = handle.spec
        machine_id = spec.machine_id
        user_id = spec.user_id
        # create() already rejected None for both; re-state so the type checker
        # narrows them for the manager call below.
        if machine_id is None or user_id is None:  # pragma: no cover - create() guards
            raise SandboxError("remote spec missing machine_id/user_id")
        result = self._rsm.create_remote_session(
            user_id=user_id,
            machine_id=machine_id,
            project_path=spec.project_path,
            cli_tool=spec.cli_tool,
            model=exec_policy.model,
            permission_mode=exec_policy.permission_mode,
            allowed_tools=list(exec_policy.allowed_tools) if exec_policy.allowed_tools else None,
        )
        if not result or result.get("success") is False or not result.get("session_id"):
            self._status[handle.sandbox_id] = SandboxStatus.ERROR
            raise SandboxError((result or {}).get("error", "failed to create remote session"))
        remote_session_id = result["session_id"]
        self._remote_sid[handle.sandbox_id] = remote_session_id
        self._status[handle.sandbox_id] = SandboxStatus.RUNNING
        # #2078 review 🟡A: restore the create↔send cancellation window the old
        # _run_remote had two _stopped checks for. exec merges create+send; the
        # runner passes its _stopped event as cancel_check so a shutdown landing
        # during create_remote_session is caught BEFORE send — no prompt reaches
        # a cancelled task (the old "intercept before dispatch" guarantee).
        if cancel_check is not None and cancel_check():
            try:
                self._rsm.stop_session(remote_session_id)
            except Exception:  # pragma: no cover - best-effort cleanup
                pass
            self._status[handle.sandbox_id] = SandboxStatus.STOPPED
            raise SandboxError("cancelled before prompt dispatch")
        sent = self._rsm.send_message(
            session_id=remote_session_id,
            content=exec_policy.prompt,
            user_id=spec.user_id,
        )
        if sent is False:
            try:
                self._rsm.stop_session(remote_session_id)
            except Exception:  # pragma: no cover - best-effort cleanup
                pass
            self._status[handle.sandbox_id] = SandboxStatus.ERROR
            raise SandboxError("failed to send prompt to remote session")
        return ExecHandle(sandbox_id=handle.sandbox_id, command_id=remote_session_id)

    def stream(self, exec_handle: ExecHandle) -> Iterator[SandboxEvent]:
        sandbox_id = exec_handle.sandbox_id
        remote_session_id = exec_handle.command_id
        self._status[sandbox_id] = SandboxStatus.RUNNING
        yield SandboxEvent(kind=SandboxEventKind.PROCESS_STARTED, sandbox_id=sandbox_id)
        yield SandboxEvent(
            kind=SandboxEventKind.COMMAND_STARTED,
            sandbox_id=sandbox_id,
            command_id=remote_session_id,
        )
        # Poll get_session_status until the remote manager observes the turn
        # complete (an output entry with is_complete=True), the poll budget runs
        # out (COMMAND_TIMED_OUT), or status polling itself fails
        # (SANDBOX_ERROR). Never disguise a non-completion as COMMAND_COMPLETED
        # (#2078 review P1#3). Message normalization stays with the runner.
        deadline = time.monotonic() + max(self._poll_timeout, 0.0)
        exit_code = 0
        terminal_kind = SandboxEventKind.COMMAND_TIMED_OUT  # if the loop exhausts
        getter = getattr(self._rsm, "get_session_status", None)
        if not callable(getter):
            terminal_kind = SandboxEventKind.SANDBOX_ERROR
        else:
            while time.monotonic() < deadline:
                try:
                    remote_state = getter(remote_session_id)
                except Exception:
                    terminal_kind = SandboxEventKind.SANDBOX_ERROR
                    remote_state = None
                    break
                if remote_state is not None and self._turn_complete(remote_state):
                    exit_code = int(remote_state.get("exit_code") or 0)
                    self._last_exit_code[sandbox_id] = exit_code
                    terminal_kind = SandboxEventKind.COMMAND_COMPLETED
                    break
                time.sleep(self._poll_interval)
        if terminal_kind != SandboxEventKind.COMMAND_COMPLETED:
            # Non-completion is an error state; collect_execution_evidence reads
            # _last_terminal_kind for the honest terminal_reason.
            self._status[sandbox_id] = SandboxStatus.ERROR
        self._last_terminal_kind[sandbox_id] = terminal_kind
        yield SandboxEvent(
            kind=terminal_kind,
            sandbox_id=sandbox_id,
            command_id=remote_session_id,
            exit_code=exit_code,
        )
        yield SandboxEvent(
            kind=SandboxEventKind.PROCESS_EXITED,
            sandbox_id=sandbox_id,
            exit_code=exit_code,
        )

    def pause(self, exec_handle: ExecHandle) -> None:
        # A remote CLI session has no SIGSTOP analogue; pause is a documented
        # no-op. The orchestrator's pause-aware timeout stays runner-side.
        return None

    def resume(self, exec_handle: ExecHandle) -> None:
        return None

    def stop(self, exec_handle: ExecHandle) -> None:
        try:
            self._rsm.stop_session(exec_handle.command_id)
        except Exception:  # pragma: no cover - best-effort
            pass
        self._status[exec_handle.sandbox_id] = SandboxStatus.STOPPED

    def upload_workspace(self, handle: SandboxHandle, snapshot: Any | None) -> None:
        # Remote workspaces are already materialized on the remote machine.
        return None

    def collect_changes(self, handle: SandboxHandle) -> Any:
        # Remote delegates change collection to the git-workspace service
        # (#2041-2043) over the remote checkout; not owned here.
        return None

    def collect_execution_evidence(self, handle: SandboxHandle) -> list[Any]:
        # Fills the #2046-A sandbox attribution (sandbox_id/generation). The
        # exit_code is the last stream() observed; terminal_reason reflects the
        # terminal event kind stream() reached (completed / timeout / sandbox
        # error) — never assuming completed on a non-completion (#2078 P1#3).
        # Prod remote does not call this today (the runner drives its own poll
        # loop and stamps attribution via the recorder); this is the contract
        # path #2023 gVisor inherits.
        remote_session_id = self._remote_sid.get(handle.sandbox_id, handle.sandbox_id)
        exit_code = self._last_exit_code.get(handle.sandbox_id, 0)
        terminal_kind = self._last_terminal_kind.get(handle.sandbox_id)
        terminal_reason = self._terminal_reason(terminal_kind, exit_code)
        return [
            CommandExecutionEvidence(
                command_id=remote_session_id,
                sandbox_id=handle.sandbox_id,
                sandbox_generation=handle.generation,
                cwd=handle.spec.project_path,
                exit_code=exit_code,
                terminal_reason=terminal_reason,
            )
        ]

    def destroy(self, handle: SandboxHandle) -> None:
        # Idempotent: stop the remote session if known, then mark destroyed.
        remote_session_id = self._remote_sid.pop(handle.sandbox_id, None)
        if remote_session_id is not None:
            try:
                self._rsm.stop_session(remote_session_id)
            except Exception:  # pragma: no cover - best-effort
                pass
        self._status[handle.sandbox_id] = SandboxStatus.DESTROYED

    def destroy_attribution(self, sandbox_id: str, remote_session_id: str | None) -> None:
        # Reconcile-path destroy (#2022 P6): the per-call provider instance (and
        # its _remote_sid map) is gone after a restart, so destroy(handle) cannot
        # resolve the session. Stop directly by the persisted id. Best-effort +
        # idempotent: a failing/repeated stop must not raise (the sweep walks many
        # rows). local/gVisor rows pass remote_session_id=None -> no-op.
        if not remote_session_id:
            return
        try:
            self._rsm.stop_session(remote_session_id)
        except Exception:  # pragma: no cover - best-effort
            pass

    def inspect(self, handle: SandboxHandle) -> SandboxStatus:
        return self._status.get(handle.sandbox_id, SandboxStatus.DESTROYED)

    # ── helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _turn_complete(remote_state: Any) -> bool:
        """Whether the remote manager observed the current request finish.

        Mirrors ``agent_runner._remote_turn_complete``: an output entry with
        ``is_complete`` on a stdout/stderr/system stream.
        """
        if not isinstance(remote_state, dict):
            return False
        output = remote_state.get("output") or []
        return any(
            isinstance(entry, dict)
            and bool(entry.get("is_complete"))
            and entry.get("stream") in ("stdout", "stderr", "system")
            for entry in output
        )

    @staticmethod
    def _terminal_reason(kind: SandboxEventKind | None, exit_code: int) -> str:
        """Map stream()'s terminal event kind to a #2046-A terminal_reason."""
        if kind == SandboxEventKind.COMMAND_TIMED_OUT:
            return TerminalReason.TIMEOUT.value
        if kind == SandboxEventKind.SANDBOX_ERROR:
            return "sandbox_error"
        return derive_terminal_reason(exit_code=exit_code, has_result=True).value
