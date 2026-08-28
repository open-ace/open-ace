"""execd PTY pipe-mode transport (#2023).

Pipe mode (`?pty=0`) is a bidirectional binary protocol: the holder sends stdin
as `0x00` + bytes and receives `0x01` (stdout) / `0x02` (stderr), ending with a
JSON `exit` frame carrying `exit_code`.

Reconnect is deliberately NOT implemented, and three upstream facts are why:

* attaching starts a shell — `pty_ws.go:139-152` runs `StartPipe()` whenever
  `!session.IsRunning()`, and `IsRunning()` is `pid != 0`, cleared on exit — so
  re-attaching to a finished session launches a *second* agent process;
* replay arrives as a third frame kind, `0x03`, from a single buffer shared by
  both streams, so it cannot be re-split — feeding it to the stream-json parser
  would corrupt the stream;
* `GET /pty/{id}` returns `{session_id, running, output_offset}` with no exit
  code, so an exit frame missed on the socket is unrecoverable.

A dropped socket is therefore terminal, and resolves to CRASH rather than a hang.
"""

from __future__ import annotations

import json
import struct
import threading
import time

import pytest

from app.modules.workspace.autonomous.sandbox.opensandbox.transport import (
    PTY_STREAM_LOST,
    PtyWebSocketTransport,
)
from app.modules.workspace.autonomous.sandbox.provider import SandboxError

pytestmark = [pytest.mark.regression, pytest.mark.issue(2023)]


class _FakeConnection:
    """Stands in for `websockets.sync.client.connect`'s return value.

    ``hold=True`` keeps the session *live* after the scripted frames run out,
    instead of ending it. Tests that exercise stdin or shutdown need that: the
    transport deliberately refuses to write to a session that has already sent
    its exit frame, so a fake that exits instantly would make those paths
    unreachable.
    """

    def __init__(self, incoming=(), *, drop_after=None, hold=False):
        self.sent: list[bytes | str] = []
        self._incoming = list(incoming)
        self._drop_after = drop_after
        self._hold = hold
        self._received = 0
        self._released = threading.Event()
        self.closed = False

    def deliver(self, frame):
        """Push a frame to a held connection, releasing the blocked reader."""
        self._incoming.append(frame)
        self._released.set()

    def send(self, data):
        self.sent.append(data)
        if self._hold and isinstance(data, str) and "SIGINT" in data:
            # A signal ends the shell; release the reader with the exit frame.
            self._incoming.append(_exit(130))
            self._released.set()

    def recv(self, timeout=None):
        if self._drop_after is not None and self._received >= self._drop_after:
            raise ConnectionError("socket dropped")
        while not self._incoming:
            if not self._hold:
                raise ConnectionError("socket closed")
            if not self._released.wait(timeout=3):
                raise ConnectionError("socket closed")
            self._released.clear()
        self._received += 1
        return self._incoming.pop(0)

    def close(self):
        self.closed = True
        self._released.set()


class _FakeApi:
    def __init__(self, running_after_drop=False):
        self.created: list[dict] = []
        self.deleted: list[str] = []
        self.signals: list[str] = []
        self._running_after_drop = running_after_drop

    def create_pty_session(self, sandbox_id, *, cwd="", command=""):
        self.created.append({"sandbox_id": sandbox_id, "cwd": cwd, "command": command})
        return "pty-1"

    def pty_status(self, sandbox_id, pty_session_id):
        return {
            "session_id": pty_session_id,
            "running": self._running_after_drop,
            "output_offset": 128,
        }

    def delete_pty_session(self, sandbox_id, pty_session_id):
        self.deleted.append(pty_session_id)

    def pty_ws_url(self, sandbox_id, pty_session_id, *, since=0):
        return f"ws://execd/pty/{pty_session_id}/ws?pty=0"


def _out(payload: bytes) -> bytes:
    return b"\x01" + payload


def _err(payload: bytes) -> bytes:
    return b"\x02" + payload


def _replay(offset: int, payload: bytes) -> bytes:
    return b"\x03" + struct.pack(">q", offset) + payload


def _exit(code: int) -> str:
    return json.dumps({"type": "exit", "exit_code": code})


