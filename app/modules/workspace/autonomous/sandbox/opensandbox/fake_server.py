"""In-memory double for the OpenSandbox APIs (Issue #2023).

Lets the provider suite run in the SQLite ``test(3.x)`` CI lane with no cluster,
following the precedent of ``sandbox/fake.py`` (a test double shipped inside the
production package).

The governing rule here is that **the fake models upstream's real behaviour, not
convenient behaviour**. Three places where those differ, and where a convenient
fake would have hidden a bug rather than caught it:

* ``POST /sandboxes`` returns ``status.state: "Running"`` — provisioning is
  synchronous — and ``DELETE`` goes ``Stopping`` then ``Terminated``. A fake that
  reported ``Pending``/``Terminated`` promptly would make the provider's status
  overlay look unnecessary while production diverged.
* A non-zero exit emits an SSE ``error`` event with a numeric ``evalue`` and
  **no** ``execution_complete``. A fake that always emitted
  ``execution_complete`` would let the provider map every failing test run to a
  sandbox error unnoticed.
* A pod-level OOM kills execd along with the container, so execd calls raise and
  the sandbox reads back ``Failed`` — there is no tidy exit 137 to observe.
"""

from __future__ import annotations

import itertools
import json
from collections.abc import Iterator

from app.modules.workspace.autonomous.sandbox.opensandbox.client import (
    INGRESS_ROUTE_HEADER,
    SECURE_ACCESS_HEADER,
    OpenSandboxApiError,
)

_PAGE_SIZE = 20


