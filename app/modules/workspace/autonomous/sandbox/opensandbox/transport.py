"""execd PTY pipe-mode transport (Issue #2023).

Implements :class:`AgentTransport` over execd's PTY WebSocket in **pipe mode**
(``?pty=0``), which is what lets the coding-agent CLI run inside the sandbox
with its ``--input-format stream-json`` interactive stdin.

Wire protocol (``components/execd/PTY.md``, ``pkg/web/controller/pty_ws.go``):

===========  ====================================================
``0x00``     holder → server: stdin bytes
``0x01``     server → holder: stdout bytes
``0x02``     server → holder: stderr bytes (pipe mode only)
``0x03``     server → holder: replay, ``[8-byte BE offset][bytes]``
JSON text    ``resize`` / ``signal`` / ``ping`` out, ``exit`` in
===========  ====================================================

Why there is no reconnect
-------------------------
Three upstream facts make reconnect actively harmful rather than merely
unimplemented:

1. **Attaching starts a shell.** ``pty_ws.go:139-152`` calls ``StartPipe()``
   whenever ``!session.IsRunning()``, and ``IsRunning()`` is ``pid != 0``,
   cleared on process exit. Re-attaching after the CLI finishes therefore
   launches a *second* agent process — a duplicated run, with no stdin
   handshake and nobody reading its output.
2. **Replay cannot be de-interleaved.** ``0x03`` frames come from a single
   ``replayBuffer`` shared by both streams; ``PTY.md`` states outright that pipe
   mode replay "is a combined stream without separate stdout/stderr channels".
   Feeding those bytes into the stream-json parser corrupts it, which is worse
   than losing them.
3. **A missed exit frame is unrecoverable.** ``PTYSessionStatusResponse`` is
   ``{session_id, running, output_offset}`` — ``session.ExitCode()`` exists in Go
   but is not exposed over HTTP.

So a dropped socket is terminal: the transport stops, records
:data:`PTY_STREAM_LOST`, and reports no exit code, which the provider maps to a
structured ``CRASH``. ``wait()`` always honours its deadline so the runner's
completion loop cannot block forever.

Threading
---------
The reader runs in a ``threading.Thread``. Both ``server.py`` and
``app/scheduler_worker.py`` call ``gevent.monkey.patch_all()``, so that is a
greenlet and the blocking socket reads stay cooperative — the same basis on
which ``vscode_ws_bridge.py`` and ``terminal_ws_bridge.py`` already drive
``websockets.sync.client`` in this process.
"""

from __future__ import annotations

import json
import queue
import threading
import time
from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING, Any

from app.modules.workspace.autonomous.sandbox.provider import SandboxError

if TYPE_CHECKING:  # pragma: no cover - annotations only
    from app.modules.workspace.autonomous.sandbox.opensandbox.client import OpenSandboxApi

# Frame kinds.
_STDIN = 0x00
_STDOUT = 0x01
_STDERR = 0x02
_REPLAY = 0x03

_REPLAY_HEADER_BYTES = 8

#: Reason code for a PTY stream that ended without a usable exit frame. Distinct
#: from a normal non-zero exit so the evidence layer reports CRASH rather than a
#: fabricated completion.
PTY_STREAM_LOST = "pty_stream_lost"

_SENTINEL = object()


