"""AgentTransport seam: the local implementation must be a pass-through (#2023).

`_run_local` drives the coding-agent CLI over an interactive stdin
(`--input-format stream-json`) through `get_process()`, a Legacy-only escape
hatch that is deliberately not on the SandboxProvider Protocol. A container
backend has no local Popen, so the IO has to move behind a seam.

The whole point of landing `LocalProcessTransport` before any container code
depends on it is that it changes nothing: every method delegates to the wrapped
Popen, so "the existing local tests still pass" is a real verification rather
than a hope.
"""

from __future__ import annotations

import subprocess
import sys
import time

import pytest

from app.modules.workspace.autonomous.sandbox.transport import AgentTransport, LocalProcessTransport

pytestmark = [pytest.mark.regression, pytest.mark.issue(2023)]


def _spawn(script: str) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-u", "-c", script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )


def test_local_transport_round_trips_stdin_and_stdout():
    process = _spawn("import sys\nfor line in sys.stdin:\n    sys.stdout.write('got:' + line)")
    transport = LocalProcessTransport(process)
    try:
        transport.write_stdin(b"hello\n")
        assert transport.readline_stdout() == b"got:hello\n"
    finally:
        transport.shutdown(grace=1.0)


def test_local_transport_readline_returns_empty_bytes_at_eof():
    process = _spawn("pass")
    transport = LocalProcessTransport(process)
    transport.wait(timeout=5)
    assert transport.readline_stdout() == b""


def test_local_transport_reads_stderr_separately():
    process = _spawn("import sys\nsys.stderr.write('boom\\n')")
    transport = LocalProcessTransport(process)
    try:
        assert transport.readline_stderr() == b"boom\n"
    finally:
        transport.shutdown(grace=1.0)


def test_local_transport_poll_and_wait_match_the_wrapped_popen():
    process = _spawn("import sys\nsys.stdin.readline()")
    transport = LocalProcessTransport(process)
    assert transport.poll() is None
    transport.write_stdin(b"go\n")
    transport.close_stdin()
    assert transport.wait(timeout=5) == 0
    assert transport.poll() == 0
    assert process.returncode == 0


def test_local_transport_exposes_the_real_pid():
    process = _spawn("import sys\nsys.stdin.readline()")
    transport = LocalProcessTransport(process)
    try:
        assert transport.pid == process.pid
    finally:
        transport.shutdown(grace=1.0)


def test_local_transport_exposes_the_wrapped_process():
    # _LocalSession.process stays populated on the Legacy path so the existing
    # pid-keyed call sites keep working; the transport is the single source of
    # truth and this accessor is how the session gets the Popen.
    process = _spawn("import sys\nsys.stdin.readline()")
    transport = LocalProcessTransport(process)
    try:
        assert transport.process is process
    finally:
        transport.shutdown(grace=1.0)


def test_local_transport_close_stdin_is_idempotent():
    process = _spawn("import sys\nsys.stdin.read()")
    transport = LocalProcessTransport(process)
    transport.close_stdin()
    transport.close_stdin()
    assert transport.wait(timeout=5) == 0


def test_shutdown_escalates_sigterm_then_sigkill_on_the_process_group():
    # Reproduces today's teardown exactly: SIGTERM, wait, then SIGKILL. The
    # child ignores SIGTERM, so only the escalation ends it.
    process = _spawn(
        "import signal, time\n" "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n" "time.sleep(60)"
    )
    transport = LocalProcessTransport(process)
    started = time.monotonic()
    transport.shutdown(grace=0.5)
    assert transport.poll() is not None
    assert time.monotonic() - started < 10


def test_shutdown_on_an_already_dead_process_does_not_raise():
    process = _spawn("pass")
    transport = LocalProcessTransport(process)
    transport.wait(timeout=5)
    transport.shutdown(grace=0.5)


def test_local_transport_satisfies_the_protocol():
    process = _spawn("pass")
    transport = LocalProcessTransport(process)
    transport.wait(timeout=5)
    assert isinstance(transport, AgentTransport)


# ── provider wiring ───────────────────────────────────────────────────


def test_legacy_provider_get_transport_wraps_its_own_popen():
    from app.modules.workspace.autonomous.sandbox.legacy_posix import LegacyPosixProvider
    from app.modules.workspace.autonomous.sandbox.types import SandboxSpec

    provider = LegacyPosixProvider()
    handle = provider.create(SandboxSpec(task_id="t-1", project_path=".", cli_tool="claude-code"))
    exec_handle = provider.exec(
        handle, command=[sys.executable, "-c", "print('hi')"], env=None, exec_policy=None
    )
    try:
        transport = provider.get_transport(exec_handle)
        assert isinstance(transport, LocalProcessTransport)
        # Same underlying process as the escape hatch, which is what makes the
        # runner's swap a no-op for Legacy.
        assert transport.process is provider.get_process(exec_handle)
    finally:
        provider.destroy(handle)
