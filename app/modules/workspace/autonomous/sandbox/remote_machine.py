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
    derive_terminal_reason,
)
from app.modules.workspace.autonomous.sandbox.provider import SandboxError, require_capabilities
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

# Remote provides an isolated agent environment equivalent to Legacy (private
# HOME on the remote machine, ACL'd workspace, proxy-token credential binding,
# resource limits enforced by the remote deployment). Same capability set as
# Legacy so a spec portable local↔remote does not fail-closed on the remote
# path. gVisor/K8s (#2023) adds NAMESPACE_ISOLATION / NETWORK_EGRESS_POLICY.
_REMOTE_CAPS = frozenset(
    {
        SandboxCapability.PRIVATE_HOME_TMP_XDG,
        SandboxCapability.FILESYSTEM_ACL,
        SandboxCapability.CPU_MEM_PIDS_TIME_QUOTA,
        SandboxCapability.CREDENTIAL_TOKEN_BINDING,
    }
)


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
        self._poll_interval = poll_interval
        self._poll_timeout = poll_timeout

    def capabilities(self) -> frozenset[SandboxCapability]:
        return _REMOTE_CAPS

    def create(self, spec: SandboxSpec) -> SandboxHandle:
        require_capabilities(_REMOTE_CAPS, spec.required_capabilities)
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
        # complete (an output entry with is_complete=True) or the poll budget
        # runs out. Message normalization stays with the runner.
        deadline = time.monotonic() + max(self._poll_timeout, 0.0)
        exit_code = 0
        getter = getattr(self._rsm, "get_session_status", None)
        while time.monotonic() < deadline:
            remote_state = getter(remote_session_id) if callable(getter) else None
            if remote_state is not None and self._turn_complete(remote_state):
                exit_code = int(remote_state.get("exit_code") or 0)
                self._last_exit_code[sandbox_id] = exit_code
                break
            time.sleep(self._poll_interval)
        yield SandboxEvent(
            kind=SandboxEventKind.COMMAND_COMPLETED,
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
        # exit_code is the last one stream()'s poll observed (stored in
        # _last_exit_code); before stream runs it defaults to 0. Prod remote
        # does not call this today (the runner drives its own poll loop and
        # stamps sandbox attribution via the recorder), so this is the contract
        # path #2023 gVisor inherits.
        remote_session_id = self._remote_sid.get(handle.sandbox_id, handle.sandbox_id)
        exit_code = self._last_exit_code.get(handle.sandbox_id, 0)
        terminal = derive_terminal_reason(exit_code=exit_code, has_result=True)
        return [
            CommandExecutionEvidence(
                command_id=remote_session_id,
                sandbox_id=handle.sandbox_id,
                sandbox_generation=handle.generation,
                cwd=handle.spec.project_path,
                exit_code=exit_code,
                terminal_reason=terminal.value,
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
