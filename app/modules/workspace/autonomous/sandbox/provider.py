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

# SandboxCapability is needed at runtime (implied_required_capabilities), not
# just for annotations. types.py is a leaf module, so this is cycle-free.
from app.modules.workspace.autonomous.sandbox.types import SandboxCapability

if TYPE_CHECKING:  # pragma: no cover - annotations only (PEP 563)
    from app.modules.workspace.autonomous.sandbox.types import (
        ExecHandle,
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


def implied_required_capabilities(spec: SandboxSpec) -> frozenset[SandboxCapability]:
    """Capabilities a spec's explicit policy fields imply (#2078 review P1#1).

    Without this, a caller can set ``network_egress`` / ``runtime`` / ``volumes``
    on the spec but forget to add the matching ``required_capabilities`` entry —
    and a provider that cannot honor the policy would silently ignore it. By
    deriving the implied requirement from the fields themselves, ``create``
    fail-closes instead: ``network_egress`` demands ``NETWORK_EGRESS_POLICY``;
    ``runtime``/``volumes`` (container image + mounts) demand
    ``NAMESPACE_ISOLATION``; ``policy.ephemeral_storage_limit``/``inode_limit``
    (>0) demand ``STORAGE_INODE_QUOTA`` (#2020 Phase B).
    """
    implied: set[SandboxCapability] = set()
    if spec.network_egress is not None:
        implied.add(SandboxCapability.NETWORK_EGRESS_POLICY)
    if spec.runtime is not None or spec.volumes:
        implied.add(SandboxCapability.NAMESPACE_ISOLATION)
    # #2020 Phase B: a policy declaring ephemeral_storage / inode limits (>0)
    # demands STORAGE_INODE_QUOTA. Legacy has no io.max/disk quota and does not
    # declare it, so such a spec fail-closes at create() — exactly the
    # test_provider_rejects_required_policy_when_unsupported guarantee.
    policy = spec.policy
    if policy is not None and (policy.ephemeral_storage_limit > 0 or policy.inode_limit > 0):
        implied.add(SandboxCapability.STORAGE_INODE_QUOTA)
    return frozenset(implied)


def validate_spec_capabilities(available: frozenset[SandboxCapability], spec: SandboxSpec) -> None:
    """Fail-closed gate combining explicit + field-implied requirements.

    Single entry point for every provider's ``create``: merges the spec's
    ``required_capabilities`` with the caps its policy fields imply, then
    :func:`require_capabilities` against what the provider declares.
    """
    required = frozenset(spec.required_capabilities) | implied_required_capabilities(spec)
    require_capabilities(available, required)


def is_current_generation(
    handle_generation: int | None,
    workflow_generation: int | None,
) -> bool:
    """True iff a handle's generation matches the workflow's current one.

    The workflow's ``sandbox_generation`` bumps on reconciliation/restart; a
    handle minted before that bump (gen N) is stale against the new generation
    (N+1) and must not operate on a future sandbox. Used by Phase 3/4 providers
    to reject stale handles, and by the Phase 2 reconciliation sweep's tests.

    ``None`` on either side cannot be confirmed current → ``False`` (fail safe:
    providers reject the op rather than risk acting on a stale handle).
    """
    if handle_generation is None or workflow_generation is None:
        return False
    return handle_generation == workflow_generation


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

    def destroy_attribution(self, sandbox_id: str, remote_session_id: str | None) -> None:
        """Tear down a sandbox identified only by persisted attribution (#2022 P6).

        Used by the startup/periodic reconciler after a crash/restart, when the
        per-call provider instance (and its ``sandbox_id`` -> handle map) is gone
        and only the strings persisted to the workflow row remain. ``destroy()``
        cannot be used here — it keys off a live handle this provider no longer
        holds. Idempotent + best-effort: never raises (the reconciler sweeps many
        rows and must not abort on one failure).

        Legacy/local: no-op (the process died with the server; the reconciler's
        DB-reset is the real cleanup). Remote: ``stop_session(remote_session_id)``
        when set. Container/gVisor (#2023) kills its sandbox by id here.
        """
        ...

    def inspect(self, handle: SandboxHandle) -> SandboxStatus:
        """Return the live status of the sandbox."""
        ...
