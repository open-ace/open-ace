"""Isolation-tier selection gate (Issue #2023).

Answers one question for the agent runner: *which* :class:`SandboxProvider`
should run this task, and is a weaker one acceptable?

The acceptance criterion this exists for is "production required policy must not
silently fall back to Legacy". The failure mode it guards is not a crash but a
quiet downgrade — a tenant configured for Kata isolation running on the local
POSIX path because a config file was missing, with nothing in the workflow row
to say so.

``requires_production_isolation`` has exactly one source: the
``production_required_tenants`` list in the backend config. Tenant keys are the
decimal string of the integer ``tenant_id`` this repository actually carries;
there is no tenant-name→id mapping anywhere in the codebase, so a slug key would
have been something nothing could supply.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from app.modules.workspace.autonomous.sandbox.provider import SandboxError

if TYPE_CHECKING:  # pragma: no cover - annotations only
    from app.modules.workspace.autonomous.sandbox.opensandbox.config import SandboxBackendConfig
    from app.modules.workspace.autonomous.sandbox.provider import SandboxProvider


def requires_production_isolation(
    tenant: str | int | None, config: SandboxBackendConfig | None
) -> bool:
    """Whether *tenant* may not fall back to a weaker backend."""
    if config is None:
        return False
    return config.requires_production_isolation(None if tenant is None else str(tenant))


def select_provider(
    *,
    tenant: str | int | None,
    project_path: str | None,
    config: SandboxBackendConfig | None,
    fallback: SandboxProvider,
    api_factory: Callable[[Any], Any] | None = None,
    event_sink: Callable[[str, dict], None] | None = None,
    connect_factory: Callable[[str, dict], Any] | None = None,
) -> SandboxProvider:
    """Return the provider for this task, or raise rather than downgrade.

    *fallback* is the caller's already-constructed provider — the runner's
    ``self._sandbox_provider``. Returning it unchanged when no backend config
    exists is deliberate: ``AgentRunner.__init__`` accepts an injected provider
    and several test suites rely on that injection, so constructing a fresh
    ``LegacyPosixProvider()`` here would silently discard it.

    A tenant in ``production_required_tenants`` gets OpenSandbox or an
    exception. There is no code path from "required" to Legacy.
    """
    required = requires_production_isolation(tenant, config)
    if config is None:
        if required:  # pragma: no cover - unreachable: required implies a config
            raise SandboxError(
                f"tenant {tenant!r} requires production isolation but no sandbox "
                "backend is configured"
            )
        return fallback

    if not config.rollout_includes(tenant=tenant, project_path=project_path):
        if required:  # pragma: no cover - parse-time validation rejects this pair
            raise SandboxError(
                f"tenant {tenant!r} requires production isolation but is excluded "
                "from the rollout allowlist"
            )
        return fallback

    tenant_key = None if tenant is None else str(tenant)
    try:
        from app.modules.workspace.autonomous.sandbox.opensandbox.provider import (
            OpenSandboxProvider,
        )
        from app.modules.workspace.autonomous.sandbox.registry import _default_api_factory

        return OpenSandboxProvider(
            config,
            api_factory=api_factory or _default_api_factory,
            tenant=tenant_key,
            project_path=project_path,
            event_sink=event_sink,
            connect_factory=connect_factory,
        )
    except SandboxError:
        if required:
            # Re-raise: this tenant may not run anywhere weaker.
            raise
        return fallback
