"""#2022 P6.2: destroy_attribution — reconcile-time destroy by persisted id.

After a crash/restart the per-call provider instance (and its sandbox_id ->
remote_session_id map) is gone. The reconciler has only the strings persisted
to the workflow row, so ``destroy()`` — which keys off a live handle — cannot
resolve the remote session. ``destroy_attribution(sandbox_id, remote_session_id)``
is the contract path for teardown-by-attribution: Legacy no-ops (the local proc
died with the server; DB-reset is correct), Remote stops the session by id.
gVisor (#2023) will kill its sandbox by id here.
"""

from __future__ import annotations

from app.modules.workspace.autonomous.sandbox.fake import FakeSandboxProvider
from app.modules.workspace.autonomous.sandbox.legacy_posix import LegacyPosixProvider
from app.modules.workspace.autonomous.sandbox.remote_machine import RemoteMachineProvider


class _FakeRSM:
    """Minimal RemoteSessionManager double for the destroy path."""

    def __init__(self) -> None:
        self.stop_calls: list[str] = []
        self.stop_raises = False

    def stop_session(self, session_id: str) -> bool:
        self.stop_calls.append(session_id)
        if self.stop_raises:
            raise RuntimeError("boom")
        return True


def test_legacy_destroy_attribution_is_noop() -> None:
    # Local proc died with the server; nothing to destroy. Must not raise.
    provider = LegacyPosixProvider()
    provider.destroy_attribution("any-sandbox-id", None)


def test_remote_destroy_attribution_stops_session_by_id() -> None:
    rsm = _FakeRSM()
    provider = RemoteMachineProvider(rsm)
    # Fresh instance — _remote_sid is empty, so destroy(handle) could not find
    # this. destroy_attribution must use the PASSED id (persisted attribution).
    provider.destroy_attribution("sandbox-1", "remote-session-42")
    assert rsm.stop_calls == ["remote-session-42"]


def test_remote_destroy_attribution_none_remote_id_is_noop() -> None:
    # A local/gVisor row (or pre-P6 remote row) has no remote_session_id.
    rsm = _FakeRSM()
    provider = RemoteMachineProvider(rsm)
    provider.destroy_attribution("sandbox-1", None)
    assert rsm.stop_calls == []


def test_remote_destroy_attribution_swallows_stop_errors() -> None:
    # Best-effort + idempotent: a failing or repeated stop must not raise
    # (the reconciler sweeps many rows and must not abort on one failure).
    rsm = _FakeRSM()
    rsm.stop_raises = True
    provider = RemoteMachineProvider(rsm)
    provider.destroy_attribution("sandbox-1", "remote-session-42")
    provider.destroy_attribution("sandbox-1", "remote-session-42")


def test_fake_records_destroy_attribution() -> None:
    provider = FakeSandboxProvider()
    provider.destroy_attribution("sandbox-1", "remote-9")
    assert provider.destroy_attribution_calls == [("sandbox-1", "remote-9")]
