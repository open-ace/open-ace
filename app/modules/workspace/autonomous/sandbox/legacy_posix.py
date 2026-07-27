"""LegacyPosixProvider — SandboxProvider over the local POSIX path (#2022 P3a).

Phase 3a implements the SandboxProvider contract on the REAL local execution
mechanics: ``subprocess.Popen(start_new_session=True)`` for spawn, the process
group for pause/resume/stop (SIGSTOP/SIGCONT/SIGTERM→SIGKILL, mirroring
``agent_runner``), and the OS exit code for the normalized lifecycle events.

Scope (#2022 P3a): this provider is NOT yet wired into ``_run_local`` — that
strangler cut is P3b. ``agent_runner.py`` and its helper methods are untouched
here, so there is zero production behavior change. P3a exists to prove the
contract can sit on the real mechanics with contract tests before the wiring
risk is taken on.

Boundary (from P1): the provider owns sandbox mechanics only — spawn, raw
stream bytes, process-group signals, exit classification. The CLI protocol
(stream-json handshake, ``tool_use`` parsing, usage) stays in ``agent_runner``
and will consume this provider's ``ExecHandle`` in P3b.

Environment policy + the ``sudo``/``openace-run-as`` ACL wrap are the caller's
concern in P3a (``exec`` spawns the command it is given); the full integration
lands in P3b.
"""

from __future__ import annotations

import os
import pwd
import queue
import signal
import subprocess
import threading
import uuid
from typing import Any

from app.modules.workspace.autonomous.sandbox.provider import require_capabilities
from app.modules.workspace.autonomous.sandbox.types import (
    ExecHandle,
    SandboxCapability,
    SandboxEvent,
    SandboxEventKind,
    SandboxHandle,
    SandboxSpec,
    SandboxStatus,
)
from app.modules.workspace.autonomous.task_isolation import sanitize_task_id

# The four isolation guarantees the Legacy POSIX backend actually supplies.
# Namespace / network-egress isolation is reserved for the OpenSandbox/K8s
# backend (#2023); requiring either makes Legacy refuse creation (fail-closed).
_LEGACY_CAPS = frozenset(
    {
        SandboxCapability.PRIVATE_HOME_TMP_XDG,
        SandboxCapability.FILESYSTEM_ACL,
        SandboxCapability.CPU_MEM_PIDS_TIME_QUOTA,
        SandboxCapability.CREDENTIAL_TOKEN_BINDING,
    }
)

# Root helper that grants ACL + drops to system_account via runuser. Mirrors
# agent_runner._OPENACE_RUN_AS so P3b wiring can swap in this provider without
# changing the launcher path.
_LAUNCHER = os.environ.get("OPENACE_RUN_AS", "/usr/local/bin/openace-run-as")

# Env keys the cross-user /usr/bin/env layer is allowed to forward to the agent
# (the rest are stripped so a private cross-user HOME cannot leak the service
# user's secrets). Mirrors agent_runner._wrap_agent_cmd.
_GUARD_ENV_KEYS = (
    "PATH",
    "OPENACE_REAL_GIT",
    "OPENACE_REAL_GH",
    "OPENACE_GIT_CACHE_ROOT",
    "OPENACE_PYTHON_COMMAND",
    "OPENACE_PROXY_URL",
    "OPENACE_PROXY_TOKEN",
    "OPENACE_MODEL",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "BAILIAN_CODING_PLAN_API_KEY",
    "GEMINI_API_KEY",
    "GEMINI_BASE_URL",
    "GIT_AUTHOR_NAME",
    "GIT_AUTHOR_EMAIL",
    "GIT_COMMITTER_NAME",
    "GIT_COMMITTER_EMAIL",
    "GH_CONFIG_DIR",
    "GIT_TERMINAL_PROMPT",
    "SKIP",
)


def _is_cross_user(system_account: str | None) -> bool:
    """True iff the agent must run as a different user than this process.

    Mirrors agent_runner._is_cross_user: a set ``system_account`` that differs
    from the current user. On uid lookup failure, assume cross-user (safe).
    """
    if not system_account:
        return False
    try:
        return pwd.getpwuid(os.getuid()).pw_name != system_account
    except KeyError:
        return True


