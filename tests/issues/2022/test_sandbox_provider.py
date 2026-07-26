"""SandboxProvider contract — creation gate, handle, destroy, inspect (#2022 P1).

Pins the fail-closed capability gate (issue acceptance: "unsupported required
policy fail closed") and the handle/destroy/inspect surface, using the
in-memory ``FakeSandboxProvider`` that Phase 3/4 contract tests will reuse.
"""

from __future__ import annotations

import pytest

from app.modules.workspace.autonomous.sandbox.fake import FakeSandboxProvider
from app.modules.workspace.autonomous.sandbox.provider import CapabilityUnsupported, SandboxError
from app.modules.workspace.autonomous.sandbox.types import (
    SandboxCapability,
    SandboxHandle,
    SandboxSpec,
    SandboxStatus,
)

_FAKE_CAPS = frozenset(
    {
        SandboxCapability.PRIVATE_HOME_TMP_XDG,
        SandboxCapability.FILESYSTEM_ACL,
        SandboxCapability.CPU_MEM_PIDS_TIME_QUOTA,
        SandboxCapability.CREDENTIAL_TOKEN_BINDING,
    }
)


def _spec(**overrides) -> SandboxSpec:
    base = {"task_id": "t-1", "project_path": "/repo", "cli_tool": "claude-code"}
    base.update(overrides)
    return SandboxSpec(**base)


def test_capabilities_returns_declared_frozenset():
    provider = FakeSandboxProvider(capabilities=frozenset({SandboxCapability.PRIVATE_HOME_TMP_XDG}))
    assert provider.capabilities() == frozenset({SandboxCapability.PRIVATE_HOME_TMP_XDG})


def test_create_rejects_unsupported_required_capability():
    # Fake only offers the four Legacy-style caps; demanding namespace
    # isolation (a #2023-only capability) must fail closed at creation.
    provider = FakeSandboxProvider(capabilities=_FAKE_CAPS)
    spec = _spec(required_capabilities=frozenset({SandboxCapability.NAMESPACE_ISOLATION}))
    with pytest.raises(CapabilityUnsupported) as exc:
        provider.create(spec)
    assert SandboxCapability.NAMESPACE_ISOLATION in exc.value.missing_capabilities
    # SandboxError is the base type so callers can catch the family.
    assert isinstance(exc.value, SandboxError)


def test_create_rejects_when_any_required_capability_missing():
    provider = FakeSandboxProvider(capabilities=frozenset({SandboxCapability.PRIVATE_HOME_TMP_XDG}))
    spec = _spec(
        required_capabilities=frozenset(
            {SandboxCapability.PRIVATE_HOME_TMP_XDG, SandboxCapability.NETWORK_EGRESS_POLICY}
        )
    )
    with pytest.raises(CapabilityUnsupported) as exc:
        provider.create(spec)
    assert exc.value.missing_capabilities == frozenset({SandboxCapability.NETWORK_EGRESS_POLICY})


def test_create_accepts_when_all_required_capabilities_supported():
    provider = FakeSandboxProvider(capabilities=_FAKE_CAPS)
    handle = provider.create(_spec(required_capabilities=_FAKE_CAPS))
    assert isinstance(handle, SandboxHandle)


def test_create_mints_handle_with_id_generation_and_status():
    provider = FakeSandboxProvider()
    handle = provider.create(_spec())
    assert handle.sandbox_id  # provider-minted, non-empty
    assert handle.generation == 1
    assert handle.provider_name == "fake"
    # Handle snapshots status at creation; live status comes from inspect().
    assert handle.status == SandboxStatus.CREATED
    assert handle.spec.task_id == "t-1"


def test_create_mints_distinct_sandbox_ids():
    provider = FakeSandboxProvider()
    h1 = provider.create(_spec(task_id="t-a"))
    h2 = provider.create(_spec(task_id="t-b"))
    assert h1.sandbox_id != h2.sandbox_id


def test_inspect_returns_live_status_after_create():
    provider = FakeSandboxProvider()
    handle = provider.create(_spec())
    assert provider.inspect(handle) == SandboxStatus.CREATED


def test_destroy_marks_destroyed():
    provider = FakeSandboxProvider()
    handle = provider.create(_spec())
    provider.destroy(handle)
    assert provider.inspect(handle) == SandboxStatus.DESTROYED


def test_destroy_is_idempotent():
    provider = FakeSandboxProvider()
    handle = provider.create(_spec())
    provider.destroy(handle)
    provider.destroy(handle)  # second call must not raise
    assert provider.inspect(handle) == SandboxStatus.DESTROYED


def test_destroy_unknown_handle_is_idempotent():
    # A handle this provider never minted (e.g. orphan from a prior generation)
    # must not raise on destroy — reconciliation relies on this.
    provider = FakeSandboxProvider()
    orphan = SandboxHandle(
        sandbox_id="never-created",
        generation=1,
        provider_name="fake",
        spec=_spec(),
    )
    provider.destroy(orphan)  # no raise
