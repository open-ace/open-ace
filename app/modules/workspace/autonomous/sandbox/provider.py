"""SandboxProvider Protocol and contract errors (Issue #2022 Phase 1).

The Protocol is the stable seam between the orchestrator (which only knows the
contract) and concrete backends (``LegacyPosixProvider`` Phase 3,
``RemoteMachineProvider`` Phase 4, OpenSandbox/Kubernetes #2023). Phase 1
declares the creation/destroy/inspect surface; Phase 3 extends it with
``exec``/``stream``/``pause``/``resume``/``stop``/``collect_execution_evidence``
as those tests land.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:  # pragma: no cover - annotations only (PEP 563)
    from app.modules.workspace.autonomous.sandbox.types import (
        ExecHandle,
        SandboxCapability,
        SandboxEvent,
        SandboxHandle,
        SandboxSpec,
        SandboxStatus,
    )


class SandboxError(Exception):
    """Base for all sandbox-contract failures (creation, lifecycle, teardown)."""


class CapabilityUnsupported(SandboxError):
    """A spec required a capability the provider cannot supply.

    Raised fail-closed at ``create`` time. Carries the missing capabilities so
    callers (and the workflow UI) can report which isolation guarantee was
    unsatisfied rather than silently degrading.
    """

    def __init__(self, missing_capabilities) -> None:
        self.missing_capabilities = frozenset(missing_capabilities)
        names = ", ".join(sorted(cap.value for cap in self.missing_capabilities))
        super().__init__(f"provider cannot supply required capabilities: {names}")


def require_capabilities(
    available,
    required,
) -> None:
    """Raise :class:`CapabilityUnsupported` if *required* is not a subset.

    Shared by every provider's ``create`` so the fail-closed gate has one
    implementation. ``available`` is what the provider declares; ``required``
    is what the spec demands.
    """
    missing = frozenset(required) - frozenset(available)
    if missing:
        raise CapabilityUnsupported(missing)


class SandboxProvider(Protocol):
    """Stable execution contract independent of the sandboxing backend."""

    def capabilities(self) -> frozenset[SandboxCapability]:
        """Declare the isolation guarantees this backend can actually supply."""
        ...

    def create(self, spec: SandboxSpec) -> SandboxHandle:
        """Create a sandbox for *spec*, fail-closed on unsupported requirements."""
        ...

    def exec(
        self,
        handle: SandboxHandle,
        command: list[str],
        env: dict[str, str] | None,
        policy: Any | None,
    ) -> ExecHandle:
        """Start one command in the sandbox; return its :class:`ExecHandle`."""
        ...

    def stream(self, exec_handle: ExecHandle) -> Iterator[SandboxEvent]:
        """Yield the normalized lifecycle events for an execution."""
        ...

    def pause(self, exec_handle: ExecHandle) -> None:
        """Pause the sandbox (best-effort; not every backend supports it)."""
        ...

    def resume(self, exec_handle: ExecHandle) -> None:
        """Resume a paused sandbox."""
        ...

    def stop(self, exec_handle: ExecHandle) -> None:
        """Stop the running command (graceful → forceful escalation is backend-specific)."""
        ...

    def collect_execution_evidence(self, handle: SandboxHandle) -> list[Any]:
        """Return ``CommandExecutionEvidence`` rows produced in this sandbox.

        Typed loosely (``list[Any]``) in Phase 1; Phase 3 binds it to the
        #2046 :class:`CommandExecutionEvidence` once the provider fills
        ``sandbox_id``/``sandbox_generation``/``signal``/``stderr_*``.
        """
        ...

    def destroy(self, handle: SandboxHandle) -> None:
        """Tear down the sandbox. Idempotent: repeated calls do not raise."""
        ...

    def inspect(self, handle: SandboxHandle) -> SandboxStatus:
        """Return the live status of the sandbox."""
        ...
