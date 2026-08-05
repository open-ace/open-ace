"""SandboxProvider factory by persisted provider name (#2022 P6).

The startup/periodic reconciler rebuilds a provider from the
``sandbox_provider`` string on the workflow row — the per-call instance that
ran the task (and held its ``sandbox_id`` -> handle map) is gone after a
restart, so the sweep mints a fresh provider solely to reach
``destroy_attribution``.

Lives in its own module (not ``provider.py``) so the contract leaf does not
import the concrete backends: ``LegacyPosixProvider`` and
``RemoteMachineProvider`` both import ``provider.py``, so a factory there would
cycle.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.modules.workspace.autonomous.sandbox.legacy_posix import LegacyPosixProvider
from app.modules.workspace.autonomous.sandbox.provider import SandboxError
from app.modules.workspace.autonomous.sandbox.remote_machine import RemoteMachineProvider

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.modules.workspace.autonomous.sandbox.provider import SandboxProvider
    from app.modules.workspace.remote_session_manager import RemoteSessionManager

# Persisted names that resolve to the local POSIX backend. ``""``/``None`` cover
# pre-P5 rows (no sandbox_provider was written) — they ran locally, so treat them
# as Legacy. ``fake`` is test-only and never persisted to a prod workflow row.
_PROVIDER_LEGACY_NAMES = frozenset({"", "legacy_posix"})


def provider_for(
    name: str | None,
    remote_session_manager: RemoteSessionManager | None = None,
) -> SandboxProvider:
    """Rebuild a SandboxProvider from its persisted name (#2022 P6).

    * ``legacy_posix`` (also ``""`` / ``None`` for pre-P5 rows) →
      :class:`LegacyPosixProvider`.
    * ``remote_machine`` → :class:`RemoteMachineProvider`(remote_session_manager).
      The manager is required: without it remote ``destroy_attribution`` cannot
      reach the session, so the call fails closed rather than silently no-op'ing.
    * Anything else (including not-yet-implemented backends like gVisor) raises
      :class:`SandboxError` — fail closed on an unknown backend rather than guess.
    """
    normalized = (name or "").strip()
    if normalized == "remote_machine":
        if remote_session_manager is None:
            raise SandboxError("remote_machine provider requires a remote_session_manager")
        return RemoteMachineProvider(remote_session_manager)
    if normalized in _PROVIDER_LEGACY_NAMES:
        return LegacyPosixProvider()
    raise SandboxError(f"unknown sandbox_provider: {name!r}")
