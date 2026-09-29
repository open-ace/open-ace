"""A ``subprocess.Popen``-shaped adapter over :class:`PtyWebSocketTransport`.

``ZCodeAppServerSession`` (``remote-agent/zcode_app_server.py``) was written
against a real ``Popen``: it writes str/bytes to ``.stdin``, iterates
``.stdout`` / ``.stderr`` line by line, reads ``.pid`` / ``.returncode``, and
calls ``.terminate()`` / ``.kill()`` / ``.wait()``. To run the app-server
inside an OpenSandbox pod (#3323) without retyping that ~1000-line session
class, this wraps the PTY transport in that same surface. The change is thereby
confined to the sandbox path; the local ``Popen`` path stays byte-identical.

The transport already behaves like a blocking line-reader — ``readline_stdout``
returns ``b""`` only on a real EOF (an ``exit`` frame or a broken stream), never
on a timeout — so the ``.stdout`` / ``.stderr`` generators terminate exactly
when the process does, with no busy-spin.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, cast


class _TransportStdin:
    """The ``.stdin`` half: normalise str/bytes to the transport's bytes API."""

    def __init__(self, transport: Any) -> None:
        self._transport = transport

    def write(self, data: str | bytes) -> None:
        if isinstance(data, str):
            data = data.encode("utf-8")
        self._transport.write_stdin(data)

    def flush(self) -> None:
        # The transport sends each write immediately; nothing to flush.
        return None

    def close(self) -> None:
        self._transport.close_stdin()


def _line_iter(readline: Any) -> Iterator[bytes]:
    """Yield successive lines from *readline* until it reports EOF (``b""``)."""
    while True:
        line = readline()
        if not line:
            return
        yield line


class TransportPopenAdapter:
    """Expose a ``PtyWebSocketTransport`` through the subset of ``Popen`` that
    ``ZCodeAppServerSession`` uses.

    ``.pid`` is ``None`` (there is no local process); callers already guard on
    that (pid registration is skipped, and the session's pause/resume — which
    ``os.kill`` the pid — short-circuit). ``terminate()`` and ``kill()`` both map
    to the transport's single ``shutdown`` (SIGINT + PTY teardown), which is
    idempotent, so the session's terminate→wait→kill escalation is safe.
    """

    def __init__(self, transport: Any) -> None:
        self._transport = transport
        self.stdin = _TransportStdin(transport)
        self.stdout: Iterator[bytes] = _line_iter(transport.readline_stdout)
        self.stderr: Iterator[bytes] = _line_iter(transport.readline_stderr)

    @property
    def pid(self) -> int | None:
        return cast("int | None", self._transport.pid)

    @property
    def returncode(self) -> int | None:
        return cast("int | None", self._transport.returncode)

    def poll(self) -> int | None:
        return cast("int | None", self._transport.poll())

    def terminate(self) -> None:
        self._transport.shutdown()

    def kill(self) -> None:
        self._transport.shutdown()

    def wait(self, timeout: float | None = None) -> int | None:
        return cast("int | None", self._transport.wait(timeout))
