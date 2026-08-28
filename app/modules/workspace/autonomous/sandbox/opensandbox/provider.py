"""OpenSandboxProvider — the production sandbox backend (Issue #2023).

Implements the frozen ``#2022`` :class:`SandboxProvider` contract over
OpenSandbox. Policy translation lives in ``policy.py`` and HTTP in
``client.py``; this module owns lifecycle, the status overlay, evidence, and the
two boot probes.

Three behaviours here exist because upstream's real timing differs from the
obvious reading, and getting them wrong is silent rather than loud:

* **The status overlay.** ``POST /sandboxes`` returns ``Running`` (provisioning
  is synchronous) and ``DELETE`` goes ``Stopping`` → ``Terminated``. The
  contract tests expect ``CREATED`` after create and ``DESTROYED`` immediately
  after destroy, so the provider keeps a per-sandbox overlay. It is set only on
  facts it observed: a destroy that never confirms leaves the overlay unset and
  reports the true state, because claiming ``DESTROYED`` for a sandbox still
  consuming quota is the same lie as marking a workflow row destroyed without
  destroying anything.
* **A non-zero exit is an SSE ``error``, not ``execution_complete``.** Mapping
  it to ``SANDBOX_ERROR`` would report every failing ``pytest`` run as
  infrastructure failure.
* **A pod-level OOM takes execd with it.** There is no exit code to read;
  ``/command/status`` simply becomes unreachable and the sandbox reads back
  ``Failed``.
"""

from __future__ import annotations

import shlex
import uuid
from collections.abc import Callable, Collection, Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from app.modules.workspace.autonomous.command_evidence.types import (
    CommandExecutionEvidence,
    TerminalReason,
    derive_terminal_reason,
)
from app.modules.workspace.autonomous.sandbox.opensandbox import policy as policy_mod
from app.modules.workspace.autonomous.sandbox.opensandbox import workspace as workspace_mod
from app.modules.workspace.autonomous.sandbox.opensandbox.client import HttpOpenSandboxApi
from app.modules.workspace.autonomous.sandbox.opensandbox.transport import PtyWebSocketTransport
from app.modules.workspace.autonomous.sandbox.provider import SandboxError, is_current_generation
from app.modules.workspace.autonomous.sandbox.types import (
    ExecHandle,
    SandboxCapability,
    SandboxEvent,
    SandboxEventKind,
    SandboxHandle,
    SandboxSpec,
    SandboxStatus,
)

if TYPE_CHECKING:  # pragma: no cover - annotations only
    from app.modules.workspace.autonomous.sandbox.opensandbox.client import OpenSandboxApi
    from app.modules.workspace.autonomous.sandbox.opensandbox.config import (
        EndpointConfig,
        SandboxBackendConfig,
    )

PROVIDER_NAME = "opensandbox"

# Where the agent's tree lives inside the sandbox.
_WORKSPACE = "/workspace"

# Self-contained repo synthesis. Runs AFTER upload_workspace, never in the
# entrypoint: the entrypoint fires during create, before any file has landed,
# so it would commit an empty tree and leave the snapshot untracked. The
# identity is inline because refusal 9 mandates a read-only rootfs and a
# non-root uid, so there is no writable ~/.gitconfig and no ambient identity —
# git would fail with "Please tell me who you are" and produce a repo with no
# HEAD.
_GIT_SYNTHESIS = (
    "git init -q && git add -A && "
    "git -c user.name='Open ACE' -c user.email='agent@open-ace.invalid' "
    "-c commit.gpgsign=false commit -q -m 'snapshot' --allow-empty"
)


class OpenSandboxError(SandboxError):
    """A fail-closed refusal, carrying a machine-readable reason code.

    The audit sink records the code, and a caller with no sink still surfaces it
    on the exception — a refusal must never be indistinguishable from a
    missing sandbox.
    """

    def __init__(self, message: str, *, reason_code: str = "") -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class OpenSandboxTurnSpec:
    """Per-turn params for an agent execution, carried via ``exec_policy``.

    Presence of this in the ``exec_policy`` slot is what selects the PTY
    transport over a plain foreground ``POST /command`` — mirroring how
    ``RemoteMachineProvider`` uses ``RemoteTurnSpec``. The provider records the
    branch per ``command_id`` so ``stream``/``stop`` resolve the same way.
    """

    prompt: str = ""
    model: str = ""
    proxy_token: str = ""