def _transport(incoming=(), *, api=None, drop_after=None, connection=None):
    api = api or _FakeApi()
    conn = connection or _FakeConnection(incoming, drop_after=drop_after)
    transport = PtyWebSocketTransport(
        api,
        sandbox_id="sb-1",
        cwd="/workspace",
        command="exec claude",
        connect_factory=lambda url, headers: conn,
    )
    transport.start()
    return transport, api, conn


# ── framing ───────────────────────────────────────────────────────────


def test_stdin_is_framed_with_0x00_prefix():
    transport, _, conn = _transport(connection=_FakeConnection(hold=True))
    transport.write_stdin(b'{"type":"user"}\n')
    assert conn.sent[0] == b'\x00{"type":"user"}\n'


def test_stdout_and_stderr_frames_demultiplex_into_separate_streams():
    transport, _, _ = _transport([_out(b"out\n"), _err(b"err\n"), _exit(0)])
    assert transport.readline_stdout() == b"out\n"
    assert transport.readline_stderr() == b"err\n"


def test_partial_frames_accumulate_into_whole_lines():
    transport, _, _ = _transport([_out(b"he"), _out(b"llo\n"), _exit(0)])
    assert transport.readline_stdout() == b"hello\n"


def test_exit_frame_resolves_poll_and_wait_with_its_exit_code():
    transport, _, _ = _transport([_out(b"done\n"), _exit(3)])
    assert transport.wait(timeout=2) == 3
    assert transport.poll() == 3


def test_readline_returns_empty_bytes_after_exit():
    transport, _, _ = _transport([_exit(0)])
    transport.wait(timeout=2)
    assert transport.readline_stdout() == b""


def test_readline_blocks_across_a_quiet_period_and_returns_the_next_line():
    # b"" must mean EOF and nothing else: the runner breaks out of its reader
    # loop on it, so returning it during a model-latency gap would permanently
    # end the reader mid-run.
    conn = _FakeConnection(hold=True)
    transport, _, _ = _transport(connection=conn)

    result: list[bytes] = []
    reader = threading.Thread(target=lambda: result.append(transport.readline_stdout()))
    reader.start()
    time.sleep(0.3)  # longer than read_timeout: a quiet period, not EOF
    assert not result, "readline returned during a quiet period"
    conn.deliver(_out(b"late line\n"))
    reader.join(timeout=3)
    assert result == [b"late line\n"]


def test_close_stdin_signals_the_shell_rather_than_doing_nothing():
    # The runner closes stdin to make the CLI terminate. An inert close would
    # let a CLI reading stream-json from a never-closing pipe block until the
    # wall-clock deadline and be misreported as a timeout.
    transport, _, conn = _transport(connection=_FakeConnection(hold=True))
    transport.close_stdin()
    signals = [json.loads(m)["signal"] for m in conn.sent if isinstance(m, str)]
    assert "SIGHUP" in signals


def test_iter_events_yields_stdout_then_a_terminal_event():
    transport, _, _ = _transport([_out(b"hello\n"), _exit(0)])
    events = list(transport.iter_events())
    assert events[0]["type"] == "stdout"
    assert events[0]["text"] == "hello\n"
    assert events[-1]["type"] == "execution_complete"


def test_iter_events_reports_a_nonzero_exit_as_an_error_with_a_numeric_evalue():
    transport, _, _ = _transport([_exit(3)])
    assert list(transport.iter_events())[-1]["error"]["evalue"] == "3"


def test_iter_events_reports_a_lost_stream_distinctly():
    transport, _, _ = _transport([_out(b"partial\n")], drop_after=1)
    assert list(transport.iter_events())[-1]["error"]["evalue"] == PTY_STREAM_LOST


def test_pid_is_none_for_a_non_local_backend():
    transport, _, _ = _transport([_exit(0)])
    assert transport.pid is None


def test_env_bearing_command_is_passed_to_pty_create():
    # CreatePTYSessionRequest takes no envs, so the command string is the only
    # channel the agent's environment can travel through.
    transport, api, _ = _transport([_exit(0)])
    assert api.created[0]["command"] == "exec claude"
    assert api.created[0]["cwd"] == "/workspace"


