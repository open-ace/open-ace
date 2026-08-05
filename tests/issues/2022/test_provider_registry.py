"""#2022 P6.3: provider_for — rebuild a SandboxProvider from its persisted name.

The reconciler rebuilds a provider from the ``sandbox_provider`` string on the
workflow row (the per-call instance that ran the task is gone after a restart).
"""

from __future__ import annotations

import pytest

from app.modules.workspace.autonomous.sandbox.legacy_posix import LegacyPosixProvider
from app.modules.workspace.autonomous.sandbox.provider import SandboxError
from app.modules.workspace.autonomous.sandbox.registry import provider_for
from app.modules.workspace.autonomous.sandbox.remote_machine import RemoteMachineProvider


class _FakeRSM:
    """Stand-in for RemoteSessionManager (RemoteMachineProvider stores it as-is)."""


def test_legacy_name_returns_legacy_provider() -> None:
    assert isinstance(provider_for("legacy_posix"), LegacyPosixProvider)


def test_empty_and_none_treated_as_legacy() -> None:
    # Pre-P5 rows have no sandbox_provider; treat as the local default.
    assert isinstance(provider_for(""), LegacyPosixProvider)
    assert isinstance(provider_for(None), LegacyPosixProvider)


def test_remote_name_returns_remote_provider() -> None:
    provider = provider_for("remote_machine", _FakeRSM())
    assert isinstance(provider, RemoteMachineProvider)


def test_remote_name_without_rsm_fails_closed() -> None:
    with pytest.raises(SandboxError):
        provider_for("remote_machine", None)


def test_unknown_name_fails_closed() -> None:
    with pytest.raises(SandboxError):
        provider_for("gVisor-future", None)
