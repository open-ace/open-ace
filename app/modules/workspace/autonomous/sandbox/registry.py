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

from typing import TYPE_CHECKING, Any

from app.modules.workspace.autonomous.sandbox.legacy_posix import LegacyPosixProvider
from app.modules.workspace.autonomous.sandbox.provider import SandboxError
from app.modules.workspace.autonomous.sandbox.remote_machine import RemoteMachineProvider

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Callable

    from app.modules.workspace.autonomous.sandbox.provider import SandboxProvider
    from app.modules.workspace.remote_session_manager import RemoteSessionManager

# Persisted names that resolve to the local POSIX backend. ``""``/``None`` cover
# pre-P5 rows (no sandbox_provider was written) — they ran locally, so treat them
# as Legacy. ``fake`` is test-only and never persisted to a prod workflow row.
_PROVIDER_LEGACY_NAMES = frozenset({"", "legacy_posix"})


def _default_api_factory(endpoint):  # pragma: no cover - trivial indirection
    """Build the real HTTP client.

    Indirected through a module-level name so a test can monkeypatch it: the
    scheduler's orphan sweep builds its provider through :func:`provider_for`
    and passes no factory, so without this seam a reconciliation test would
    construct a real client against whatever ``base_url`` the config names.
    """
    from app.modules.workspace.autonomous.sandbox.opensandbox.client import HttpOpenSandboxApi

    return HttpOpenSandboxApi(endpoint)


def provider_for(
    name: str | None,
    remote_session_manager: RemoteSessionManager | None = None,
    *,
    event_sink: Callable[[str, dict], None] | None = None,
    api_factory: Callable[[Any], Any] | None = None,
    tenant: str | None = None,
    project_path: str | None = None,
) -> SandboxProvider:
    """Rebuild a SandboxProvider from its persisted name (#2022 P6).

    * ``legacy_posix`` (also ``""`` / ``None`` for pre-P5 rows) →
      :class:`LegacyPosixProvider`.
    * ``remote_machine`` → :class:`RemoteMachineProvider`(remote_session_manager).
      The manager is required: without it remote ``destroy_attribution`` cannot
      reach the session, so the call fails closed rather than silently no-op'ing.
    * ``opensandbox`` → :class:`OpenSandboxProvider` built from the backend
      config. A missing config raises rather than handing back a weaker
      provider: the workflow row says the task ran under OpenSandbox, so
      silently substituting Legacy would let the reconciler believe it had torn
      down a sandbox it never touched.
    * Anything else raises :class:`SandboxError` — fail closed on an unknown
      backend rather than guess.

    ``event_sink`` and ``api_factory`` are keyword-only and optional, so every
    existing call site (and ``tests/unit/test_sandbox_registry.py``) is
    unaffected.
    """
    normalized = (name or "").strip()
    if normalized == "opensandbox":
        return _build_opensandbox(
            event_sink=event_sink,
            api_factory=api_factory,
            tenant=tenant,
            project_path=project_path,
        )
    if normalized == "remote_machine":
        if remote_session_manager is None:
            raise SandboxError("remote_machine provider requires a remote_session_manager")
        return RemoteMachineProvider(remote_session_manager)
    if normalized in _PROVIDER_LEGACY_NAMES:
        return LegacyPosixProvider()
    raise SandboxError(f"unknown sandbox_provider: {name!r}")


def _build_opensandbox(
    *,
    event_sink: Callable[[str, dict], None] | None,
    api_factory: Callable[[Any], Any] | None,
    tenant: str | None,
    project_path: str | None,
) -> SandboxProvider:
    from app.modules.workspace.autonomous.sandbox.opensandbox.config import load_backend_config
    from app.modules.workspace.autonomous.sandbox.opensandbox.provider import OpenSandboxProvider

    config = load_backend_config()
    if config is None:
        raise SandboxError(
            "sandbox_provider 'opensandbox' requires a backend config "
            "(OPENACE_SANDBOX_BACKENDS / /etc/openace/sandbox-backends.json); "
            "refusing to substitute a weaker provider"
        )
    return OpenSandboxProvider(
        config,
        api_factory=api_factory or _default_api_factory,
        tenant=tenant,
        project_path=project_path,
        event_sink=event_sink,
    )
