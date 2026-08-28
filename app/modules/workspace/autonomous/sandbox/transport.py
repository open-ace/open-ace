"""The agent IO seam that lets a non-local backend drive the CLI (#2023).

``agent_runner._run_local`` speaks the CLI stream-json protocol over an
interactive stdin, reading and writing a raw ``subprocess.Popen`` obtained from
``LegacyPosixProvider.get_process()`` — an escape hatch deliberately kept off
the :class:`SandboxProvider` Protocol. ``#2022`` recorded the consequence in a
note at ``agent_runner.py:2669``: a container backend has no local ``Popen``, so
reusing that path "requires abstracting the IO into a provider-returned
transport handle (the 'replaceable local seam')". This module is that seam.

:class:`LocalProcessTransport` is a strict pass-through over the existing
``Popen``, including the SIGTERM→wait→SIGKILL process-group escalation
``_run_local`` performs today. It is landed before any container code depends on
it precisely so "the existing local tests still pass" is a real verification
that the swap changed nothing.
"""

from __future__ import annotations

import os
import signal
import subprocess
from typing import Protocol, cast, runtime_checkable


@runtime_checkable
class AgentTransport(Protocol):
    """Everything ``_run_local`` needs from the agent process.

    ``pid`` is ``None`` for backends with no local process. Callers must treat
    that as "pid-keyed operations do not apply here" rather than passing it on:
    ``os.getpgid(None)`` raises ``TypeError``, which the surrounding handlers in
    ``_run_local`` do not catch.
    """

    def write_stdin(self, data: bytes) -> None: ...
    def close_stdin(self) -> None: ...
    def readline_stdout(self) -> bytes: ...
    def readline_stderr(self) -> bytes: ...
    def poll(self) -> int | None: ...
    def wait(self, timeout: float | None = None) -> int | None: ...
    def shutdown(self, grace: float = 5.0) -> None: ...

    @property
    def pid(self) -> int | None: ...


class LocalProcessTransport:
    """:class:`AgentTransport` over a local ``subprocess.Popen``.

    Every method delegates one-to-one. This class adds no behaviour, which is
    what makes swapping ``_run_local`` onto the seam safe: the Legacy path keeps
    the same object, the same syscalls and the same failure modes.
    """

    def __init__(self, process: subprocess.Popen) -> None:
        self._process = process

    @property
    def process(self) -> subprocess.Popen:
        """The wrapped process.

        ``_LocalSession.process`` stays populated on the Legacy path through
        this accessor. Nulling it universally would change behaviour at a dozen
        pid-keyed call sites on the hot path of every autonomous workflow.
        """
        return self._process

    @property
    def pid(self) -> int | None:
        return self._process.pid

    def write_stdin(self, data: bytes) -> None:
        stdin = self._process.stdin
        if stdin is None:
            return
        stdin.write(data)
        stdin.flush()

    def close_stdin(self) -> None:
        stdin = self._process.stdin
        if stdin is None or stdin.closed:
            return
        try:
            stdin.close()
        except (OSError, ValueError):
            # Already closed or the peer is gone; closing twice must be safe
            # because the runner closes stdin on several unrelated paths.
            pass

    def readline_stdout(self) -> bytes:
        stdout = self._process.stdout
        # Popen's streams are typed IO[Any]; the runner opens them in binary
        # mode, so the cast states what the pipes actually carry.
        return b"" if stdout is None else cast("bytes", stdout.readline())

    def readline_stderr(self) -> bytes:
        stderr = self._process.stderr
        return b"" if stderr is None else cast("bytes", stderr.readline())

    def poll(self) -> int | None:
        return self._process.poll()

    def wait(self, timeout: float | None = None) -> int | None:
        try:
            return self._process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None

    def shutdown(self, grace: float = 5.0) -> None:
        """Graceful then forceful teardown of the whole process group.

        Mirrors ``_run_local``'s existing escalation exactly: SIGTERM to the
        group, wait up to *grace*, then SIGKILL. The process group (rather than
        the pid) is the target because the CLI spawns children — killing only
        the leader would orphan them.
        """
        if self._process.poll() is not None:
            return
        try:
            pgid = os.getpgid(self._process.pid)
        except (ProcessLookupError, OSError):
            return
        try:
            os.killpg(pgid, signal.SIGTERM)
            self._process.wait(timeout=grace)
        except (ProcessLookupError, OSError, subprocess.TimeoutExpired):
            try:
                os.killpg(pgid, signal.SIGKILL)
            except (ProcessLookupError, OSError):
                pass
