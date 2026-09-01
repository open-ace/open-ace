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

import itertools
import re
import shlex
import time
import uuid
from collections.abc import Callable, Collection, Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.modules.workspace.autonomous.command_evidence.types import (
    CommandExecutionEvidence,
    TerminalReason,
    compute_output_digest,
    derive_terminal_reason,
)
from app.modules.workspace.autonomous.sandbox.agent_state_store import MAX_AGENT_STATE_BYTES
from app.modules.workspace.autonomous.sandbox.opensandbox import config as config_mod
from app.modules.workspace.autonomous.sandbox.opensandbox import policy as policy_mod
from app.modules.workspace.autonomous.sandbox.opensandbox import workspace as workspace_mod
from app.modules.workspace.autonomous.sandbox.opensandbox.client import HttpOpenSandboxApi
from app.modules.workspace.autonomous.sandbox.opensandbox.config import SandboxConfigError
from app.modules.workspace.autonomous.sandbox.opensandbox.transport import PtyWebSocketTransport
from app.modules.workspace.autonomous.sandbox.provider import (
    AGENT_STATE_CARRIED,
    SandboxError,
    is_current_generation,
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

if TYPE_CHECKING:  # pragma: no cover - annotations only
    from app.modules.workspace.autonomous.sandbox.opensandbox.client import OpenSandboxApi
    from app.modules.workspace.autonomous.sandbox.opensandbox.config import (
        EndpointConfig,
        SandboxBackendConfig,
    )

PROVIDER_NAME = "opensandbox"

# Where the agent's tree lives inside the sandbox.
_WORKSPACE = "/workspace"

# How much stderr tail to keep on the evidence row.
_STDERR_EXCERPT_CHARS = 4000

# Upper bound on remembered destroyed-sandbox ids.
_DESTROYED_MEMO_LIMIT = 4096

# Self-contained repo synthesis. Runs AFTER upload_workspace, never in the
# entrypoint: the entrypoint fires during create, before any file has landed,
# so it would commit an empty tree and leave the snapshot untracked. The
# identity is inline because refusal 9 mandates a read-only rootfs and a
# non-root uid, so there is no writable ~/.gitconfig and no ambient identity —
# git would fail with "Please tell me who you are" and produce a repo with no
# HEAD.
# The producer script and its output both live in /tmp, not /workspace, so
# neither can appear in the manifest they generate.
_MANIFEST_SCRIPT_PATH = "/tmp/openace-manifest.py"  # noqa: S108 - ephemeral sandbox
_MANIFEST_OUTPUT_PATH = "/tmp/openace-manifest.json"  # noqa: S108 - ephemeral sandbox

# Two destinations the shipped cluster NetworkPolicy denies, probed from inside
# the sandbox by _probe_cluster_egress.
#
# Resolution is separated from connection on purpose. A dropped packet and a
# failed DNS lookup both surface as an exception from `connect`, so folding them
# together would let a cluster with broken DNS report BLOCKED and buy the
# attestation for free — a false PASS on the one check a gVisor tier's egress
# rests on. `UNRESOLVED` is reported distinctly and refused.
#
# `connect` rather than `connect_ex`: the latter's behaviour on timeout differs
# across CPython versions (it can raise instead of returning an errno), and a
# probe whose failure mode is version-dependent is worse than no probe. Single
# quotes throughout so the whole program survives the double-quoted shell word,
# and no `$` or backtick anywhere for the shell to expand.
#
# Verified inside a real gVisor sandbox on a live cluster (runsc netstack,
# uid 1000, readOnlyRootFilesystem): a pod carrying the template's labels
# reports OPENACE_CLUSTER=BLOCKED, and an otherwise identical pod without them
# reports REACHABLE. That difference is what establishes the verdict comes from
# the NetworkPolicy rather than from an unroutable address.
_CLUSTER_EGRESS_PROBE = """python3 -c "
import socket

def probe(host, port):
    try:
        infos = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
    except Exception:
        return 'UNRESOLVED'
    s = socket.socket()
    s.settimeout(3)
    try:
        s.connect(infos[0][4])
        return 'REACHABLE'
    except Exception:
        return 'BLOCKED'
    finally:
        s.close()

print('OPENACE_METADATA=' + probe('169.254.169.254', 80))
print('OPENACE_CLUSTER=' + probe('kubernetes.default.svc.cluster.local', 443))
" """

# `safe.directory` on EVERY invocation, not just the commit. Under the shipped
# pod template the sandbox container runs as uid 1000 while the /workspace
# emptyDir is created root-owned, and the entrypoint's `chown -R` — which runs
# as that same uid 1000 — cannot change it. git then refuses:
#   fatal: detected dubious ownership in repository at '/workspace'
# exit 128, killing the synthesis and therefore every run, on BOTH tiers. Found
# on a live gVisor cluster; reproduced locally with git's own
# GIT_TEST_ASSUME_DIFFERENT_OWNER hook, which fails `git add` at exactly this
# point and passes with these `-c` flags in place.
#
# `-c` rather than relying on the entrypoint's global config so this works even
# if that line did not: it is the one command whose failure has no fallback.
_SAFE_DIR = f"-c safe.directory={_WORKSPACE}"
# Where the CLI keeps its conversation transcript inside the sandbox (#3237).
#
# The directory name is the CLI's encoding of its own cwd, and inside the
# sandbox that cwd is always _WORKSPACE — `_exec_command` passes it — so this
# is a constant rather than a host-derived path. Verified against a real CLI
# (2.1.170): the directory it created matched
# `AutonomousAgentRunner._encode_project_path("/workspace")` exactly, and
# test_the_transcript_dir_matches_what_the_cli_really_writes pins both halves
# so they cannot drift apart.
_AGENT_STATE_DIR = "/home/agent/.claude/projects/-workspace"


def _agent_state_path(cli_session_id: str) -> str | None:
    """The transcript path for *cli_session_id*, or ``None`` if unusable.

    The id is NOT trusted. It reaches here from ``agent_sessions.cli_session_id``,
    which ``_extract_stream_session_id`` fills with whatever the sandbox printed
    on its own stdout — and under this backend the sandbox is the untrusted
    party by construction. Without this, a compromised sandbox could name
    ``../../../../workspace/.git/hooks/pre-commit`` and have the NEXT turn's
    sandbox receive attacker-chosen bytes at that path.

    ``AgentStateStore`` already applies exactly this reasoning to the same class
    of database-sourced id ("neither is a trusted path fragment"); this is the
    one place that had not. Refused rather than sanitised, for the same reason:
    rewriting an id silently changes which file is addressed.
    """
    candidate = str(cli_session_id or "").strip()
    if not _SAFE_SESSION_ID.match(candidate):
        return None
    return f"{_AGENT_STATE_DIR}/{candidate}.jsonl"


# Session ids are uuids in practice; this is deliberately a little wider so a
# CLI that changes its id format does not silently stop carrying history, while
# still admitting no separator, no dot-segment and no absolute path.
_SAFE_SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

_GIT_SYNTHESIS = (
    f"git {_SAFE_DIR} init -q && git {_SAFE_DIR} add -A && "
    f"git {_SAFE_DIR} -c user.name='Open ACE' -c user.email='agent@open-ace.invalid' "
    "-c commit.gpgsign=false commit -q -m 'snapshot'"
)
# No --allow-empty on purpose: if upload_workspace's uploads silently failed,
# `git add -A` stages nothing and an allow-empty commit would succeed, producing
# a valid-looking repository with an empty tree — the original failure made
# undetectable. Without it the commit fails and _run_foreground raises.


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
    # Set when the sandbox itself reached Failed without an OOM reason: the run
    # crashed rather than never having run.
    crashed: bool = False


class OpenSandboxProvider:
    """SandboxProvider over OpenSandbox's Kubernetes runtime."""

    # HOME is an emptyDir that dies with the pod, so the CLI transcript has to
    # be moved in and out by hand or `--resume` finds nothing. See
    # export_agent_state / import_agent_state below (#3237).
    agent_state_persistence = AGENT_STATE_CARRIED

    def __init__(
        self,
        config: SandboxBackendConfig,
        *,
        api_factory: Callable[[EndpointConfig], OpenSandboxApi] = HttpOpenSandboxApi,
        tenant: str | None = None,
        project_path: str | None = None,
        event_sink: Callable[[str, dict], None] | None = None,
        connect_factory: Callable[[str, dict], Any] | None = None,
        destroy_poll_interval: float = 0.5,
        generation: int = 1,
    ) -> None:
        self._config = config
        self._api_factory = api_factory
        self._tenant = tenant
        self._project_path = project_path
        self._event_sink = event_sink
        # Injected through to PtyWebSocketTransport so unit tests drive a fake
        # connection instead of opening a real socket.
        self._connect_factory = connect_factory
        self._destroy_poll_interval = destroy_poll_interval
        # The workflow's sandbox_generation. Bumped by the reconciler on every
        # restart sweep, so a handle minted before a bump must not operate on a
        # sandbox created after it. Comparing against a literal 1 would accept
        # exactly the stale handles this check exists to reject, and refuse the
        # legitimate ones.
        self._generation = generation
        self._endpoint = config.endpoint_for(tenant=tenant, project_path=project_path)
        self._api = api_factory(self._endpoint)
        self._state: dict[str, _SandboxState] = {}
        # Sandbox ids whose teardown we actually observed, so inspect() can
        # answer DESTROYED without keeping a full state object alive.
        self._destroyed: set[str] = set()
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
            generation=self._generation,
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
        if snapshot is not None and not isinstance(snapshot, str):
            raise self._refuse(
                f"upload_workspace expects a worktree path or None, got "
                f"{type(snapshot).__name__}",
                "invalid_snapshot",
            )
        source = snapshot or handle.spec.project_path
        for entry in workspace_mod.build_snapshot(source):
            self._api.upload_file(
                handle.sandbox_id,
                f"{_WORKSPACE}/{entry.path}",
                entry.data,
                workspace_mod.snapshot_upload_mode(),
            )
        self._run_foreground(
            handle.sandbox_id, _GIT_SYNTHESIS, reason_code="workspace_setup_failed"
        )
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
        # The two branches produce different objects: /command yields SSE dicts,
        # a PTY turn yields them from the live transport. iter_events() gives
        # both the same shape so there is one mapping below.
        source = (
            state.transport.iter_events()
            if state.is_pty and state.transport is not None
            else (state.transport or [])
        )
        for event in source:
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
            elif kind == "status" and str(event.get("text")) == "timeout":
                terminal = SandboxEvent(
                    kind=SandboxEventKind.COMMAND_TIMED_OUT,
                    sandbox_id=sandbox_id,
                    command_id=exec_handle.command_id,
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
        # Upstream's pause is asynchronous (202, then Pausing -> Paused). Setting
        # the overlay on acceptance would report PAUSED for a sandbox that is
        # still Running if the pause later fails.
        self._api.pause_sandbox(exec_handle.sandbox_id)
        if self._confirm_state(exec_handle.sandbox_id, "Paused"):
            self._state_for(exec_handle.sandbox_id).overlay = SandboxStatus.PAUSED
            self._emit("sandbox_paused", {"sandbox_id": exec_handle.sandbox_id})
        else:
            self._emit(
                "sandbox_pause_unconfirmed",
                {"sandbox_id": exec_handle.sandbox_id, "reason_code": "pause_unconfirmed"},
            )

    def resume(self, exec_handle: ExecHandle) -> None:
        self._api.resume_sandbox(exec_handle.sandbox_id)
        if self._confirm_state(exec_handle.sandbox_id, "Running"):
            self._state_for(exec_handle.sandbox_id).overlay = None
            self._emit("sandbox_resumed", {"sandbox_id": exec_handle.sandbox_id})
        else:
            self._emit(
                "sandbox_resume_unconfirmed",
                {"sandbox_id": exec_handle.sandbox_id, "reason_code": "resume_unconfirmed"},
            )

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
        """Produce and return this sandbox's ChangeSet as ``(entries, deleted)``.

        Uploads the producer, runs it inside the sandbox, and downloads its
        output. A failure **raises** rather than returning ``None``: an empty
        result is indistinguishable from "the agent changed nothing", and
        silently discarding a run's work product is precisely the quiet failure
        this backend exists to avoid.
        """
        self._require_current(handle)
        self._api.upload_file(
            handle.sandbox_id, _MANIFEST_SCRIPT_PATH, _manifest_producer_source(), 0o755
        )
        self._run_foreground(
            handle.sandbox_id,
            f"python3 {_MANIFEST_SCRIPT_PATH}",
            reason_code="manifest_producer_failed",
        )
        try:
            payload = self._api.download_file(handle.sandbox_id, _MANIFEST_OUTPUT_PATH)
        except SandboxError as exc:
            raise self._refuse(
                f"manifest producer left no output at {_MANIFEST_OUTPUT_PATH}: {exc}",
                "manifest_missing",
            ) from exc
        entries, deleted = workspace_mod.parse_manifest(payload)
        self._emit(
            "changeset_collected",
            {"sandbox_id": handle.sandbox_id, "files": len(entries), "deleted": len(deleted)},
        )
        return entries, deleted

    def apply_changes(self, handle: SandboxHandle, worktree_path: str) -> None:
        """Validate a collected ChangeSet and apply it to the trusted worktree."""
        entries, _ = self.collect_changes(handle)
        # The producer cannot report removals — it reports what is present, and
        # the sandbox has no baseline to diff against. Deriving them here is
        # what stops a file the agent deleted from silently surviving in the
        # trusted worktree while the commit that follows looks correct.
        deleted = workspace_mod.derive_deletions(entries, worktree_path=worktree_path)
        workspace_mod.apply_changeset(
            entries,
            root=worktree_path,
            limits=self._config.changeset_limits,
            fetch=lambda path: self._api.download_file(handle.sandbox_id, f"{_WORKSPACE}/{path}"),
            deleted=deleted,
        )
        self._emit("changeset_applied", {"sandbox_id": handle.sandbox_id})

    def export_agent_state(self, handle: SandboxHandle, *, cli_session_id: str) -> bytes | None:
        """Read the CLI transcript out before the sandbox is destroyed (#3237).

        Returns ``None`` when there is nothing to carry — no session id (the
        stream never yielded one) or no transcript at that path (the turn never
        started a conversation). Neither is an error, and neither should cost
        the caller a completed milestone.
        """
        path = _agent_state_path(cli_session_id)
        if path is None:
            return None
        try:
            blob = self._api.download_file(handle.sandbox_id, path)
        except SandboxError:
            return None
        # The cap is re-checked at the store, but a blob that never fits must
        # not be handed onward either. HOME is an emptyDir with a 1Gi
        # sizeLimit, so a runaway transcript is bounded by the pod, not by
        # anything here — and `download_file` buffers the whole body.
        if len(blob) > MAX_AGENT_STATE_BYTES:
            self._emit(
                "agent_state_too_large",
                {"sandbox_id": handle.sandbox_id, "bytes": len(blob)},
            )
            return None
        return blob

    def import_agent_state(
        self, handle: SandboxHandle, *, cli_session_id: str, blob: bytes
    ) -> None:
        """Place the transcript where ``--resume`` will look for it (#3237).

        Only this one file. Not ``.claude.json``, not ``.credentials.json``,
        not settings: the sandbox environment is constructed, never inherited,
        and a credential must not round-trip through the control plane.
        Verified against a real CLI that restoring this file alone into an
        otherwise empty HOME is sufficient for ``--resume`` to resolve, with
        the original session id preserved.
        """
        path = _agent_state_path(cli_session_id)
        if path is None:
            raise self._refuse(
                f"agent state id {cli_session_id!r} is not a plain path component; "
                "refusing to build a transcript path from it",
                "agent_state_unavailable",
            )
        self._api.upload_file(handle.sandbox_id, path, blob, 0o600)

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
            has_result=(exit_code is not None or timed_out or signal is not None or state.crashed),
        )
        stderr_digest = compute_output_digest(state.stderr) if state.stderr else None
        return [
            CommandExecutionEvidence(
                command_id=state.command_id,
                sandbox_id=handle.sandbox_id,
                sandbox_generation=handle.generation,
                # Where the command actually ran, which is the sandbox tree —
                # not the host path the spec carries.
                cwd=_WORKSPACE,
                exit_code=exit_code,
                signal=signal,
                terminal_reason=reason.value,
                stderr_digest=stderr_digest,
                output_excerpt=state.stderr[-_STDERR_EXCERPT_CHARS:] if state.stderr else "",
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
            self._destroyed.add(sandbox_id)
            if len(self._destroyed) > _DESTROYED_MEMO_LIMIT:
                # Bounded: this provider can outlive thousands of sandboxes in a
                # long-running scheduler, and an unbounded set is a slow leak.
                self._destroyed.pop()
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
        must never abort it. The frozen ``#2022`` Protocol types this ``None``,
        so callers that need to know whether teardown actually happened use
        :meth:`destroy_attribution_checked` instead.
        """
        self.destroy_attribution_checked(sandbox_id, remote_session_id)

    def destroy_attribution_checked(self, sandbox_id: str, remote_session_id: str | None) -> bool:
        """:meth:`destroy_attribution`, but reporting whether it worked.

        An extension beyond the Protocol — the same shape as
        :meth:`get_transport` — because widening the Protocol's return type
        would change the ``#2022`` contract for every provider.

        Reporting is the point. Swallowing the error let the scheduler clear
        ``sandbox_id`` on a transient API outage, discarding the one piece of
        information a later retry needs: the sandbox keeps running until its
        TTL and nothing left in the database can name it.

        Searches EVERY configured endpoint, not just this provider's own. The
        post-restart sweep rebuilds a provider from a workflow row that records
        the provider *name* and nothing about the tier, so it resolves to
        ``default_tier``. Any deployment with more than one tier (``tenant_tiers``
        routing tenants to separate endpoints) therefore resolves to the wrong
        server for every non-default tenant — and since ``delete_sandbox`` treats
        404 as success, deleting a sandbox's id against a server that never held
        it *reported success*. The scheduler then cleared the ids while the real sandbox ran
        on to its TTL: exactly the leak this reporting exists to prevent.

        Locating the sandbox before deleting it is what makes the answer mean
        something. Finding it nowhere is success — it is already gone.
        """
        found_anywhere = False
        errors: list[str] = []
        inconclusive: list[str] = []
        for tier, endpoint in self._config.endpoints.items():
            api = self._api if endpoint is self._endpoint else self._api_factory(endpoint)
            try:
                if api.get_sandbox(sandbox_id) is None:
                    continue
                found_anywhere = True
                api.delete_sandbox(sandbox_id)
                # DELETE is accepted, not applied: upstream goes Stopping then
                # Terminated over successive reads, and a server that ignored
                # the request answers exactly the same. Without this the method
                # reported success for a sandbox still Running, and the
                # scheduler cleared the only id that could name it again.
                if not self._confirm_terminal(sandbox_id, api=api):
                    errors.append(f"{tier}: delete not confirmed terminal")
            except SandboxConfigError as exc:
                # A config fault here (an unset API-key env var in THIS process —
                # the scheduler is its own unit and need not share the web
                # process's environment) is deterministic and will recur on every
                # sweep, so reporting it as a plain retryable failure pinned the
                # row `running` forever while each sweep re-armed the TTL reaper.
                #
                # But it is NOT evidence of absence, which is what an earlier
                # version of this concluded — and that reasoning contradicted
                # the very sentence above: if the scheduler's environment can
                # differ from the web process's, then the web process may well
                # have created the sandbox through exactly this endpoint. So the
                # endpoint is recorded as *inconclusive*: harmless when the
                # sandbox is found and torn down elsewhere, and fatal to the
                # "already gone" conclusion when it is not.
                inconclusive.append(f"{tier}: {exc}")
                self._emit(
                    "sandbox_endpoint_unusable",
                    {"sandbox_id": sandbox_id, "tier": tier, "error": str(exc)},
                )
            except Exception as exc:  # noqa: BLE001 - report, never raise
                errors.append(f"{tier}: {exc}")
        if errors:
            self._emit(
                "sandbox_destroy_attribution_failed",
                {"sandbox_id": sandbox_id, "errors": errors},
            )
            return False
        if not found_anywhere:
            if inconclusive:
                # Never seen anywhere we could actually ask. Concluding "already
                # gone" here is how a live sandbox loses the only id that names
                # it; keep the attribution and let an operator fix the
                # credentials.
                self._emit(
                    "sandbox_destroy_attribution_failed",
                    {"sandbox_id": sandbox_id, "errors": inconclusive},
                )
                return False
            self._emit("sandbox_destroy_attribution_absent", {"sandbox_id": sandbox_id})
        return True

    def inspect(self, handle: SandboxHandle) -> SandboxStatus:
        if handle.sandbox_id in self._destroyed:
            return SandboxStatus.DESTROYED
        state = self._state.get(handle.sandbox_id)
        if state is not None and state.overlay is not None:
            return state.overlay
        record = self._api.get_sandbox(handle.sandbox_id)
        if record is None:
            return SandboxStatus.DESTROYED
        return policy_mod.map_state(str((record.get("status") or {}).get("state") or ""))

    # ── extensions beyond the Protocol ────────────────────────────────

    def agent_turn_policy(self, *, prompt: str, model: str, env: Mapping[str, str]) -> Any:
        """Build the ``exec_policy`` that selects the PTY agent-turn path.

        The runner cannot construct this itself without importing an
        OpenSandbox-specific type into ``agent_runner``, and it cannot pass
        ``None`` either: ``exec`` would then take the ``POST /command``
        foreground branch and the very next ``get_transport()`` would refuse
        the state with ``not_an_agent_turn``. Every provider that ``_run_local``
        can select therefore answers this — Legacy returns ``None``, which is
        exactly the value it was already being given.
        """
        return OpenSandboxTurnSpec(
            prompt=prompt,
            model=model,
            proxy_token=str(env.get("OPENACE_PROXY_TOKEN") or ""),
        )

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
        installation = self._config.installation_id
        if not installation:
            # Cannot happen through parse_backend_config, which requires it —
            # but a hand-built config in a test or a future caller could get
            # here, and an unscoped sweep on a shared server deletes other
            # installations' running sandboxes. Refuse to sweep at all.
            return destroyed
        query = {
            "openace.provider": PROVIDER_NAME,
            policy_mod.INSTALLATION_METADATA_KEY: installation,
        }
        try:
            rows = self._api.list_sandboxes(query)
        except Exception:  # noqa: BLE001 - a failed sweep must not raise
            return destroyed
        live = set(live_sandbox_ids)
        for row in rows:
            sandbox_id = str(row.get("id") or "")
            metadata = row.get("metadata") or {}
            # Belt and braces: filter client-side too, in case a server ignores
            # the query parameters. The installation check is the load-bearing
            # half — a server that ignored the filter would otherwise hand us
            # another deployment's sandboxes, and every one of them looks
            # "unclaimed" from here because its workflow rows live in a
            # different database.
            if metadata.get("openace.provider") != PROVIDER_NAME:
                continue
            if metadata.get(policy_mod.INSTALLATION_METADATA_KEY) != installation:
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
                generation=self._generation,
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
        """Check the runtime class and the egress mode before trusting them.

        Upstream exposes no API reporting the effective secure runtime, so
        without this the runtime class is only an operator's word, and
        ``NETWORK_EGRESS_POLICY`` rests on a boolean nobody checked.

        WHAT THIS ACTUALLY ESTABLISHES, per direction — the two are not
        symmetric, and the weaker direction is the one guarding the tier whose
        egress allowlist is the reason to choose it:

        * gVisor-declared: POSITIVE. gVisor's kernel identifies itself in
          ``/proc/version``, so a tier claiming gVisor and not getting it is
          caught.
        * Kata-declared: NEGATIVE ONLY. It rules out gVisor. Kata boots a real
          kernel in a VM whose ``/proc/version`` is indistinguishable from plain
          runc's, so this cannot tell Kata from an unisolated container. A
          positive check would need a hypervisor signal (DMI product name), and
          that differs per Kata hypervisor (qemu / fc / clh) — shipping one
          untested risks refusing legitimate deployments, so the asymmetry is
          documented rather than papered over. ``docs/sandbox-backends.md`` §5
          states the same limitation.
        """
        if self._probes_passed:
            return
        kernel = self._probe_kernel(handle.sandbox_id)
        expected = self._endpoint.runtime_class.lower()
        looks_gvisor = "gvisor" in kernel.lower()
        # Same classifier the config validation uses, so the names the two agree
        # to recognise cannot drift. Matching on `startswith("gvisor")` alone
        # meant "runsc" — gVisor's OWN handler name — matched neither branch:
        # the probe verified nothing and then set probes_passed, off which
        # NAMESPACE_ISOLATION is granted.
        family = config_mod.runtime_family(expected)
        if not family:
            # Unreachable through parse_backend_config, which refuses an
            # unrecognised runtime_class — but a hand-built config must not be
            # able to buy a capability with an unverifiable name.
            raise self._refuse(
                f"runtime probe: endpoint declares runtime_class {expected!r}, which this "
                "backend cannot verify; refusing rather than passing a probe that "
                "checked nothing",
                "runtime_class_unverifiable",
            )
        if family == "gvisor" and not looks_gvisor:
            raise self._refuse(
                f"runtime probe: endpoint declares {expected!r} but the sandbox "
                f"kernel does not identify as gVisor ({kernel[:80]!r})",
                "runtime_class_mismatch",
            )
        if family == "kata" and looks_gvisor:
            raise self._refuse(
                f"runtime probe: endpoint declares {expected!r} but the sandbox "
                "kernel identifies as gVisor",
                "runtime_class_mismatch",
            )
        att = self._endpoint.attestations
        if att.egress_cni_default_deny or att.metadata_cidr_blocked:
            self._probe_cluster_egress(handle.sandbox_id)
        if att.egress_enforced:
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
        """Read every place gVisor may identify itself, not just /proc/version.

        Current runsc does NOT put a marker in /proc/version — on
        release-20260112 it reports a plain ``Linux version 4.4.0 #1 SMP ...``,
        so probing that file alone REFUSED a correctly-deployed gVisor tier with
        ``runtime_class_mismatch``. Verified on a live gVisor cluster. The
        markers live in ``/proc/cmdline`` (``BOOT_IMAGE=/vmlinuz-4.4.0-gvisor``)
        and in ``dmesg`` (``Starting gVisor...``), so all three are read and
        concatenated; any one carrying the marker is enough.

        Failures are swallowed per-source on purpose: ``dmesg`` is frequently
        unavailable to an unprivileged process, and a missing optional source
        must not become a refusal.
        """
        probe = getattr(self._api, "proc_version", None)
        if callable(probe):
            return str(probe(sandbox_id))
        collected: list[str] = []
        for command in ("cat /proc/version", "cat /proc/cmdline", "dmesg 2>/dev/null | head -20"):
            try:
                events = list(self._api.run_command(sandbox_id, {"command": command}))
            except SandboxError:
                continue
            collected.append(
                "".join(str(e.get("text") or "") for e in events if e.get("type") == "stdout")
            )
        return "\n".join(collected)

    def _probe_cluster_egress(self, sandbox_id: str) -> None:
        """Check the cluster NetworkPolicy from inside the sandbox.

        The counterpart of the sidecar probe below, for the mechanism attested
        by ``egress_cni_default_deny`` / ``metadata_cidr_blocked``. It matters
        more here than there: on a gVisor tier this NetworkPolicy is the ONLY
        egress control, and a manifest that was never applied — or whose
        podSelector matches no pod, which is a mistake this repository has
        already made once — leaves the attestation asserted with nothing behind
        it.

        WHAT THIS ESTABLISHES, precisely:

        * The metadata address and the Kubernetes API server are unreachable
          from the sandbox. Those are two destinations the shipped policy denies
          and that exist to be denied — the API server in particular is present
          in every cluster and sits inside the service CIDR, so reaching it
          proves the private-range denial is not in force.
        * It does NOT prove the shipped manifest is what produced that result.
          An unroutable address is unreachable whether or not a policy exists.
          The probe is a NEGATIVE control: it catches an unenforced attestation,
          it does not independently verify an enforced one. On a cluster with no
          metadata service that leg cannot discriminate at all — verified live,
          where it read BLOCKED both with and without the policy applied; the
          API-server leg is the one that separated the two.

        A probe that cannot run is a refusal, not a shrug. Unlike
        ``_probe_kernel``'s optional sources, this is the only check standing
        between a CNI tier and unrestricted egress — so a missing verdict and an
        unresolvable destination are both refusals rather than passes.
        """
        try:
            events = list(self._api.run_command(sandbox_id, {"command": _CLUSTER_EGRESS_PROBE}))
        except SandboxError as exc:
            raise self._refuse(
                f"egress probe: could not run the cluster-egress check ({exc}); refusing "
                "rather than trusting egress_cni_default_deny unverified",
                "egress_probe_unavailable",
            ) from exc
        output = "".join(
            str(e.get("text") or "") for e in events if e.get("type") in ("stdout", "stderr")
        )
        reachable = [
            name
            for name, token in (
                ("the instance metadata service", "OPENACE_METADATA="),
                ("the Kubernetes API server", "OPENACE_CLUSTER="),
            )
            if f"{token}REACHABLE" in output
        ] or None
        missing = [
            token for token in ("OPENACE_METADATA=", "OPENACE_CLUSTER=") if token not in output
        ]
        if missing:
            raise self._refuse(
                f"egress probe: the cluster-egress check produced no verdict for {missing} "
                f"(output {output[:200]!r}); python3 must be on PATH in the sandbox image "
                "for this attestation to be verifiable",
                "egress_probe_unavailable",
            )
        if "OPENACE_CLUSTER=UNRESOLVED" in output:
            # DNS failed, so the check could not discriminate. Folding this into
            # BLOCKED would grant the attestation to a cluster whose resolver is
            # broken — and such a sandbox could not reach its LLM proxy either.
            raise self._refuse(
                "egress probe: kubernetes.default.svc.cluster.local did not resolve, so the "
                "cluster-egress check proves nothing; the sandbox has no working DNS, which "
                "would also stop the agent reaching its LLM proxy",
                "egress_probe_unavailable",
            )
        if reachable:
            raise self._refuse(
                f"egress probe: the sandbox can reach {' and '.join(reachable)}, so the "
                "cluster NetworkPolicy is not restricting this pod. Apply "
                "k8s/extras/opensandbox/networkpolicy.yaml, confirm its podSelector matches "
                "the sandbox pod labels, and check that your service CIDR falls inside one "
                "of its excluded ranges",
                "egress_cni_not_enforced",
            )

    def _exec_command(
        self, handle: SandboxHandle, command: list[str], env: dict[str, str]
    ) -> ExecHandle:
        policy = handle.spec.policy
        body = policy_mod.build_command_request(
            command,
            # NOT handle.spec.project_path — that is the control plane's host
            # path (agent_runner passes the same value it uses for the local
            # Popen's cwd). Inside the container the tree is at _WORKSPACE.
            cwd=_WORKSPACE,
            envs=env,
            wall_clock_limit=getattr(policy, "wall_clock_limit", 0) if policy else 0,
            uid=self._endpoint.exec_uid,
            gid=self._endpoint.exec_gid,
            drop_credentials=not self._endpoint.attestations.execd_runs_as_exec_identity,
        )
        events = self._api.run_command(handle.sandbox_id, body)
        # Read only as far as the `init` event, which is the sole source of the
        # command id. Draining the whole stream here would block exec() until
        # the command finished — nothing would stream incrementally, and stop()
        # could never be called because the caller would still be inside exec().
        command_id, pending = self._read_until_init(events)
        state = self._state_for(handle.sandbox_id)
        state.command_id = command_id
        state.is_pty = False
        state.transport = pending
        return ExecHandle(sandbox_id=handle.sandbox_id, command_id=command_id)

    @staticmethod
    def _read_until_init(events: Any) -> tuple[str, Any]:
        """Consume up to and including ``init``; return its id plus the rest."""
        iterator = iter(events)
        seen: list[dict] = []
        command_id = ""
        for event in iterator:
            seen.append(event)
            if event.get("type") == "init" and event.get("text"):
                command_id = str(event["text"])
                break
        return command_id or uuid.uuid4().hex, itertools.chain(seen, iterator)

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
        # Fail closed before the agent starts rather than after it has burned a
        # turn failing to reach its own proxy.
        policy_mod.assert_proxy_reachable(merged, self._endpoint)
        # argv[0] was resolved by shutil.which on the CONTROL PLANE. That host
        # path has no meaning inside the image, so resolve by name there — the
        # container's PATH is the only authority on where its own CLI lives.
        pty_command = policy_mod.build_pty_command(
            [Path(command[0]).name, *command[1:]] if command else command, env=merged
        )
        transport = PtyWebSocketTransport(
            self._api,
            sandbox_id=handle.sandbox_id,
            cwd=_WORKSPACE,
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

    def _run_foreground(self, sandbox_id: str, command: str, *, reason_code: str) -> str:
        """Run a control-plane command and FAIL CLOSED on a non-zero exit.

        Discarding the result would let workspace setup fail silently and leave
        the agent running against a tree that was never prepared.
        """
        body: dict[str, Any] = {
            "command": command,
            "cwd": _WORKSPACE,
            "background": False,
            "envs": {},
        }
        # Same rule as _exec_command: when execd already runs AS the exec
        # identity it cannot switch credentials to it, and every setup command
        # dies `fork/exec ...: operation not permitted`. This is the call that
        # runs the repo synthesis, so getting it wrong means no run gets past
        # upload_workspace.
        if not self._endpoint.attestations.execd_runs_as_exec_identity:
            body["uid"] = self._endpoint.exec_uid
            body["gid"] = self._endpoint.exec_gid
        events = list(self._api.run_command(sandbox_id, body))
        if not events:
            # A command that produced NO events is not an observed success.
            # Real execd always emits at least an `init` frame, so an empty
            # stream means we failed to read the protocol — which is exactly
            # what happened while the SSE parser only understood `data:` lines:
            # every setup command "succeeded" without evidence, and the agent
            # ran against a tree that may never have been prepared. Fail closed
            # here too, so a future protocol drift cannot resurrect that.
            raise self._refuse(
                f"sandbox setup command produced no events: {command[:80]}", reason_code
            )
        stdout = "".join(str(e.get("text") or "") for e in events if e.get("type") == "stdout")
        for event in events:
            if event.get("type") != "error":
                continue
            detail = (event.get("error") or {}).get("evalue", "")
            raise self._refuse(
                f"sandbox setup command failed ({detail}): {command[:80]}", reason_code
            )
        return stdout

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
        if state.is_pty:
            # execd never saw this command id (a PTY session is not a command),
            # so command_status would 404 and the run would always record
            # MISSING_RESULT. The transport holds the exit frame's code.
            transport = state.transport
            break_reason = getattr(transport, "protocol_break_reason", "") if transport else ""
            if break_reason:
                return None, None, False  # -> CRASH via has_result below
            exit_code = getattr(transport, "returncode", None)
            if exit_code is None:
                return None, None, timed_out
            signal = exit_code - 128 if 128 < exit_code < 192 else None
            return exit_code, signal, timed_out
        status = self._command_status(sandbox_id, state.command_id)
        if status is None:
            # execd unreachable. Ask the control plane what became of the pod.
            record = self._api.get_sandbox(sandbox_id)
            reason = str(((record or {}).get("status") or {}).get("reason") or "").lower()
            state_name = str(((record or {}).get("status") or {}).get("state") or "")
            if state_name == "Failed" and ("oom" in reason or "evict" in reason):
                return None, 9, False
            if state_name == "Failed":
                # Crashed, not "never ran". has_result=True with no exit code
                # is what derive_terminal_reason maps to CRASH.
                state.crashed = True
                return None, None, False
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

    def _confirm_terminal(
        self, sandbox_id: str, attempts: int = 5, api: OpenSandboxApi | None = None
    ) -> bool:  # noqa: D401
        """Poll until the sandbox is observably gone, on the server that HELD it.

        Upstream's DELETE returns 204 and the sandbox then goes Stopping →
        Terminated, so a single read would almost always see Stopping and
        report an unconfirmed destroy for a teardown that in fact succeeded.

        *api* is not optional in spirit: reading ``self._api`` unconditionally
        meant that when ``destroy_attribution_checked`` swept a non-default
        tier, the confirmation asked the DEFAULT server about a sandbox that
        had never lived there, got a 404, and scored it as a confirmed
        teardown — turning the guard into the opposite of a check for exactly
        the multi-tier case the sweep exists to handle.
        """
        client = api if api is not None else self._api
        for attempt in range(max(attempts, 1)):
            if attempt:
                time.sleep(self._destroy_poll_interval)
            try:
                record = client.get_sandbox(sandbox_id)
            except SandboxError:
                return False
            if record is None:
                return True  # 404 is a confirmed teardown
            state = str((record.get("status") or {}).get("state") or "")
            if state in ("Terminated", "Stopping"):
                # `Stopping` counts. Kubernetes pod deletion runs to
                # terminationGracePeriodSeconds (30 by default), so waiting for
                # `Terminated` would mean every successful teardown reported
                # unconfirmed and asked the reconciler to retry — training
                # operators to ignore the one signal that matters. A 204 plus an
                # observed `Stopping` means the delete was accepted and the
                # sandbox is going away, which is honest enough for inspect and
                # does not block the caller for half a minute. A sandbox still
                # `Running` after the budget is genuinely unconfirmed.
                return True
        return False

    def _confirm_state(self, sandbox_id: str, expected: str, attempts: int = 5) -> bool:
        """Poll until the sandbox reports *expected*, or give up."""
        for attempt in range(max(attempts, 1)):
            if attempt:
                time.sleep(self._destroy_poll_interval)
            try:
                record = self._api.get_sandbox(sandbox_id)
            except SandboxError:
                return False
            if record is None:
                return False
            if str((record.get("status") or {}).get("state") or "") == expected:
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
        if not is_current_generation(handle.generation, self._generation):
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


def _manifest_producer_source() -> bytes:
    """Read the producer script that runs inside the sandbox."""
    return (Path(__file__).with_name("manifest_producer.py")).read_bytes()


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
