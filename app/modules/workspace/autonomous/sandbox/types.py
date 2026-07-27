"""SandboxProvider contract types (Issue #2022 Phase 1).

Phase 1 fixes the stable contract surface — capability taxonomy, the
``SandboxSpec`` value object, lifecycle status, handles, and the normalized
event stream — that later phases implement:

* Phase 2 — persistence + reconciliation (``sandbox_*`` columns on
  ``autonomous_workflows``).
* Phase 3 — ``LegacyPosixProvider`` wrapping the existing local POSIX path
  (``openace-run-as`` + ACL + per-task HOME/TMP/cgroup from #2020).
* Phase 4 — ``RemoteMachineProvider`` wrapping the autonomous remote-agent
  execution surface.
* Phase 5 — wiring + UI.

Scope (#2022 comment, 2026-07-26): this contract abstracts ONLY the autonomous
agent execution path. It does not touch ``app/routes/fs.py::run_as_user`` or
the ordinary remote-session lifecycle in ``remote_session_manager.py``.

Design note — provider vs CLI protocol boundary
------------------------------------------------
The provider owns *sandbox mechanics*: environment build, the
``sudo``/``openace-run-as``/ACL command wrap, the ``subprocess.Popen`` and its
process group, signal delivery (pause/resume/stop), and exit classification.
It deliberately does NOT own the CLI protocol (stream-json stdin handshake,
``tool_use``/``tool_result`` parsing, session-id resolution, usage accounting)
— that stays in ``agent_runner`` and consumes the provider's raw byte streams.
This separation is what keeps a provider from re-leaking backend detail into
the orchestrator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids runtime import cycle
    from app.modules.workspace.autonomous.task_isolation import AgentTaskPolicy


class SandboxCapability(str, Enum):
    """What isolation guarantees a provider can actually supply.

    A spec's ``required_capabilities`` is checked against a provider's
    declared capabilities at ``create`` time; an unsupported *required*
    capability must refuse creation (fail-closed) rather than silently
    degrade. ``LegacyPosixProvider`` (Phase 3) satisfies the first four;
    ``namespace_isolation`` / ``network_egress_policy`` are reserved for the
    OpenSandbox/Kubernetes backend (#2023).
    """

    PRIVATE_HOME_TMP_XDG = "private_home_tmp_xdg"
    FILESYSTEM_ACL = "filesystem_acl"
    CPU_MEM_PIDS_TIME_QUOTA = "cpu_mem_pids_time_quota"
    CREDENTIAL_TOKEN_BINDING = "credential_token_binding"
    NAMESPACE_ISOLATION = "namespace_isolation"
    NETWORK_EGRESS_POLICY = "network_egress_policy"


# ── gVisor/container-facing isolation dimensions (#2022 P4 ①) ──
#
# A gVisor/OpenSandbox backend (#2023) needs to express network egress,
# runtime/image and volumes THROUGH THE SPEC, not by extending the contract
# (which would re-leak backend detail into the seam — exactly what #2022
# prevents). HOME/TMP/cgroup/quota stay on #2020's AgentTaskPolicy via
# ``SandboxSpec.policy``; these value objects model only the dimensions
# AgentTaskPolicy does not. Legacy/Remote providers leave them None/empty.


@dataclass(frozen=True)
class NetworkEgressPolicy:
    """Outbound network policy a sandbox requires (#2023 gVisor headline).

    ``mode``: ``deny_all`` (default, safest) | ``allow_explicit`` (only the
    listed CIDRs/hosts) | ``unrestricted`` (escape hatch, fail-closed providers
    refuse to satisfy this without an explicit capability).
    """

    mode: str = "deny_all"
    allow_cidrs: tuple[str, ...] = ()
    allow_hosts: tuple[str, ...] = ()


@dataclass(frozen=True)
class RuntimeSpec:
    """Container runtime/image a sandbox should use (#2023).

    ``runtime`` e.g. ``runsc`` (gVisor) / ``runc`` / ``""`` (Legacy: N/A).
    ``toolchain`` is an optional named toolchain profile the backend resolves.
    """

    image: str = ""
    runtime: str = ""
    toolchain: str = ""


@dataclass(frozen=True)
class VolumeSpec:
    """One mount a sandbox should expose (#2023)."""

    name: str
    mount_path: str
    kind: str = "ephemeral"  # ephemeral | persistent
    read_only: bool = False


@dataclass(frozen=True)
class SandboxSpec:
    """Immutable description of the sandbox a task needs.

    Carries identity (``task_id`` / ``cli_tool`` / ``system_account``) plus the
    isolation intent. The HOME/TMP/quota knobs are NOT redefined here — they
    ride on the #2020 :class:`AgentTaskPolicy` via ``policy`` — so there is one
    source of truth for the per-task isolation tree. The gVisor-facing
    dimensions (``network_egress`` / ``runtime`` / ``volumes``) model what
    AgentTaskPolicy does not, so a #2023 backend expresses intent through the
    spec rather than by growing the contract. ``machine_id`` / ``user_id``
    carry remote identity (Phase 4). ``required_capabilities`` is the
    fail-closed gate: a provider that cannot meet a required capability must
    reject the spec at creation.
    """

    task_id: str
    project_path: str
    cli_tool: str
    system_account: str | None = None
    policy: AgentTaskPolicy | None = None
    required_capabilities: frozenset[SandboxCapability] = field(default_factory=frozenset)
    # Remote identity (Phase 4 RemoteMachineProvider). None for local/gVisor.
    machine_id: str | None = None
    user_id: int | None = None
    # gVisor/container dimensions (#2023). None/empty for Legacy/Remote, which
    # ignore them.
    network_egress: NetworkEgressPolicy | None = None
    runtime: RuntimeSpec | None = None
    volumes: tuple[VolumeSpec, ...] = ()
    # Lightweight profile slots whose semantics are decided by #2047 (transcript)
    # and #2046 (evidence); empty string = provider default.
    transcript_profile: str = ""
    evidence_profile: str = ""


class SandboxStatus(str, Enum):
    """Live lifecycle state of a sandbox, as observed via ``inspect``.

    The :class:`SandboxHandle` snapshots ``CREATED`` at creation; subsequent
    transitions are observed through ``provider.inspect(handle)`` rather than
    by mutating the frozen handle, so a stale handle from a prior generation
    cannot fabricate a live status.
    """

    CREATED = "created"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    DESTROYED = "destroyed"
    ERROR = "error"


@dataclass(frozen=True)
class SandboxHandle:
    """Identity + creation snapshot for a sandbox.

    ``sandbox_id`` is provider-minted and stable across the task; ``generation``
    is bumped on restart/reconciliation so a stale handle (e.g. one held across
    a provider restart) cannot operate on a new sandbox with the same id. The
    ``initial_status`` field is the creation-time snapshot only — use
    :meth:`SandboxProvider.inspect` for the live status (a frozen handle cannot
    track transitions).
    """

    sandbox_id: str
    generation: int
    provider_name: str
    spec: SandboxSpec
    initial_status: SandboxStatus = SandboxStatus.CREATED


@dataclass(frozen=True)
class ExecHandle:
    """Identity for one command execution within a sandbox.

    ``pause``/``resume``/``stop`` key off the exec handle; ``sandbox_id`` links
    it back to the owning sandbox so lifecycle ops resolve the live status.
    """

    sandbox_id: str
    command_id: str


class SandboxEventKind(str, Enum):
    """Normalized lifecycle event every provider must emit (issue §3).

    These are the *sandbox-lifecycle* events — distinct from the CLI-protocol
    events (``tool_use``/``tool_result``) the runner emits from parsing the
    agent's stream-json. A consumer correlates them via ``sandbox_id``.
    """

    PROCESS_STARTED = "process_started"
    COMMAND_STARTED = "command_started"
    STDOUT_CHUNK = "stdout_chunk"
    STDERR_CHUNK = "stderr_chunk"
    COMMAND_COMPLETED = "command_completed"
    COMMAND_TIMED_OUT = "command_timed_out"
    COMMAND_CANCELLED = "command_cancelled"
    PROCESS_EXITED = "process_exited"
    RESOURCE_LIMIT_EXCEEDED = "resource_limit_exceeded"
    SANDBOX_ERROR = "sandbox_error"


@dataclass(frozen=True)
class SandboxEvent:
    """One normalized lifecycle event from a provider's ``stream``.

    ``data`` carries the chunk text for ``STDOUT_CHUNK``/``STDERR_CHUNK``;
    ``exit_code`` is authoritative for ``COMMAND_COMPLETED``/``PROCESS_EXITED``
    (#2046 contract); ``signal`` is set for ``RESOURCE_LIMIT_EXCEEDED``.
    """

    kind: SandboxEventKind
    sandbox_id: str = ""
    command_id: str = ""
    exit_code: int | None = None
    signal: int | None = None
    data: str = ""