class FakeOpenSandboxApi:
    """An :class:`OpenSandboxApi` that lives entirely in memory."""

    def __init__(
        self,
        *,
        fail_create: bool = False,
        scripted_exit_code: int = 0,
        scripted_timeout: bool = False,
        pod_oom: bool = False,
        # A normal kernel, matching the Kata tiers the fixtures now use: gVisor
        # cannot run these workloads at all (upstream rejects every networkPolicy
        # under it), so a gVisor default would make the probe refuse everywhere.
        # Tests that exercise the mismatch pass a gVisor string explicitly.
        runtime_kernel: str = "Linux version 5.15.0 #1 SMP",
        stderr_text: str = "",
        egress_enforcement_mode: str = "dns+nft",
        egress_default_action: str = "deny",
        require_endpoint_token: bool = True,
    ) -> None:
        self.sandboxes: dict[str, dict] = {}
        self.created_bodies: list[dict] = []
        self.command_bodies: list[dict] = []
        self.uploaded: dict[str, dict[str, bytes]] = {}
        self.deleted: set[str] = set()
        self.pty_sessions: dict[str, dict] = {}
        self.renewed: list[tuple[str, str]] = []
        self.list_filters: list[dict] = []
        # When set, DELETE is accepted but the sandbox never even begins to
        # stop — it stays Running. That is the genuinely unconfirmed case: an
        # observed `Stopping` means the delete took, so only an unchanged
        # `Running` proves it did not.
        self.stall_delete = False
        # When set, DELETE takes but the sandbox lingers in Stopping — the
        # normal production shape, given the grace period.
        self.linger_in_stopping = False

        self._ids = itertools.count(1)
        self._command_ids = itertools.count(1)
        self._fail_create = fail_create
        self._scripted_exit_code = scripted_exit_code
        self._scripted_timeout = scripted_timeout
        self._pod_oom = pod_oom
        self._runtime_kernel = runtime_kernel
        self._stderr_text = stderr_text
        # What the in-sandbox manifest producer will "produce": a {path: bytes}
        # map, or None to model a producer that left no output.
        self._manifest: dict[str, bytes] | None = {}
        self._failed_state: tuple[str, str] | None = None
        self._egress_enforcement_mode = egress_enforcement_mode
        self._egress_default_action = egress_default_action
        self._require_endpoint_token = require_endpoint_token
        self._commands: dict[str, dict] = {}

    # ── Lifecycle ─────────────────────────────────────────────────────

    def create_sandbox(self, body: dict) -> dict:
        if self._fail_create:
            raise OpenSandboxApiError("create refused", status_code=400, code="INVALID_REQUEST")
        self.created_bodies.append(body)
        sandbox_id = f"sb-{next(self._ids)}"
        # Upstream returns Running: "provisioning completed synchronously".
        record = {
            "id": sandbox_id,
            "status": {"state": "Running", "reason": "", "message": ""},
            "metadata": dict(body.get("metadata") or {}),
            "entrypoint": body.get("entrypoint") or [],
            "createdAt": "2026-08-28T00:00:00Z",
        }
        self.sandboxes[sandbox_id] = record
        self.uploaded[sandbox_id] = {}
        return record

    def get_sandbox(self, sandbox_id: str) -> dict | None:
        record = self.sandboxes.get(sandbox_id)
        if record is None:
            return None
        if self._failed_state is not None:
            reason, message = self._failed_state
            record["status"] = {"state": "Failed", "reason": reason, "message": message}
            return record
        if self._pod_oom:
            # The container (and execd with it) was OOM-killed.
            record["status"] = {
                "state": "Failed",
                "reason": "OOMKilled",
                "message": "container exceeded its memory limit",
            }
            return record
        if sandbox_id in self.deleted and not self.stall_delete:
            state = "Stopping" if self.linger_in_stopping else "Terminated"
            record["status"] = {"state": state, "reason": "", "message": ""}
        return record

    def list_sandboxes(self, metadata: dict | None = None) -> list[dict]:
        self.list_filters.append(dict(metadata or {}))
        rows = [r for sid, r in self.sandboxes.items() if sid not in self.deleted]
        if metadata:
            rows = [
                r
                for r in rows
                if all((r.get("metadata") or {}).get(k) == v for k, v in metadata.items())
            ]
        return rows

    def delete_sandbox(self, sandbox_id: str) -> None:
        # 404 is success: destroy() must be idempotent.
        record = self.sandboxes.get(sandbox_id)
        if self.stall_delete:
            # Accepted, but nothing happens: the sandbox stays Running.
            return
        self.deleted.add(sandbox_id)
        if record is not None:
            # Upstream goes Stopping, then Terminated — not straight to gone.
            # The transition happens over successive READS, which is what makes
            # the provider's poll-to-terminal real rather than decorative.
            record["status"] = {"state": "Stopping", "reason": "", "message": ""}

    def pause_sandbox(self, sandbox_id: str) -> None:
        self._require(sandbox_id)["status"] = {"state": "Paused", "reason": "", "message": ""}

    def resume_sandbox(self, sandbox_id: str) -> None:
        self._require(sandbox_id)["status"] = {"state": "Running", "reason": "", "message": ""}

    def renew_expiration(self, sandbox_id: str, expires_at: str) -> None:
        self.renewed.append((sandbox_id, expires_at))

    def execd_base_url(self, sandbox_id: str) -> str:
        self._require(sandbox_id)
        return f"http://execd.invalid/sandboxes/{sandbox_id}/port/44772"

    def execd_headers(self, sandbox_id: str) -> dict[str, str]:
        # UPSTREAM names, and the header-mode SHAPE the shipped ConfigMaps
        # produce: the routing header always, the secure-access token when the
        # server mints one. This previously returned an invented
        # "X-Sandbox-Access-Token" that happened to be on the client's allowlist,
        # so the fake and the client agreed with each other about a header the
        # real server never sends — and it later omitted the routing header
        # entirely, hiding that the filter dropped it.
        headers = {INGRESS_ROUTE_HEADER: f"{sandbox_id}-44772"}
        if self._require_endpoint_token:
            headers[SECURE_ACCESS_HEADER] = f"tok-{sandbox_id}"
        return headers

    def peer_request(self, sandbox_id: str, *, token: str | None) -> dict:
        """Simulate a *different* sandbox reaching this one's endpoint.

        With ``secureAccess`` enabled upstream provisions an access credential
        and the endpoint rejects unauthenticated callers; with it off (the
        upstream default) any peer that knows the URL gets in.
        """
        if self._require_endpoint_token and token != f"tok-{sandbox_id}":
            raise OpenSandboxApiError("peer access denied", status_code=401, code="UNAUTHORIZED")
        return {"ok": True}

    # ── Execd ─────────────────────────────────────────────────────────

    def upload_file(self, sandbox_id: str, path: str, data: bytes, mode: int) -> None:
        self._require_execd(sandbox_id)
        self.uploaded.setdefault(sandbox_id, {})[path] = data

    def download_file(self, sandbox_id: str, path: str) -> bytes:
        self._require_execd(sandbox_id)
        stored = self.uploaded.get(sandbox_id, {})
        if path not in stored:
            raise OpenSandboxApiError(f"no such file {path}", status_code=404, code="NOT_FOUND")
        return stored[path]

    def run_command(self, sandbox_id: str, body: dict) -> Iterator[dict]:
        self._require_execd(sandbox_id)
        self.command_bodies.append(body)
        command = str(body.get("command") or "")
        if "openace-manifest.py" in command:
            # The producer runs inside the sandbox and writes to /tmp.
            if self._manifest is not None:
                payload = scripted_manifest(self._manifest)
                self.uploaded.setdefault(sandbox_id, {})["/tmp/openace-manifest.json"] = payload
                for path, data in self._manifest.items():
                    self.uploaded[sandbox_id][f"/workspace/{path}"] = data
        command_id = f"cmd-{next(self._command_ids)}"
        exit_code = self._scripted_exit_code
        self._commands[command_id] = {
            "id": command_id,
            "running": self._scripted_timeout,
            "exit_code": None if self._scripted_timeout else exit_code,
            "error": "",
            "started_at": "2026-08-28T00:00:00Z",
            "finished_at": None if self._scripted_timeout else "2026-08-28T00:00:01Z",
        }
        return iter(self._script_events(command_id, exit_code))

    def _script_events(self, command_id: str, exit_code: int) -> list[dict]:
        events: list[dict] = [{"type": "init", "text": command_id}]
        events.append({"type": "stdout", "text": "working\n"})
        if self._stderr_text:
            events.append({"type": "stderr", "text": self._stderr_text})
        if self._scripted_timeout:
            # No terminal event at all: the stream just ends.
            return events
        if exit_code != 0:
            # Upstream emits `error` with a NUMERIC evalue and never
            # execution_complete for a non-zero exit.
            events.append(
                {
                    "type": "error",
                    "error": {
                        "ename": "CommandExecError",
                        "evalue": str(exit_code),
                        "traceback": [],
                    },
                }
            )
            return events
        events.append({"type": "execution_complete", "execution_time": 12})
        return events

    def command_status(self, sandbox_id: str, command_id: str) -> dict | None:
        self._require_execd(sandbox_id)
        return self._commands.get(command_id)

    def interrupt_command(self, sandbox_id: str, command_id: str) -> None:
        self._require_execd(sandbox_id)
        record = self._commands.get(command_id)
        if record is not None:
            record["running"] = False

    def egress_policy(self, sandbox_id: str) -> dict:
        self._require_execd(sandbox_id)
        return {
            "defaultAction": self._egress_default_action,
            "enforcementMode": self._egress_enforcement_mode,
            "egress": [],
        }

    # ── PTY ───────────────────────────────────────────────────────────

    def create_pty_session(self, sandbox_id: str, *, cwd: str = "", command: str = "") -> str:
        self._require_execd(sandbox_id)
        pty_id = f"pty-{sandbox_id}"
        self.pty_sessions[pty_id] = {
            "session_id": pty_id,
            "running": True,
            "output_offset": 0,
            "cwd": cwd,
            "command": command,
        }
        return pty_id

    def pty_status(self, sandbox_id: str, pty_session_id: str) -> dict:
        return self.pty_sessions.get(
            pty_session_id, {"session_id": pty_session_id, "running": False, "output_offset": 0}
        )

    def delete_pty_session(self, sandbox_id: str, pty_session_id: str) -> None:
        self.pty_sessions.pop(pty_session_id, None)

    def pty_ws_url(self, sandbox_id: str, pty_session_id: str, *, since: int = 0) -> str:
        return f"ws://execd.invalid/pty/{pty_session_id}/ws?pty=0"

    # ── probes ────────────────────────────────────────────────────────

    def set_pod_oom(self, value: bool) -> None:
        """Simulate the container being OOM-killed after it started running."""
        self._pod_oom = value

    def set_failed(self, reason: str, message: str = "") -> None:
        """Simulate the sandbox reaching Failed for a NON-OOM reason."""
        self._failed_state = (reason, message)

    def set_manifest(self, files: dict[str, bytes] | None) -> None:
        """Script what the in-sandbox manifest producer will emit.

        ``None`` models a producer that ran but left no output, which the
        provider must treat as an error rather than as "no changes".
        """
        self._manifest = files

    def proc_version(self, sandbox_id: str) -> str:
        """What ``cat /proc/version`` returns — the runtime-class probe's input."""
        return self._runtime_kernel

    # ── helpers ───────────────────────────────────────────────────────

    def _require(self, sandbox_id: str) -> dict:
        record = self.sandboxes.get(sandbox_id)
        if record is None or sandbox_id in self.deleted:
            raise OpenSandboxApiError("no such sandbox", status_code=404, code="NOT_FOUND")
        return record

    def _require_execd(self, sandbox_id: str) -> None:
        """Execd dies with the pod, which is what any Failed state looks like.

        Modelling execd as still reachable on a Failed sandbox would let the
        provider read a tidy exit code that production never provides.
        """
        if self._pod_oom or self._failed_state is not None:
            raise OpenSandboxApiError("connection refused: execd is gone")
        self._require(sandbox_id)


def scripted_manifest(files: dict[str, bytes], deleted: list[str] | None = None) -> bytes:
    """Build a supervisor manifest for tests that exercise ChangeSet collection."""
    import hashlib

    return json.dumps(
        {
            "files": [
                {
                    "path": path,
                    "mode": 0o644,
                    "size": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
                for path, data in files.items()
            ],
            "deleted": list(deleted or []),
        }
    ).encode("utf-8")


_ = _PAGE_SIZE  # documented upstream default; the fake returns one page