class _LineStream:
    """A byte stream assembled from frames and read a line at a time.

    ``readline`` blocks until a newline arrives or the stream closes, matching
    ``Popen.stdout.readline`` semantics (``b""`` at EOF) so the runner's reader
    loops need no changes.
    """

    def __init__(self) -> None:
        self._chunks: queue.Queue[Any] = queue.Queue()
        self._buffer = b""
        self._closed = False

    def feed(self, data: bytes) -> None:
        self._chunks.put(data)

    def close(self) -> None:
        self._chunks.put(_SENTINEL)

    def read_available(self) -> bytes:
        """Return a buffered line without waiting, or ``b""`` if none is ready.

        Distinct from :meth:`readline`, which blocks until data or close: this
        is for draining, where a blocking read would spin.
        """
        newline = self._buffer.find(b"\n")
        if newline < 0:
            try:
                chunk = self._chunks.get_nowait()
            except queue.Empty:
                return b""
            if chunk is _SENTINEL:
                # The close marker, not data — concatenating it would raise.
                self._closed = True
            else:
                self._buffer += chunk
            newline = self._buffer.find(b"\n")
        if newline < 0:
            if self._closed and self._buffer:
                line, self._buffer = self._buffer, b""
                return line
            return b""
        line, self._buffer = self._buffer[: newline + 1], self._buffer[newline + 1 :]
        return line

    def readline(self, timeout: float | None = None) -> bytes:
        """Block until a full line, or until the stream closes.

        ``b""`` means EOF and nothing else. Returning it on a mere timeout would
        be indistinguishable from a finished stream, and the runner's readers
        treat ``b""`` as "break out of the loop" — so a multi-second gap between
        stream-json messages (model latency, a long tool call) would
        permanently end the reader mid-run.

        ``timeout`` therefore bounds each internal wait, not the call: the loop
        keeps waiting until data arrives or the producer closes the stream.
        """
        while True:
            newline = self._buffer.find(b"\n")
            if newline >= 0:
                line, self._buffer = self._buffer[: newline + 1], self._buffer[newline + 1 :]
                return line
            if self._closed:
                # Flush any trailing partial line, then report a real EOF.
                line, self._buffer = self._buffer, b""
                return line
            try:
                chunk = self._chunks.get(timeout=timeout)
            except queue.Empty:
                continue  # quiet period, NOT end of stream
            if chunk is _SENTINEL:
                self._closed = True
                continue
            self._buffer += chunk