@dataclass
class _SandboxState:
    """Everything the provider knows about one live sandbox.

    Popped wholesale on destroy, which is what guarantees a warm-pool
    reallocation cannot inherit a previous tenant's evidence or status.
    """

    overlay: SandboxStatus | None = SandboxStatus.CREATED
    command_id: str = ""
    is_pty: bool = False
    transport: Any = None
    exit_code: int | None = None
    terminal_kind: SandboxEventKind | None = None
    started_at: str = ""
    completed_at: str = ""
    stderr: str = ""


class OpenSandboxProvider:
    """SandboxProvider over OpenSandbox's Kubernetes runtime."""

    def __init__(
        self,
        config: SandboxBackendConfig,
        *,
        api_factory: Callable[[EndpointConfig], OpenSandboxApi] = HttpOpenSandboxApi,
        tenant: str | None = None,
        project_path: str | None = None,
        event_sink: Callable[[str, dict], None] | None = None,
        connect_factory: Callable[[str, dict], Any] | None = None,
    ) -> None:
        self._config = config
        self._api_factory = api_factory
        self._tenant = tenant
        self._project_path = project_path
        self._event_sink = event_sink
        # Injected through to PtyWebSocketTransport so unit tests drive a fake
        # connection instead of opening a real socket.
        self._connect_factory = connect_factory
        self._endpoint = config.endpoint_for(tenant=tenant, project_path=project_path)
        self._api = api_factory(self._endpoint)
        self._state: dict[str, _SandboxState] = {}
        # Probes run once per endpoint per process; until they pass, the
        # capabilities they gate are not declared.
        self._probes_passed = False

    # ── contract ──────────────────────────────────────────────────────

    def capabilities(self) -> frozenset[SandboxCapability]:
        return policy_mod.derive_capabilities(self._endpoint, probes_passed=self._probes_passed)

    def create(self, spec: SandboxSpec, *, use_pool: bool = False) -> SandboxHandle:
        if use_pool and not self._endpoint.pool.usable():
            raise self._refuse(
                "pool mode requires egress_preapplied, recycle_delete and a "
                "digest-pinned image_digest; upstream rejects networkPolicy, "
                "resourceLimits and image alongside poolRef, so none of them can "
                "be enforced from here",
                "pool_not_attested",
            )
        body = self._build_create_body(spec, use_pool=use_pool)
        record = self._api.create_sandbox(body)
        sandbox_id = str(record.get("id") or "")
        if not sandbox_id:
            raise self._refuse("create returned no sandbox id", "create_failed")
        self._state[sandbox_id] = _SandboxState()
        handle = SandboxHandle(
            sandbox_id=sandbox_id,
            generation=1,
            provider_name=PROVIDER_NAME,
            spec=spec,
            initial_status=SandboxStatus.CREATED,
        )
        try:
            self._run_probes(handle)
        except SandboxError:
            # A sandbox we cannot verify must not survive the attempt.
            self._safe_destroy(sandbox_id)
            self._state.pop(sandbox_id, None)
            raise
        self._emit("sandbox_created", {"sandbox_id": sandbox_id, "task_id": spec.task_id})
        return handle

    def upload_workspace(self, handle: SandboxHandle, snapshot: Any | None) -> None:
        """Push the credential-free snapshot, then synthesise a git repository.

        Order is load-bearing: the repo synthesis must see the files. Running it
        from the container entrypoint (during ``create``) would commit an empty
        tree and leave the snapshot as untracked files afterwards.
        """
        self._require_current(handle)
        source = str(snapshot or handle.spec.project_path)
        for entry in workspace_mod.build_snapshot(source):
            self._api.upload_file(
                handle.sandbox_id,
                f"{_WORKSPACE}/{entry.path}",
                entry.data,
                workspace_mod.snapshot_upload_mode(),
            )
        self._run_foreground(handle.sandbox_id, _GIT_SYNTHESIS)
        self._emit("workspace_uploaded", {"sandbox_id": handle.sandbox_id})

    def exec(
        self,
        handle: SandboxHandle,
        command: list[str],
        env: dict[str, str] | None,
        exec_policy: Any | None,
    ) -> ExecHandle:
        self._require_current(handle)
        state = self._state_for(handle.sandbox_id)
        # Clear a STOPPED overlay: a cancelled turn must not make inspect report
        # STOPPED for the rest of the sandbox's life.
        state.overlay = None
        if isinstance(exec_policy, OpenSandboxTurnSpec):
            return self._exec_agent_turn(handle, command, env or {}, exec_policy)
        return self._exec_command(handle, command, env or {})

    def stream(self, exec_handle: ExecHandle) -> Iterator[SandboxEvent]:
        sandbox_id = exec_handle.sandbox_id
        state = self._state_for(sandbox_id)
        yield SandboxEvent(kind=SandboxEventKind.PROCESS_STARTED, sandbox_id=sandbox_id)
        yield SandboxEvent(
            kind=SandboxEventKind.COMMAND_STARTED,
            sandbox_id=sandbox_id,
            command_id=exec_handle.command_id,
        )
        terminal: SandboxEvent | None = None
        for event in state.transport or []:
            kind = event.get("type")
            if kind == "stdout":
                yield SandboxEvent(
                    kind=SandboxEventKind.STDOUT_CHUNK,
                    sandbox_id=sandbox_id,
                    command_id=exec_handle.command_id,
                    data=str(event.get("text") or ""),
                )
            elif kind == "stderr":
                text = str(event.get("text") or "")
                state.stderr += text
                yield SandboxEvent(
                    kind=SandboxEventKind.STDERR_CHUNK,
                    sandbox_id=sandbox_id,
                    command_id=exec_handle.command_id,
                    data=text,
                )
            elif kind == "execution_complete":
                terminal = SandboxEvent(
                    kind=SandboxEventKind.COMMAND_COMPLETED,
                    sandbox_id=sandbox_id,
                    command_id=exec_handle.command_id,
                    exit_code=0,
                )
            elif kind == "error":
                terminal = self._terminal_from_error(event, sandbox_id, exec_handle.command_id)
        if terminal is None:
            # The stream ended with no terminal event. Ask execd what happened;
            # never synthesise COMMAND_COMPLETED for a non-completion.
            terminal = self._terminal_from_status(sandbox_id, exec_handle.command_id)
        state.exit_code = terminal.exit_code
        state.terminal_kind = terminal.kind
        yield terminal
        yield SandboxEvent(
            kind=SandboxEventKind.PROCESS_EXITED,
            sandbox_id=sandbox_id,
            exit_code=terminal.exit_code,
        )

    def pause(self, exec_handle: ExecHandle) -> None:
        self._api.pause_sandbox(exec_handle.sandbox_id)
        self._state_for(exec_handle.sandbox_id).overlay = SandboxStatus.PAUSED
        self._emit("sandbox_paused", {"sandbox_id": exec_handle.sandbox_id})

    def resume(self, exec_handle: ExecHandle) -> None:
        self._api.resume_sandbox(exec_handle.sandbox_id)
        self._state_for(exec_handle.sandbox_id).overlay = None
        self._emit("sandbox_resumed", {"sandbox_id": exec_handle.sandbox_id})

    def stop(self, exec_handle: ExecHandle) -> None:
        state = self._state_for(exec_handle.sandbox_id)
        try:
            if state.is_pty and state.transport is not None:
                state.transport.shutdown(grace=5.0)
            else:
                self._api.interrupt_command(exec_handle.sandbox_id, exec_handle.command_id)
        except Exception:  # noqa: BLE001 - stop is best-effort
            pass
        # Interrupting a command does not change the sandbox state upstream, so
        # the overlay is what makes inspect honest here.
        state.overlay = SandboxStatus.STOPPED
        self._emit("sandbox_stopped", {"sandbox_id": exec_handle.sandbox_id})

    def collect_changes(self, handle: SandboxHandle) -> Any:
        """Return the supervisor's manifest, or ``None`` when there is none."""
        try:
            payload = self._api.download_file(
                handle.sandbox_id, f"{_WORKSPACE}/.openace-manifest.json"
            )
        except SandboxError:
            return None
        return workspace_mod.parse_manifest(payload)

    def collect_execution_evidence(self, handle: SandboxHandle) -> list[Any]:
        state = self._state.get(handle.sandbox_id)
        if state is None or not state.command_id:
            return []
        exit_code, signal, timed_out = self._resolve_outcome(handle.sandbox_id, state)
        reason = derive_terminal_reason(
            exit_code=exit_code,
            signal=signal,
            timed_out=timed_out,
            cancelled=state.terminal_kind == SandboxEventKind.COMMAND_CANCELLED,
            has_result=exit_code is not None or timed_out or signal is not None,
        )
        return [
            CommandExecutionEvidence(
                command_id=state.command_id,
                sandbox_id=handle.sandbox_id,
                sandbox_generation=handle.generation,
                cwd=handle.spec.project_path,
                exit_code=exit_code,
                signal=signal,
                terminal_reason=reason.value,
                started_at=_parse_timestamp(state.started_at),
                completed_at=_parse_timestamp(state.completed_at),
            )
        ]

    def destroy(self, handle: SandboxHandle) -> None:
        sandbox_id = handle.sandbox_id
        self._safe_destroy(sandbox_id)
        confirmed = self._confirm_terminal(sandbox_id)
        state = self._state.pop(sandbox_id, None)
        if state is not None and state.transport is not None and state.is_pty:
            try:
                state.transport.shutdown(grace=1.0)
            except Exception:  # noqa: BLE001 - best effort
                pass
        if confirmed:
            self._state[sandbox_id] = _SandboxState(overlay=SandboxStatus.DESTROYED)
            self._emit("sandbox_destroyed", {"sandbox_id": sandbox_id})
        else:
            # Leave the overlay unset so inspect reports the true state, and
            # signal the reconciler to retry.
            self._emit(
                "sandbox_destroy_unconfirmed",
                {"sandbox_id": sandbox_id, "reason_code": "destroy_unconfirmed"},
            )

    def destroy_attribution(self, sandbox_id: str, remote_session_id: str | None) -> None:
        """Destroy by persisted id alone, for the post-restart reconciler.

        Best-effort and idempotent: the sweep walks many rows and one bad row
        must never abort it.
        """
        try:
            self._api.delete_sandbox(sandbox_id)
        except Exception:  # noqa: BLE001 - never raises, by contract
            pass

    def inspect(self, handle: SandboxHandle) -> SandboxStatus:
        state = self._state.get(handle.sandbox_id)
        if state is not None and state.overlay is not None:
            return state.overlay
        record = self._api.get_sandbox(handle.sandbox_id)
        if record is None:
            return SandboxStatus.DESTROYED
        return policy_mod.map_state(str((record.get("status") or {}).get("state") or ""))

    # ── extensions beyond the Protocol ────────────────────────────────

    def get_transport(self, exec_handle: ExecHandle) -> Any:
        """Return the :class:`AgentTransport` for an agent turn."""
        state = self._state_for(exec_handle.sandbox_id)
        if not state.is_pty or state.transport is None:
            raise self._refuse(
                "get_transport is only valid for an agent turn started with an "
                "OpenSandboxTurnSpec",
                "not_an_agent_turn",
            )
        return state.transport

    def reconcile_orphans(self, live_sandbox_ids: Collection[str]) -> list[str]:
        """Destroy every sandbox of ours the control plane no longer claims.

        The metadata filter is not optional: on a shared OpenSandbox server an
        unfiltered sweep would destroy other teams' and other products'
        workloads.
        """
        destroyed: list[str] = []
        try:
            rows = self._api.list_sandboxes({"openace.provider": PROVIDER_NAME})
        except Exception:  # noqa: BLE001 - a failed sweep must not raise
            return destroyed
        live = set(live_sandbox_ids)
        for row in rows:
            sandbox_id = str(row.get("id") or "")
            metadata = row.get("metadata") or {}
            # Belt and braces: filter client-side too, in case a server ignores
            # the query parameter.
            if metadata.get("openace.provider") != PROVIDER_NAME:
                continue
            if not sandbox_id or sandbox_id in live:
                continue
            self._safe_destroy(sandbox_id)
            destroyed.append(sandbox_id)
        if destroyed:
            self._emit("sandbox_orphans_destroyed", {"sandbox_ids": destroyed})
        return destroyed

    # ── internals ─────────────────────────────────────────────────────

    def _build_create_body(self, spec: SandboxSpec, *, use_pool: bool) -> dict:
        try:
            body = policy_mod.build_create_request(
                spec,
                self._config,
                self._endpoint,
                generation=1,
                tenant=self._tenant,
                # Probes cannot have run yet for the first sandbox, so the
                # capability gate is evaluated against the post-probe set; a
                # probe failure destroys the sandbox before it is used.
                probes_passed=True,
            )
        except SandboxError as exc:
            raise self._refuse(str(exc), getattr(exc, "reason_code", "") or "spec_refused") from exc
        if use_pool:
            body = {
                key: value
                for key, value in body.items()
                # Upstream rejects these alongside poolRef.
                if key not in ("image", "networkPolicy", "volumes", "resourceLimits")
            }
            body["extensions"] = {"poolRef": self._endpoint.pool.pool_ref}
        return body

    def _run_probes(self, handle: SandboxHandle) -> None:
        """Verify the runtime class and the egress mode before trusting them.

        Upstream exposes no API reporting the effective secure runtime, so
        without this the runtime class is only an operator's word, and
        ``NETWORK_EGRESS_POLICY`` rests on a boolean nobody checked.
        """
        if self._probes_passed:
            return
        kernel = self._probe_kernel(handle.sandbox_id)
        expected = self._endpoint.runtime_class.lower()
        looks_gvisor = "gvisor" in kernel.lower()
        if expected.startswith("gvisor") and not looks_gvisor:
            raise self._refuse(
                f"runtime probe: endpoint declares {expected!r} but the sandbox "
                f"kernel does not identify as gVisor ({kernel[:80]!r})",
                "runtime_class_mismatch",
            )
        if expected.startswith("kata") and looks_gvisor:
            raise self._refuse(
                f"runtime probe: endpoint declares {expected!r} but the sandbox "
                "kernel identifies as gVisor",
                "runtime_class_mismatch",
            )
        if self._endpoint.attestations.egress_enforced:
            policy = self._api.egress_policy(handle.sandbox_id)
            if policy.get("defaultAction") != "deny":
                raise self._refuse(
                    f"egress probe: sidecar reports defaultAction="
                    f"{policy.get('defaultAction')!r}, expected 'deny'",
                    "egress_not_deny_default",
                )
            if policy.get("enforcementMode") != "dns+nft":
                raise self._refuse(
                    f"egress probe: sidecar reports enforcementMode="
                    f"{policy.get('enforcementMode')!r}; dns-only enforcement does "
                    "not stop a connection made to a bare IP",
                    "egress_mode_insufficient",
                )
        self._probes_passed = True

    def _probe_kernel(self, sandbox_id: str) -> str:
        probe = getattr(self._api, "proc_version", None)
        if callable(probe):
            return str(probe(sandbox_id))
        events = list(self._api.run_command(sandbox_id, {"command": "cat /proc/version"}))
        return "".join(str(e.get("text") or "") for e in events if e.get("type") == "stdout")

    def _exec_command(
        self, handle: SandboxHandle, command: list[str], env: dict[str, str]
    ) -> ExecHandle:
        policy = handle.spec.policy
        body = policy_mod.build_command_request(
            command,
            cwd=handle.spec.project_path or _WORKSPACE,
            envs=env,
            wall_clock_limit=getattr(policy, "wall_clock_limit", 0) if policy else 0,
            uid=self._endpoint.exec_uid,
            gid=self._endpoint.exec_gid,
        )
        events = list(self._api.run_command(handle.sandbox_id, body))
        command_id = self._command_id_from(events)
        state = self._state_for(handle.sandbox_id)
        state.command_id = command_id
        state.is_pty = False
        state.transport = events
        return ExecHandle(sandbox_id=handle.sandbox_id, command_id=command_id)

    def _exec_agent_turn(
        self,
        handle: SandboxHandle,
        command: list[str],
        env: dict[str, str],
        turn: OpenSandboxTurnSpec,
    ) -> ExecHandle:
        merged = policy_mod.build_env(
            handle.spec,
            self._config,
            self._endpoint,
            proxy_token=turn.proxy_token,
            extra=env,
        )
        pty_command = policy_mod.build_pty_command(command, env=merged)
        transport = PtyWebSocketTransport(
            self._api,
            sandbox_id=handle.sandbox_id,
            cwd=handle.spec.project_path or _WORKSPACE,
            command=pty_command,
            connect_factory=self._connect_factory,
        )
        transport.start()
        command_id = uuid.uuid4().hex
        state = self._state_for(handle.sandbox_id)
        state.command_id = command_id
        state.is_pty = True
        state.transport = transport
        return ExecHandle(sandbox_id=handle.sandbox_id, command_id=command_id)

    def _run_foreground(self, sandbox_id: str, command: str) -> None:
        list(
            self._api.run_command(
                sandbox_id,
                {
                    "command": command,
                    "cwd": _WORKSPACE,
                    "background": False,
                    "uid": self._endpoint.exec_uid,
                    "gid": self._endpoint.exec_gid,
                    "envs": {},
                },
            )
        )

    def _terminal_from_error(self, event: dict, sandbox_id: str, command_id: str) -> SandboxEvent:
        error = event.get("error") or {}
        evalue = str(error.get("evalue") or event.get("evalue") or "")
        if evalue.lstrip("-").isdigit():
            # A normal non-zero exit. Upstream emits `error` (not
            # execution_complete) for these; calling it SANDBOX_ERROR would turn
            # every failing test run into an infrastructure alarm.
            return SandboxEvent(
                kind=SandboxEventKind.COMMAND_COMPLETED,
                sandbox_id=sandbox_id,
                command_id=command_id,
                exit_code=int(evalue),
            )
        return SandboxEvent(
            kind=SandboxEventKind.SANDBOX_ERROR,
            sandbox_id=sandbox_id,
            command_id=command_id,
            data=evalue,
        )

    def _terminal_from_status(self, sandbox_id: str, command_id: str) -> SandboxEvent:
        status = self._command_status(sandbox_id, command_id)
        if status is None:
            return SandboxEvent(
                kind=SandboxEventKind.SANDBOX_ERROR, sandbox_id=sandbox_id, command_id=command_id
            )
        if status.get("running"):
            return SandboxEvent(
                kind=SandboxEventKind.COMMAND_TIMED_OUT,
                sandbox_id=sandbox_id,
                command_id=command_id,
            )
        raw = status.get("exit_code")
        return SandboxEvent(
            kind=SandboxEventKind.COMMAND_COMPLETED,
            sandbox_id=sandbox_id,
            command_id=command_id,
            exit_code=int(raw) if isinstance(raw, int) else None,
        )

    def _command_status(self, sandbox_id: str, command_id: str) -> dict | None:
        try:
            return self._api.command_status(sandbox_id, command_id)
        except SandboxError:
            # execd is unreachable — which is what a pod-level kill looks like.
            return None

    def _resolve_outcome(
        self, sandbox_id: str, state: _SandboxState
    ) -> tuple[int | None, int | None, bool]:
        """Return ``(exit_code, signal, timed_out)`` for the evidence row.

        Decoding ``128+n`` into ``signal`` before calling
        ``derive_terminal_reason`` is load-bearing: that function maps
        ``exit_code=137, signal=None`` to ``COMPLETED``, so without the decode a
        SIGKILL would be recorded as a clean finish.
        """
        timed_out = state.terminal_kind == SandboxEventKind.COMMAND_TIMED_OUT
        status = self._command_status(sandbox_id, state.command_id)
        if status is None:
            # execd unreachable. Ask the control plane what became of the pod.
            record = self._api.get_sandbox(sandbox_id)
            reason = str(((record or {}).get("status") or {}).get("reason") or "").lower()
            state_name = str(((record or {}).get("status") or {}).get("state") or "")
            if state_name == "Failed" and ("oom" in reason or "evict" in reason):
                return None, 9, False
            return None, None, timed_out
        state.started_at = str(status.get("started_at") or "")
        state.completed_at = str(status.get("finished_at") or "")
        raw = status.get("exit_code")
        exit_code = int(raw) if isinstance(raw, int) else state.exit_code
        if status.get("running") and exit_code is None:
            return None, None, True
        signal = None
        if exit_code is not None and 128 < exit_code < 192:
            signal = exit_code - 128
        return exit_code, signal, timed_out

    def _command_id_from(self, events: list[dict]) -> str:
        for event in events:
            if event.get("type") == "init" and event.get("text"):
                return str(event["text"])
        return uuid.uuid4().hex

    def _confirm_terminal(self, sandbox_id: str, attempts: int = 5) -> bool:
        """Poll until the sandbox is observably gone.

        Upstream's DELETE returns 204 and the sandbox then goes Stopping →
        Terminated, so a single read would almost always see Stopping and
        report an unconfirmed destroy for a teardown that in fact succeeded.
        """
        for _ in range(max(attempts, 1)):
            try:
                record = self._api.get_sandbox(sandbox_id)
            except SandboxError:
                return False
            if record is None:
                return True  # 404 is a confirmed teardown
            if str((record.get("status") or {}).get("state") or "") == "Terminated":
                return True
        return False

    def _safe_destroy(self, sandbox_id: str) -> None:
        try:
            self._api.delete_sandbox(sandbox_id)
        except Exception:  # noqa: BLE001 - destroy is idempotent and best-effort
            pass

    def _state_for(self, sandbox_id: str) -> _SandboxState:
        state = self._state.get(sandbox_id)
        if state is None:
            state = _SandboxState(overlay=None)
            self._state[sandbox_id] = state
        return state

    def _require_current(self, handle: SandboxHandle) -> None:
        """Reject a handle minted before a reconciliation bumped the generation."""
        if not is_current_generation(handle.generation, 1):
            raise self._refuse(
                f"handle generation {handle.generation} is stale for sandbox "
                f"{handle.sandbox_id}",
                "stale_generation",
            )

    def _refuse(self, message: str, reason_code: str) -> OpenSandboxError:
        self._emit("sandbox_refused", {"reason_code": reason_code, "detail": message})
        return OpenSandboxError(message, reason_code=reason_code)

    def _emit(self, name: str, data: dict) -> None:
        if self._event_sink is None:
            return
        payload = dict(data)
        payload.setdefault("provider", PROVIDER_NAME)
        payload.setdefault("tier", self._endpoint.tier)
        payload.setdefault("runtime_class", self._endpoint.runtime_class)
        payload.setdefault("tenant", self._tenant or "")
        try:
            self._event_sink(name, payload)
        except Exception:  # noqa: BLE001 - auditing must never break the run
            pass


def _parse_timestamp(value: str) -> datetime | None:
    """Parse execd's RFC3339 timestamps into the datetimes evidence expects.

    ``CommandStatusResponse`` reports ``started_at``/``finished_at`` as RFC3339
    strings while ``CommandExecutionEvidence`` types them as ``datetime`` —
    handing the string straight through would put the wrong type in the
    evidence row.
    """
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


_ = (shlex, TerminalReason)  # re-exported semantics live in policy/evidence modules