# ── the reconnect trap ────────────────────────────────────────────────


def test_replay_frame_is_never_fed_into_the_stdout_stream():
    # 0x03 is [8-byte BE offset][raw bytes] from a SINGLE buffer shared by both
    # streams. It cannot be re-split, and interleaved bytes would corrupt the
    # stream-json parser far more destructively than dropping them.
    transport, _, _ = _transport(
        [_replay(0, b"stale interleaved bytes\n"), _out(b"live\n"), _exit(0)]
    )
    assert transport.readline_stdout() == b"live\n"


def test_transport_never_reopens_the_socket():
    # Re-attaching to a finished session runs StartPipe() again, launching a
    # SECOND agent process rather than resuming the first.
    calls: list[str] = []

    def factory(url, headers):
        calls.append(url)
        return _FakeConnection([_exit(0)])

    api = _FakeApi()
    transport = PtyWebSocketTransport(
        api,
        sandbox_id="sb-1",
        cwd="/workspace",
        command="exec claude",
        connect_factory=factory,
    )
    transport.start()
    transport.wait(timeout=2)
    transport.readline_stdout()
    assert len(calls) == 1


def test_dropped_socket_without_exit_frame_resolves_to_crash_not_hang():
    # GET /pty/{id} carries no exit code, so a missed exit frame is
    # unrecoverable. wait() must honour its deadline and report the break.
    transport, _, _ = _transport([_out(b"partial\n")], drop_after=1)
    assert transport.wait(timeout=2) is None
    assert transport.poll() is None
    assert transport.protocol_break_reason == PTY_STREAM_LOST


def test_wait_always_honours_its_deadline():
    conn = _FakeConnection([_out(b"x\n")], drop_after=None)
    transport, _, _ = _transport(connection=conn)
    assert transport.wait(timeout=0.2) is None


# ── shutdown ──────────────────────────────────────────────────────────


def test_shutdown_sends_sigint_then_deletes_the_pty_session():
    transport, api, conn = _transport(connection=_FakeConnection(hold=True))
    transport.shutdown(grace=1.0)
    signals = [json.loads(m)["signal"] for m in conn.sent if isinstance(m, str)]
    assert "SIGINT" in signals
    assert api.deleted == ["pty-1"]


def test_shutdown_is_idempotent():
    transport, api, _ = _transport([_exit(0)])
    transport.shutdown(grace=0.1)
    transport.shutdown(grace=0.1)
    assert api.deleted == ["pty-1"]


def test_close_stdin_does_not_kill_the_session():
    transport, api, _ = _transport([_exit(0)])
    transport.close_stdin()
    assert api.deleted == []


def test_start_is_required_before_io():
    api = _FakeApi()
    transport = PtyWebSocketTransport(
        api,
        sandbox_id="sb-1",
        cwd="/workspace",
        command="exec claude",
        connect_factory=lambda url, headers: _FakeConnection([_exit(0)]),
    )
    with pytest.raises(SandboxError):
        transport.write_stdin(b"x")


def test_iter_events_interleaves_stderr_with_stdout():
    # Draining stderr only after exit withholds it for the whole turn and leaves
    # stderr_digest empty for a run that never terminates.
    transport, _, _ = _transport([_out(b"a\n"), _err(b"b\n"), _out(b"c\n"), _exit(0)])
    kinds = [e["type"] for e in transport.iter_events()]
    assert "stderr" in kinds[:-1]


def test_iter_events_honours_a_deadline_instead_of_blocking_forever():
    # readline blocks until data or close, so a CLI that hangs writing nothing
    # would block any Protocol-level consumer of stream() indefinitely.
    transport, _, _ = _transport(connection=_FakeConnection(hold=True))
    events = list(transport.iter_events(deadline_seconds=0.3))
    assert events[-1] == {"type": "status", "text": "timeout"}


def test_read_available_never_blocks():
    stream = _line_stream()
    assert stream.read_available() == b""


def _line_stream():
    from app.modules.workspace.autonomous.sandbox.opensandbox.transport import _LineStream

    return _LineStream()
