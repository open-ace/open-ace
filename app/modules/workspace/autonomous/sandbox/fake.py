"""In-memory SandboxProvider for contract tests (Issue #2022 Phase 1).

``FakeSandboxProvider`` is the shared vehicle for contract tests: Phase 3
(``LegacyPosixProvider``) and Phase 4 (``RemoteMachineProvider``) must satisfy
the same tests against their own implementations. Phase 1 implements the
creation/destroy/inspect surface; Phase 3 extends it with
``exec``/``stream``/``pause``/``resume``/``stop``/``collect_execution_evidence``.

It is NOT a production provider — it owns no process, no ACL, no cgroup. It
records state in memory so tests can assert on the contract without a real
sandbox.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

from app.modules.workspace.autonomous.sandbox.provider import validate_spec_capabilities
from app.modules.workspace.autonomous.sandbox.types import (
    ExecHandle,
    SandboxCapability,
    SandboxEvent,
    SandboxEventKind,
    SandboxHandle,
    SandboxSpec,
    SandboxStatus,
)

# The four isolation guarantees the Legacy POSIX backend will declare in
# Phase 3. The fake defaults to these so ``FakeSandboxProvider()`` stands in
# for Legacy without each test repeating the set.
_LEGACY_CAPS = frozenset(
    {
        SandboxCapability.PRIVATE_HOME_TMP_XDG,
        SandboxCapability.FILESYSTEM_ACL,
        SandboxCapability.CPU_MEM_PIDS_TIME_QUOTA,
        SandboxCapability.CREDENTIAL_TOKEN_BINDING,
    }
)


class FakeSandboxProvider:
    """In-memory provider satisfying the :class:`SandboxProvider` contract."""

    def __init__(self, capabilities: frozenset[SandboxCapability] | None = None) -> None:
        self._capabilities: frozenset[SandboxCapability] = (
            frozenset(capabilities) if capabilities is not None else _LEGACY_CAPS
        )
        # sandbox_id -> live status. Unknown ids inspect as DESTROYED so
        # reconciling an orphan handle (one this provider never minted) is a
        # no-op rather than a KeyError.
        self._status: dict[str, SandboxStatus] = {}
        # command_id -> the command argv + owning sandbox_id, so stream() can
        # replay a deterministic lifecycle without a real process.
        self._execs: dict[str, dict[str, Any]] = {}
        # P6.2: record reconcile-time destroy_attribution calls for assertions.
        self.destroy_attribution_calls: list[tuple[str, str | None]] = []

    def capabilities(self) -> frozenset[SandboxCapability]:
        return self._capabilities

    def create(self, spec: SandboxSpec) -> SandboxHandle:
        validate_spec_capabilities(self._capabilities, spec)
        sandbox_id = uuid.uuid4().hex
        self._status[sandbox_id] = SandboxStatus.CREATED
        return SandboxHandle(
            sandbox_id=sandbox_id,
            generation=1,
            provider_name="fake",
            spec=spec,
            initial_status=SandboxStatus.CREATED,
        )

    def exec(
        self,
        handle: SandboxHandle,
        command: list[str],
        env: dict[str, str] | None,
        exec_policy: Any | None,
    ) -> ExecHandle:
        command_id = uuid.uuid4().hex
        self._execs[command_id] = {
            "sandbox_id": handle.sandbox_id,
            "command": list(command),
        }
        return ExecHandle(sandbox_id=handle.sandbox_id, command_id=command_id)

    def upload_workspace(self, handle: SandboxHandle, snapshot: Any | None) -> None:
        # Phase 1 placeholder. LegacyPosixProvider (P3) is a no-op (the local
        # worktree is already in place); container/K8s backends (#2023) upload
        # the snapshot. P1 just freezes the seam.
        return None

    def stream(self, exec_handle: ExecHandle) -> Iterator[SandboxEvent]:
        meta = self._execs[exec_handle.command_id]
        sandbox_id = exec_handle.sandbox_id
        command = meta["command"]
        # The canonical happy-path sequence every provider must emit.
        self._status[sandbox_id] = SandboxStatus.RUNNING
        yield SandboxEvent(kind=SandboxEventKind.PROCESS_STARTED, sandbox_id=sandbox_id)
        yield SandboxEvent(
            kind=SandboxEventKind.COMMAND_STARTED,
            sandbox_id=sandbox_id,
            command_id=exec_handle.command_id,
        )
        yield SandboxEvent(
            kind=SandboxEventKind.STDOUT_CHUNK,
            sandbox_id=sandbox_id,
            command_id=exec_handle.command_id,
            data=" ".join(command),
        )
        yield SandboxEvent(
            kind=SandboxEventKind.COMMAND_COMPLETED,
            sandbox_id=sandbox_id,
            command_id=exec_handle.command_id,
            exit_code=0,
        )
        yield SandboxEvent(
            kind=SandboxEventKind.PROCESS_EXITED,
            sandbox_id=sandbox_id,
            exit_code=0,
        )

    def pause(self, exec_handle: ExecHandle) -> None:
        self._status[exec_handle.sandbox_id] = SandboxStatus.PAUSED

    def resume(self, exec_handle: ExecHandle) -> None:
        self._status[exec_handle.sandbox_id] = SandboxStatus.RUNNING

    def stop(self, exec_handle: ExecHandle) -> None:
        self._status[exec_handle.sandbox_id] = SandboxStatus.STOPPED

    def collect_execution_evidence(self, handle: SandboxHandle) -> list[Any]:
        # Phase 1: the contract method exists; Phase 3 fills it from the real
        # command stream (test_exec_emits_command_execution_evidence).
        return []

    def collect_changes(self, handle: SandboxHandle) -> Any:
        # Phase 1 placeholder (ChangeSet schema fixed in P3/P4). Legacy
        # delegates to the git-workspace service (#2041-2043); non-local
        # backends own collection. P1 just freezes the seam.
        return None

    def destroy(self, handle: SandboxHandle) -> None:
        # Idempotent: destroy of an already-destroyed or unknown handle is a
        # no-op. Reconciliation (Phase 2) relies on this for orphan sandboxes.
        self._status[handle.sandbox_id] = SandboxStatus.DESTROYED

    def destroy_attribution(self, sandbox_id: str, remote_session_id: str | None) -> None:
        # P6.2: record the reconcile-path destroy-by-attribution for assertions.
        self.destroy_attribution_calls.append((sandbox_id, remote_session_id))

    def inspect(self, handle: SandboxHandle) -> SandboxStatus:
        return self._status.get(handle.sandbox_id, SandboxStatus.DESTROYED)