class PtyWebSocketTransport:
    """:class:`AgentTransport` over an execd PTY session in pipe mode."""

    def __init__(
        self,
        api: OpenSandboxApi,
        *,
        sandbox_id: str,
        cwd: str,
        command: str,
        connect_factory: Callable[[str, dict[str, str]], Any] | None = None,
        read_timeout: float = 1.0,
    ) -> None:
        self._api = api
        self._sandbox_id = sandbox_id
        self._cwd = cwd
        # The env-bearing `bash -c` string from policy.build_pty_command:
        # CreatePTYSessionRequest carries no envs, and pty_session.go starts the
        # shell with cmd.Env = os.Environ() and no merge, so this is the only
        # channel the agent's environment can travel through.
        self._command = command
        self._connect_factory = connect_factory or _default_connect
        self._read_timeout = read_timeout

        self._pty_session_id: str | None = None
        self._conn: Any | None = None
        self._reader: threading.Thread | None = None
        self._stdout = _LineStream()
        self._stderr = _LineStream()
        self._exit_code: int | None = None
        self._finished = threading.Event()
        self._protocol_break: str = ""
        # Distinguishes a first-attach snapshot from a replay after live output.
        self._seen_live_frame = False
        self._shutdown_done = False
        self._started = False

    # ── lifecycle ─────────────────────────────────────────────────────

    def start(self) -> None:
        """Create the PTY session and attach the pipe-mode socket."""
        self._pty_session_id = self._api.create_pty_session(
            self._sandbox_id, cwd=self._cwd, command=self._command
        )
        url = self._api.pty_ws_url(self._sandbox_id, self._pty_session_id)
        header_getter = getattr(self._api, "execd_headers", None)
        headers: dict[str, str] = header_getter(self._sandbox_id) if header_getter else {}
        self._conn = self._connect_factory(url, headers)
        self._started = True
        self._reader = threading.Thread(
            target=self._read_loop, name=f"pty-{self._pty_session_id}", daemon=True
        )
        self._reader.start()

    @property
    def protocol_break_reason(self) -> str:
        """:data:`PTY_STREAM_LOST` when the stream ended unusably, else ``""``."""
        return self._protocol_break

    @property
    def pid(self) -> int | None:
        # No local process exists. Callers must not pass this to os.getpgid.
        return None

    @property
    def returncode(self) -> int | None:
        """Last observed exit status without side effects (mirrors Popen.returncode)."""
        return self._exit_code

    # ── IO ────────────────────────────────────────────────────────────

    def write_stdin(self, data: bytes) -> None:
        self._require_started()
        conn = self._conn
        if conn is None or self._finished.is_set():
            return
        try:
            conn.send(bytes([_STDIN]) + data)
        except Exception as exc:  # noqa: BLE001 - any socket failure is terminal
            self._break_stream(f"stdin write failed: {exc}")

    def close_stdin(self) -> None:
        """Signal end-of-input to the shell.

        The wire has no half-close — closing the socket would tear down the
        session — so this sends SIGHUP instead. The runner closes stdin
        deliberately to make the CLI terminate; leaving this inert would let a
        CLI reading stream-json from a never-closing pipe block until the
        wall-clock deadline and be misreported as a timeout.
        """
        self._require_started()
        conn = self._conn
        if conn is None or self._finished.is_set():
            return
        try:
            conn.send(json.dumps({"type": "signal", "signal": "SIGHUP"}))
        except Exception:  # noqa: BLE001 - best effort; shutdown still follows
            pass

    def readline_stdout(self) -> bytes:
        self._require_started()
        return self._stdout.readline(timeout=self._read_timeout)

    def readline_stderr(self) -> bytes:
        self._require_started()
        return self._stderr.readline(timeout=self._read_timeout)

    # ── completion ────────────────────────────────────────────────────

    def poll(self) -> int | None:
        return self._exit_code

    def wait(self, timeout: float | None = None) -> int | None:
        """Wait for the exit frame, always honouring *timeout*.

        Returns ``None`` on timeout **and** on a lost stream — there is no HTTP
        endpoint carrying an exit code, so a missed frame cannot be recovered
        and must not be invented.
        """
        self._finished.wait(timeout=timeout)
        return self._exit_code

    def shutdown(self, grace: float = 5.0) -> None:
        """SIGINT the shell, then tear the PTY session down.

        Upstream delivers signals to the process group, so this reaches the
        agent's children rather than only the shell.
        """
        if self._shutdown_done:
            return
        self._shutdown_done = True
        conn = self._conn
        if conn is not None and not self._finished.is_set():
            try:
                conn.send(json.dumps({"type": "signal", "signal": "SIGINT"}))
            except Exception:  # noqa: BLE001 - best effort
                pass
            self._finished.wait(timeout=grace)
        if self._pty_session_id is not None:
            try:
                self._api.delete_pty_session(self._sandbox_id, self._pty_session_id)
            except Exception:  # noqa: BLE001 - best effort; destroy still follows
                pass
        self._close_conn()

    def iter_events(self, deadline_seconds: float | None = None) -> Iterator[dict]:
        """Yield ``{"type": ...}`` events until the shell exits.

        Same shape as the SSE events the ``/command`` branch produces, so
        ``OpenSandboxProvider.stream`` has one mapping for both branches. The
        transport itself is deliberately NOT iterable: an object that is
        sometimes a list of events and sometimes a live connection invites
        exactly the confusion that made ``stream()`` raise on every agent turn.
        """
        self._require_started()
        deadline = None if deadline_seconds is None else time.monotonic() + deadline_seconds
        timed_out = False
        while not self._finished.is_set():
            if deadline is not None and time.monotonic() > deadline:
                # Without this a CLI that hangs writing nothing would block any
                # Protocol-level consumer of stream() forever.
                timed_out = True
                break
            # Both streams each pass, so stderr is interleaved with stdout as it
            # arrives rather than arriving in a burst after exit — which also
            # keeps stderr_digest populated for a run that never terminates.
            produced = False
            for kind, stream in (("stdout", self._stdout), ("stderr", self._stderr)):
                line = stream.read_available()
                if line:
                    produced = True
                    yield {"type": kind, "text": line.decode("utf-8", errors="replace")}
            if not produced:
                self._finished.wait(timeout=self._read_timeout)
        # Drain what is still buffered before reporting the terminal event.
        for kind, stream in (("stdout", self._stdout), ("stderr", self._stderr)):
            while True:
                line = stream.read_available()
                if not line:
                    break
                yield {"type": kind, "text": line.decode("utf-8", errors="replace")}
        if timed_out:
            yield {"type": "status", "text": "timeout"}
            return
        if self._protocol_break:
            yield {"type": "error", "error": {"ename": "PtyStreamLost", "evalue": PTY_STREAM_LOST}}
        elif self._exit_code == 0:
            yield {"type": "execution_complete", "execution_time": 0}
        else:
            yield {
                "type": "error",
                "error": {"ename": "CommandExecError", "evalue": str(self._exit_code)},
            }

    # ── reader ────────────────────────────────────────────────────────

    def _read_loop(self) -> None:
        conn = self._conn
        assert conn is not None
        try:
            while True:
                message = conn.recv()
                if isinstance(message, (bytes, bytearray)):
                    if not self._handle_binary(bytes(message)):
                        continue
                else:
                    if self._handle_text(str(message)):
                        return
        except Exception as exc:  # noqa: BLE001 - any read failure ends the stream
            if not self._finished.is_set():
                self._break_stream(f"socket read failed: {exc}")
        finally:
            self._stdout.close()
            self._stderr.close()

    def _handle_binary(self, frame: bytes) -> bool:
        if not frame:
            return False
        kind, payload = frame[0], frame[1:]
        if kind == _STDOUT:
            self._seen_live_frame = True
            self._stdout.feed(payload)
        elif kind == _STDERR:
            self._seen_live_frame = True
            self._stderr.feed(payload)
        elif kind == _REPLAY:
            # Channel-merged and unsplittable, so the bytes are always dropped:
            # interleaving them into stdout would corrupt the stream-json parser,
            # which is worse than losing them.
            #
            # But a replay frame on the FIRST attach is normal, not a break.
            # pty_ws.go starts the shell (step 4) before attaching (steps 5-6),
            # so anything the shell writes in that window — a bash notice, a CLI
            # banner — comes back as a snapshot on our very first connection.
            # Only a replay arriving after live frames indicates we missed
            # output we should have seen.
            if self._seen_live_frame:
                self._protocol_break = PTY_STREAM_LOST
        return False

    def _handle_text(self, message: str) -> bool:
        try:
            event = json.loads(message)
        except ValueError:
            return False
        if not isinstance(event, dict) or event.get("type") != "exit":
            return False
        raw = event.get("exit_code")
        self._exit_code = int(raw) if isinstance(raw, int) else None
        if self._exit_code is None:
            self._protocol_break = PTY_STREAM_LOST
        self._finished.set()
        return True

    def _break_stream(self, detail: str) -> None:
        self._protocol_break = PTY_STREAM_LOST
        self._exit_code = None
        self._finished.set()
        self._stdout.close()
        self._stderr.close()

    def _close_conn(self) -> None:
        conn = self._conn
        if conn is None:
            return
        try:
            conn.close()
        except Exception:  # noqa: BLE001 - best effort
            pass

    def _require_started(self) -> None:
        if not self._started:
            raise SandboxError("PTY transport used before start()")


def _default_connect(url: str, headers: dict[str, str]) -> Any:
    """Open a real WebSocket. Imported lazily so tests never need the dependency."""
    from websockets.sync.client import connect

    return connect(url, additional_headers=headers or None)
