"""Registry resolution and orphan reconciliation for OpenSandbox (#2023)."""

from __future__ import annotations

import json

import pytest

from app.modules.workspace.autonomous.sandbox import registry
from app.modules.workspace.autonomous.sandbox.legacy_posix import LegacyPosixProvider
from app.modules.workspace.autonomous.sandbox.opensandbox.fake_server import FakeOpenSandboxApi
from app.modules.workspace.autonomous.sandbox.opensandbox.provider import OpenSandboxProvider
from app.modules.workspace.autonomous.sandbox.provider import SandboxError
from app.modules.workspace.autonomous.sandbox.registry import provider_for

pytestmark = [pytest.mark.regression, pytest.mark.issue(2023)]

_DIGEST = "ghcr.io/open-ace/agent@sha256:" + "a" * 64


def _write_config(tmp_path) -> str:
    raw = {
        "default_tier": "gvisor",
        "endpoints": {
            "gvisor": {
                "base_url": "http://osb.open-ace.svc.cluster.local:8080/v1",
                "api_key_env": "OSB_KEY",
                "execd_token_env": "OSB_EXECD_TOKEN",
                "runtime_class": "gvisor",
                "default_image": _DIGEST,
                "egress_allow_hosts": ["api.anthropic.com"],
                "attestations": {
                    "egress_enforced": True,
                    "egress_mode_dns_nft": True,
                    "metadata_cidr_blocked": True,
                    "execd_token_required": True,
                    "secure_access_required": True,
                    "nonroot_enforced": True,
                    "readonly_rootfs": True,
                    "seccomp_runtime_default": True,
                    "dedicated_service_account": True,
                    "pod_pids_limit": 512,
                },
            }
        },
        "image_allowlist": [_DIGEST],
    }
    path = tmp_path / "sandbox-backends.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return str(path)


@pytest.fixture
def configured(tmp_path, monkeypatch):
    monkeypatch.setenv("OSB_KEY", "k")
    monkeypatch.setenv("OSB_EXECD_TOKEN", "t")
    monkeypatch.setenv("OPENACE_SANDBOX_BACKENDS", _write_config(tmp_path))
    return tmp_path


# ── registry ──────────────────────────────────────────────────────────


def test_registry_resolves_opensandbox_name(configured):
    api = FakeOpenSandboxApi()
    provider = provider_for("opensandbox", api_factory=lambda endpoint: api)
    assert isinstance(provider, OpenSandboxProvider)


def test_registry_raises_when_backend_config_absent(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENACE_SANDBOX_BACKENDS", raising=False)
    monkeypatch.setattr(
        "app.modules.workspace.autonomous.sandbox.opensandbox.config."
        "DEFAULT_BACKEND_CONFIG_PATH",
        str(tmp_path / "etc.json"),
    )
    monkeypatch.setattr(
        "app.modules.workspace.autonomous.sandbox.opensandbox.config.USER_BACKEND_CONFIG_PATH",
        str(tmp_path / "user.json"),
    )
    # The workflow row says the task ran under OpenSandbox; substituting Legacy
    # would let the reconciler believe it tore down a sandbox it never touched.
    with pytest.raises(SandboxError):
        provider_for("opensandbox")


def test_registry_still_resolves_legacy_and_rejects_unknown_names():
    assert isinstance(provider_for("legacy_posix"), LegacyPosixProvider)
    assert isinstance(provider_for(""), LegacyPosixProvider)
    with pytest.raises(SandboxError):
        provider_for("gVisor-future")


def test_default_api_factory_is_monkeypatchable(configured, monkeypatch):
    # The scheduler's sweep builds its provider through provider_for and passes
    # no factory, so this indirection is the only seam a reconciliation test has.
    api = FakeOpenSandboxApi()
    monkeypatch.setattr(registry, "_default_api_factory", lambda endpoint: api)
    provider = provider_for("opensandbox")
    provider.destroy_attribution("sb-1", None)
    assert "sb-1" in api.deleted


# ── orphan sweep ──────────────────────────────────────────────────────


def test_reconcile_orphans_paginates_and_filters(configured):
    api = FakeOpenSandboxApi()
    provider = provider_for("opensandbox", api_factory=lambda endpoint: api)
    keep = provider.create(_spec("keep"))
    orphan_a = provider.create(_spec("a"))
    orphan_b = provider.create(_spec("b"))
    destroyed = provider.reconcile_orphans(live_sandbox_ids={keep.sandbox_id})
    assert sorted(destroyed) == sorted([orphan_a.sandbox_id, orphan_b.sandbox_id])
    assert api.list_filters[-1] == {"openace.provider": "opensandbox"}


def test_reconcile_never_destroys_sandboxes_without_our_metadata(configured):
    api = FakeOpenSandboxApi()
    provider = provider_for("opensandbox", api_factory=lambda endpoint: api)
    # A sandbox belonging to some other system on the same shared server.
    api.sandboxes["sb-foreign"] = {
        "id": "sb-foreign",
        "status": {"state": "Running"},
        "metadata": {"owner": "another-team"},
    }
    provider.reconcile_orphans(live_sandbox_ids=set())
    assert "sb-foreign" not in api.deleted


def test_reconcile_never_raises_on_a_failing_list(configured):
    class _Broken(FakeOpenSandboxApi):
        def list_sandboxes(self, metadata=None):
            raise SandboxError("boom")

    provider = provider_for("opensandbox", api_factory=lambda endpoint: _Broken())
    assert provider.reconcile_orphans(live_sandbox_ids=set()) == []


def _spec(task_id: str):
    from app.modules.workspace.autonomous.sandbox.types import SandboxSpec

    return SandboxSpec(task_id=task_id, project_path="/workspace", cli_tool="claude-code")