def classify_isolated_exit_code(
    return_code: int | None,
    stderr: str = "",
    *,
    orchestrator_initiated: bool = False,
    resource_policy_configured: bool = False,
) -> tuple[str | None, str | None]:
    """Map an isolated-launcher child exit to a structured error code.

    Mirrors agent_runner._classify_isolated_exit_code (#2067). Python encodes
    signal deaths as negative return codes; SIGTERM(-15)/SIGINT(-2) are the
    orchestrator's own stop/timeout signals and are never a resource breach.
    """
    lowered = (stderr or "").lower()
    if "openace_cgroup_required" in lowered:
        return (
            "task_resource_policy_unavailable",
            "Task resource policy could not be enforced (cgroup unavailable)",
        )
    if return_code == 68 or "openace_repo_integrity_violation" in lowered:
        return ("repo_integrity_violation", None)
    if (
        not orchestrator_initiated
        and resource_policy_configured
        and return_code is not None
        and return_code < 0
        and -return_code in (signal.SIGKILL, signal.SIGXCPU)
    ):
        return (
            "task_resource_limit_exceeded",
            "Agent process killed by signal (possible resource-limit breach)",
        )
    return (None, None)


class LegacyPosixProvider:
    """Local POSIX SandboxProvider (spawn/stream/signal/destroy on real procs)."""

    def __init__(self) -> None:
        # sandbox_id -> live status. Unknown ids inspect as DESTROYED so an
        # orphan handle (one this provider never minted) is a no-op on destroy.
        self._status: dict[str, SandboxStatus] = {}
        # command_id -> the spawned Popen. Kept so pause/resume/stop/destroy can
        # reach the process group; cleared on destroy.
        self._procs: dict[str, subprocess.Popen[Any]] = {}
        # command_id -> owning sandbox_id, so destroy can reap every proc that
        # belongs to a sandbox without iterating opaque command ids.
        self._sandbox_of: dict[str, str] = {}

    def capabilities(self) -> frozenset[SandboxCapability]:
        return _LEGACY_CAPS

    def create(self, spec: SandboxSpec) -> SandboxHandle:
        require_capabilities(_LEGACY_CAPS, spec.required_capabilities)
        sandbox_id = uuid.uuid4().hex
        self._status[sandbox_id] = SandboxStatus.CREATED
        return SandboxHandle(
            sandbox_id=sandbox_id,
            generation=1,
            provider_name="legacy_posix",
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
        # The provider OWNS the sudo/openace-run-as ACL wrap (P1 boundary), so
        # exec applies build_launch_argv internally — a cross-user spec gets the
        # isolation wrap automatically, P3b cannot silently skip it by calling
        # exec with a raw command.
        argv = self.build_launch_argv(handle, command, env)
        # env=None must NOT inherit the orchestrator's environment (it carries
        # credentials). Spawn with an empty env instead; callers must pass a
        # scrubbed env (#2019 / _build_agent_env output) for the agent to work.
        spawn_env: dict[str, str] | None = env if env is not None else {}
        # cwd mirrors _wrap_agent_cmd's (cmd, cwd) return: same-user agents
        # (claude-code/qwen-code) infer project root from cwd (no --cwd flag),
        # so the spawn must chdir into project_path; cross-user launches leave
        # cwd=None because the run-as launcher chdir's as root before dropping
        # to system_account. Omitting cwd (the P3a default) would run the
        # same-user agent in the orchestrator's cwd — wrong project.
        cwd: str | None = (
            None if _is_cross_user(handle.spec.system_account) else handle.spec.project_path
        )
        # start_new_session=True puts the child in its own process group/session
        # (pgid == child pid), which is what later lets pause/resume/stop reach
        # the whole tree via os.killpg(os.getpgid(pid), sig). This invariant
        # mirrors agent_runner._run_local.
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            env=spawn_env,
            start_new_session=True,
        )
        command_id = uuid.uuid4().hex
        self._procs[command_id] = proc
        self._sandbox_of[command_id] = handle.sandbox_id
        return ExecHandle(sandbox_id=handle.sandbox_id, command_id=command_id)

    def get_process(self, exec_handle: ExecHandle) -> subprocess.Popen[Any]:
        """Return the raw ``Popen`` for an execution (#2022 P3b).

        The CLI stream-json protocol (``_read_stdout``/``_read_stderr``/
        ``_send_sdk_init``/``_send_message``) drives the stdin/stdout handshake
        directly and needs the raw process; the provider's normalized
        ``stream()`` events are too high-level to carry it. This is a
        Legacy-specific escape hatch — it is deliberately NOT on the
        ``SandboxProvider`` Protocol: ``RemoteMachineProvider`` (P4) has no
        local ``Popen`` and the remote agent speaks a different transport, so
        exposing a local process would not make sense cross-backend.
        """
        return self._procs[exec_handle.command_id]

    def build_launch_argv(
        self,
        handle: SandboxHandle,
        command: list[str],
        env: dict[str, str] | None,
    ) -> list[str]:
        """Build the cross-user launch argv for *command* (mirrors _wrap_agent_cmd).

        Same-user: the command verbatim. Cross-user: ``sudo -n -u root
        <launcher> --isolated --task-id <sanitized> <system_account>
        <project_path> /usr/bin/env K=V ... <command>`` — the launcher chdir's
        as root then drops to ``system_account`` via runuser, and only the
        allowlisted env keys are forwarded so a private cross-user HOME cannot
        leak the service user's secrets. ``exec`` applies this internally so
        the wrap is automatic (the P1 boundary puts ACL wrap on the provider,
        not the caller).
        """
        system_account = handle.spec.system_account
        if not _is_cross_user(system_account):
            return list(command)
        assert system_account is not None  # _is_cross_user guarantees non-empty
        guard_env = [f"{key}={env[key]}" for key in _GUARD_ENV_KEYS if env and env.get(key)]
        guarded_cmd = ["/usr/bin/env", *guard_env, *command] if guard_env else list(command)
        return [
            "sudo",
            "-n",
            "-u",
            "root",
            _LAUNCHER,
            "--isolated",
            "--task-id",
            sanitize_task_id(handle.spec.task_id),
            system_account,
            handle.spec.project_path,
            *guarded_cmd,
        ]

    def stream(self, exec_handle: ExecHandle):  # type: ignore[no-untyped-def]
        proc = self._procs[exec_handle.command_id]
        sandbox_id = exec_handle.sandbox_id
        command_id = exec_handle.command_id
        self._status[sandbox_id] = SandboxStatus.RUNNING
        yield SandboxEvent(kind=SandboxEventKind.PROCESS_STARTED, sandbox_id=sandbox_id)
        yield SandboxEvent(
            kind=SandboxEventKind.COMMAND_STARTED,
            sandbox_id=sandbox_id,
            command_id=command_id,
        )
        # Read stdout + stderr CONCURRENTLY. A sequential read (stdout to EOF,
        # then stderr) deadlocks once the child fills its 64KB stderr OS pipe
        # while the parent is still blocked reading stdout — Python's
        # subprocess docs warn about this, and a real agent's verbose stderr
        # (>64KB) would hang the stream. ``communicate()`` would avoid it but
        # is one-shot and cannot yield chunks, so two daemon threads pump each
        # fd into a queue the generator drains (mirrors agent_runner's
        # ``_stdout_thread`` / ``_stderr_thread``).
        chunk_queue: queue.Queue[Any] = queue.Queue()
        sentinel = object()

        def _pump(stream: Any, kind: SandboxEventKind) -> None:
            try:
                if stream is None:
                    return
                for raw in stream:
                    chunk_queue.put((kind, raw))
            finally:
                chunk_queue.put(sentinel)

        stdout_thread = threading.Thread(
            target=_pump, args=(proc.stdout, SandboxEventKind.STDOUT_CHUNK), daemon=True
        )
        stderr_thread = threading.Thread(
            target=_pump, args=(proc.stderr, SandboxEventKind.STDERR_CHUNK), daemon=True
        )
        stdout_thread.start()
        stderr_thread.start()
        sentinels_seen = 0
        while sentinels_seen < 2:
            item = chunk_queue.get()
            if item is sentinel:
                sentinels_seen += 1
                continue
            kind, raw = item
            yield SandboxEvent(
                kind=kind,
                sandbox_id=sandbox_id,
                command_id=command_id,
                data=raw.decode("utf-8", errors="replace"),
            )
        stdout_thread.join(timeout=5)
        stderr_thread.join(timeout=5)
        proc.wait()
        exit_code = proc.returncode
        yield SandboxEvent(
            kind=SandboxEventKind.COMMAND_COMPLETED,
            sandbox_id=sandbox_id,
            command_id=command_id,
            exit_code=exit_code,
        )
        yield SandboxEvent(
            kind=SandboxEventKind.PROCESS_EXITED,
            sandbox_id=sandbox_id,
            exit_code=exit_code,
        )

    def pause(self, exec_handle: ExecHandle) -> None:
        self._signal_process_group(exec_handle, signal.SIGSTOP)
        self._status[exec_handle.sandbox_id] = SandboxStatus.PAUSED

    def resume(self, exec_handle: ExecHandle) -> None:
        self._signal_process_group(exec_handle, signal.SIGCONT)
        self._status[exec_handle.sandbox_id] = SandboxStatus.RUNNING

    def stop(self, exec_handle: ExecHandle) -> None:
        # Mirror agent_runner.stop_session: if paused, SIGCONT first so SIGTERM
        # is deliverable, then SIGTERM, wait, escalate to SIGKILL.
        proc = self._procs.get(exec_handle.command_id)
        if proc is not None and proc.poll() is None:
            if self._status.get(exec_handle.sandbox_id) == SandboxStatus.PAUSED:
                self._signal_process_group(exec_handle, signal.SIGCONT)
            self._signal_process_group(exec_handle, signal.SIGTERM)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._signal_process_group(exec_handle, signal.SIGKILL)
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    pass
        self._status[exec_handle.sandbox_id] = SandboxStatus.STOPPED

    def collect_changes(self, handle: SandboxHandle) -> Any:
        # P3a placeholder. Legacy delegates to the git-workspace service
        # (#2041-2043) in P3b; non-local backends own collection.
        return None

    def upload_workspace(self, handle: SandboxHandle, snapshot: Any | None) -> None:
        # Legacy no-op: the local worktree is already in place.
        return None

    def collect_execution_evidence(self, handle: SandboxHandle) -> list[Any]:
        # P3a placeholder; filled from the real command stream in P3b.
        return []

    def destroy(self, handle: SandboxHandle) -> None:
        # Reap any live process belonging to this sandbox, then mark destroyed.
        # Idempotent: an unknown / already-destroyed sandbox is a no-op.
        self._reap_sandbox(handle.sandbox_id)
        self._status[handle.sandbox_id] = SandboxStatus.DESTROYED

    def inspect(self, handle: SandboxHandle) -> SandboxStatus:
        return self._status.get(handle.sandbox_id, SandboxStatus.DESTROYED)

    # ── helpers ─────────────────────────────────────────────────────

    def _signal_process_group(self, exec_handle: ExecHandle, sig: int) -> None:
        proc = self._procs.get(exec_handle.command_id)
        if proc is None or proc.poll() is not None or proc.pid is None:
            return
        try:
            os.killpg(os.getpgid(proc.pid), sig)
        except (ProcessLookupError, OSError):
            pass

    def _reap_sandbox(self, sandbox_id: str) -> None:
        # SIGTERM→SIGKILL any live proc whose command belongs to this sandbox.
        # _sandbox_of tracks command_id -> sandbox_id at exec time.
        for command_id, proc in list(self._procs.items()):
            if self._sandbox_of.get(command_id) != sandbox_id:
                continue
            if proc.poll() is None and proc.pid is not None:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                    proc.wait(timeout=5)
                except (ProcessLookupError, OSError, subprocess.TimeoutExpired):
                    try:
                        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    except (ProcessLookupError, OSError):
                        pass
