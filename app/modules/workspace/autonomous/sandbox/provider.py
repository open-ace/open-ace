"""SandboxProvider Protocol and contract errors (Issue #2022 Phase 1).

The Protocol is the stable seam between the orchestrator (which only knows the
contract) and concrete backends (``LegacyPosixProvider`` Phase 3,
``RemoteMachineProvider`` Phase 4, OpenSandbox/Kubernetes #2023). Phase 1
freezes the full lifecycle surface here so P2-P5 implement against a fixed
seam; two result schemas (``collect_changes``/``collect_execution_evidence``)
stay ``Any`` until their owning services (#2041-2043, #2046) land.
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

    def __init__(self, missing_capabilities: frozenset[SandboxCapability]) -> None:
        self.missing_capabilities = frozenset(missing_capabilities)
        names = ", ".join(sorted(cap.value for cap in self.missing_capabilities))
        super().__init__(f"provider cannot supply required capabilities: {names}")


def require_capabilities(
    available: frozenset[SandboxCapability],
    required: frozenset[SandboxCapability],
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
    """Stable execution contract independent of the sandboxing backend.

    The full lifecycle surface is frozen here so P2-P5 implement against a
    fixed seam (the point of Phase 1). Two methods return ``Any`` for now —
    ``collect_changes`` and ``collect_execution_evidence`` — because their
    result schemas bind to services (#2041-2043 git-workspace, #2046 evidence)
    that land in later phases; tightening ``Any`` to concrete types later is
    backward-compatible and does not churn the seam.
    """

    def capabilities(self) -> frozenset[SandboxCapability]:
        """Declare the isolation guarantees this backend can actually supply."""
        ...

    def create(self, spec: SandboxSpec) -> SandboxHandle:
        """Create a sandbox for *spec*, fail-closed on unsupported requirements."""
        ...

    def upload_workspace(self, handle: SandboxHandle, snapshot: Any | None) -> None:
        """Push a project snapshot into the sandbox.

        ``LegacyPosixProvider`` (Phase 3) is a no-op — the local worktree is
        already in place. Container/K8s backends (#2023) materialize the
        snapshot into the ephemeral sandbox filesystem. Frozen here so #2023
        does not grow its own upload path outside the contract.
        """
        ...

    def exec(
        self,
        handle: SandboxHandle,
        command: list[str],
        env: dict[str, str] | None,
        exec_policy: Any | None,
    ) -> ExecHandle:
        """Start one command in the sandbox; return its :class:`ExecHandle`.

        ``exec_policy`` is the *per-command* execution policy (wall-clock
        budget, resource overrides) — distinct from :attr:`SandboxSpec.policy`,
        which is the *sandbox-isolation* policy (HOME/TMP/quota from #2020).
        It stays ``Any`` until Phase 3 binds it to the wall-clock budget and
        cgroup knobs.
        """
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

    def collect_changes(self, handle: SandboxHandle) -> Any:
        """Return the changes the agent made inside the sandbox.

        Load-bearing for non-local backends: a ``RemoteMachineProvider``'s agent
        edits live on the remote machine and the orchestrator cannot read them
        with local git, so the diff/ChangeSet must come back through the
        provider. Ownership: ``LegacyPosixProvider`` delegates to the existing
        git-workspace service (#2041-2043) rather than reimplementing git;
        container/K8s backends (#2023) own collection from the ephemeral FS
        before destroy. Result typed ``Any`` (``ChangeSet``) until P3/P4 fix
        the schema.
        """
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
