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
        "installation_id": "openace-test",
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
    api.sandboxes["sb-1"] = {"id": "sb-1", "status": {"state": "Running"}, "metadata": {}}
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
    assert api.list_filters[-1] == {
        "openace.provider": "opensandbox",
        "openace.installation": "openace-test",
    }


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


def test_another_installations_sandboxes_are_never_destroyed(configured):
    """Two Open ACE deployments sharing one lifecycle server must not fight.

    Both stamp `openace.provider=opensandbox`, and each one's workflow rows
    live in its own database — so with a provider-only filter every sandbox
    belonging to the other reads as unclaimed, and the sweep deletes it
    mid-run. The installation tag is what separates them.
    """
    api = FakeOpenSandboxApi()
    provider = provider_for("opensandbox", api_factory=lambda endpoint: api)
    ours = provider.create(_spec("ours"))
    # Same product, same provider tag, different deployment.
    api.sandboxes["sb-theirs"] = {
        "id": "sb-theirs",
        "status": {"state": "Running"},
        "metadata": {
            "openace.provider": "opensandbox",
            "openace.installation": "openace-other-cluster",
            "openace.task_id": "t-1",
        },
    }
    destroyed = provider.reconcile_orphans(live_sandbox_ids={ours.sandbox_id})
    assert destroyed == []
    assert "sb-theirs" not in api.deleted


def test_a_server_that_ignores_the_filter_is_still_filtered_client_side(configured):
    """Belt and braces: the client-side check is the load-bearing half."""
    api = FakeOpenSandboxApi()
    provider = provider_for("opensandbox", api_factory=lambda endpoint: api)
    api.sandboxes["sb-theirs"] = {
        "id": "sb-theirs",
        "status": {"state": "Running"},
        "metadata": {
            "openace.provider": "opensandbox",
            "openace.installation": "openace-other-cluster",
        },
    }
    # A server that returns everything regardless of the metadata query.
    api.list_sandboxes = lambda metadata=None: list(api.sandboxes.values())  # type: ignore[assignment]
    assert provider.reconcile_orphans(live_sandbox_ids=set()) == []


def test_create_metadata_carries_the_installation_tag(configured):
    api = FakeOpenSandboxApi()
    provider = provider_for("opensandbox", api_factory=lambda endpoint: api)
    handle = provider.create(_spec("x"))
    metadata = api.sandboxes[handle.sandbox_id]["metadata"]
    assert metadata["openace.installation"] == "openace-test"


def test_destroy_attribution_reports_failure_instead_of_swallowing_it(configured):
    """The scheduler clears sandbox_id on the strength of this answer.

    Returning None unconditionally let a transient API outage discard the only
    handle a retry could use, stranding a live sandbox until its TTL.
    """
    api = FakeOpenSandboxApi()
    api.sandboxes["sb-1"] = {"id": "sb-1", "status": {"state": "Running"}, "metadata": {}}
    provider = provider_for("opensandbox", api_factory=lambda endpoint: api)

    def _boom(sandbox_id):
        raise RuntimeError("lifecycle server unreachable")

    api.delete_sandbox = _boom  # type: ignore[assignment]
    assert provider.destroy_attribution_checked("sb-1", None) is False
    # The Protocol-typed method stays None-returning and still never raises.
    assert provider.destroy_attribution("sb-1", None) is None


def test_destroy_attribution_reports_success(configured):
    api = FakeOpenSandboxApi()
    api.sandboxes["sb-1"] = {"id": "sb-1", "status": {"state": "Running"}, "metadata": {}}
    provider = provider_for("opensandbox", api_factory=lambda endpoint: api)
    assert provider.destroy_attribution_checked("sb-1", None) is True
    assert "sb-1" in api.deleted


def test_an_already_gone_sandbox_is_success_not_failure(configured):
    """Nothing to delete is the desired end state, not a retryable failure."""
    api = FakeOpenSandboxApi()
    provider = provider_for("opensandbox", api_factory=lambda endpoint: api)
    assert provider.destroy_attribution_checked("sb-missing", None) is True


def test_a_sandbox_on_a_non_default_tier_is_destroyed_on_its_own_server(tmp_path, monkeypatch):
    """The two-tier topology this repo ships, and the leak it used to cause.

    The workflow row records the provider NAME and nothing about the tier, so
    the post-restart sweep resolved to default_tier — the gvisor server for a
    kata tenant. delete_sandbox treats 404 as success, so deleting the kata
    sandbox's id against the gvisor server reported SUCCESS, the scheduler
    cleared the ids, and the real sandbox ran on to its TTL.
    """
    raw = {
        "installation_id": "openace-test",
        "default_tier": "gvisor",
        "tenant_tiers": {"42": "kata"},
        "endpoints": {
            tier: {
                "base_url": f"http://osb-{tier}.open-ace.svc.cluster.local:8080/v1",
                "api_key_env": "OSB_KEY",
                "execd_token_env": "OSB_EXECD_TOKEN",
                "runtime_class": "gvisor" if tier == "gvisor" else "kata-qemu",
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
            for tier in ("gvisor", "kata")
        },
        "image_allowlist": [_DIGEST],
    }
    path = tmp_path / "sandbox-backends.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    monkeypatch.setenv("OSB_KEY", "k")
    monkeypatch.setenv("OSB_EXECD_TOKEN", "t")
    monkeypatch.setenv("OPENACE_SANDBOX_BACKENDS", str(path))

    servers = {"gvisor": FakeOpenSandboxApi(), "kata": FakeOpenSandboxApi()}
    # The sandbox lives on kata; the sweep will resolve to gvisor.
    servers["kata"].sandboxes["sb-kata-1"] = {
        "id": "sb-kata-1",
        "status": {"state": "Running"},
        "metadata": {"openace.provider": "opensandbox", "openace.installation": "openace-test"},
    }

    def _factory(endpoint):
        return servers["kata" if "kata" in endpoint.base_url else "gvisor"]

    monkeypatch.setattr(registry, "_default_api_factory", _factory)
    provider = provider_for("opensandbox")

    assert provider.destroy_attribution_checked("sb-kata-1", None) is True
    assert "sb-kata-1" in servers["kata"].deleted, "the kata sandbox was never actually destroyed"
    assert "sb-kata-1" not in servers["gvisor"].deleted


def test_an_unauthenticatable_endpoint_does_not_pin_a_row_forever(tmp_path, monkeypatch):
    """A deterministic config fault must not read as a retryable failure.

    The scheduler runs as its own systemd unit and need not share the web
    process's environment, so a tier whose api_key_env is unset there fails
    identically on every sweep. Reporting that as "destroy failed" pinned the
    row `running` permanently while each sweep refreshed updated_at and re-armed
    the TTL reaper. The sandbox was found and deleted on its own server — the
    row must be allowed to close.
    """
    raw = {
        "installation_id": "openace-test",
        "default_tier": "gvisor",
        "endpoints": {
            tier: {
                "base_url": f"http://osb-{tier}.open-ace.svc.cluster.local:8080/v1",
                "api_key_env": "OSB_KEY" if tier == "gvisor" else "OSB_KEY_UNSET",
                "execd_token_env": "OSB_EXECD_TOKEN",
                "runtime_class": "gvisor" if tier == "gvisor" else "kata-qemu",
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
            for tier in ("gvisor", "kata")
        },
        "image_allowlist": [_DIGEST],
    }
    path = tmp_path / "sandbox-backends.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    monkeypatch.setenv("OSB_KEY", "k")
    monkeypatch.setenv("OSB_EXECD_TOKEN", "t")
    monkeypatch.delenv("OSB_KEY_UNSET", raising=False)
    monkeypatch.setenv("OPENACE_SANDBOX_BACKENDS", str(path))

    from app.modules.workspace.autonomous.sandbox.opensandbox.config import SandboxConfigError

    gvisor = FakeOpenSandboxApi()
    gvisor.sandboxes["sb-1"] = {"id": "sb-1", "status": {"state": "Running"}, "metadata": {}}

    class _Unauthenticatable:
        def get_sandbox(self, sandbox_id):
            raise SandboxConfigError("endpoint 'kata': API key env var 'OSB_KEY_UNSET' is unset")

    def _factory(endpoint):
        return _Unauthenticatable() if "kata" in endpoint.base_url else gvisor

    monkeypatch.setattr(registry, "_default_api_factory", _factory)
    provider = provider_for("opensandbox")

    assert provider.destroy_attribution_checked("sb-1", None) is True
    assert "sb-1" in gvisor.deleted


def test_a_transient_endpoint_error_is_still_reported_as_a_failure(configured, monkeypatch):
    """Convergence must not cost the safety property for retryable faults."""
    api = FakeOpenSandboxApi()
    api.sandboxes["sb-1"] = {"id": "sb-1", "status": {"state": "Running"}, "metadata": {}}
    provider = provider_for("opensandbox", api_factory=lambda endpoint: api)

    def _boom(sandbox_id):
        raise ConnectionError("lifecycle server unreachable")

    api.delete_sandbox = _boom  # type: ignore[assignment]
    assert provider.destroy_attribution_checked("sb-1", None) is False


def test_an_unconfirmed_delete_is_not_reported_as_success(configured):
    """DELETE is accepted, not applied.

    Upstream goes Stopping then Terminated over successive reads, and a server
    that ignored the request answers identically at the moment of the call.
    Returning True there let the scheduler clear the only id naming a sandbox
    that was still Running.
    """
    api = FakeOpenSandboxApi()
    api.sandboxes["sb-1"] = {"id": "sb-1", "status": {"state": "Running"}, "metadata": {}}
    api.stall_delete = True  # accepted, nothing happens
    provider = provider_for("opensandbox", api_factory=lambda endpoint: api)
    assert provider.destroy_attribution_checked("sb-1", None) is False


def test_an_endpoint_we_could_not_query_is_not_proof_of_absence(tmp_path, monkeypatch):
    """ "I could not ask" must never be recorded as "it is not there".

    The scheduler is its own unit and may lack a credential the web process
    had — which is exactly why an endpoint it cannot authenticate to may be the
    one that created the sandbox. Concluding absence there discards the only id
    that could name a live sandbox.
    """
    raw = {
        "installation_id": "openace-test",
        "default_tier": "gvisor",
        "endpoints": {
            tier: {
                "base_url": f"http://osb-{tier}.open-ace.svc.cluster.local:8080/v1",
                "api_key_env": "OSB_KEY" if tier == "gvisor" else "OSB_KEY_UNSET",
                "execd_token_env": "OSB_EXECD_TOKEN",
                "runtime_class": "gvisor" if tier == "gvisor" else "kata-qemu",
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
            for tier in ("gvisor", "kata")
        },
        "image_allowlist": [_DIGEST],
    }
    path = tmp_path / "sandbox-backends.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    monkeypatch.setenv("OSB_KEY", "k")
    monkeypatch.setenv("OSB_EXECD_TOKEN", "t")
    monkeypatch.delenv("OSB_KEY_UNSET", raising=False)
    monkeypatch.setenv("OPENACE_SANDBOX_BACKENDS", str(path))

    from app.modules.workspace.autonomous.sandbox.opensandbox.config import SandboxConfigError

    empty = FakeOpenSandboxApi()  # gvisor genuinely does not have it

    class _Unauthenticatable:
        def get_sandbox(self, sandbox_id):
            raise SandboxConfigError("endpoint 'kata': API key env var 'OSB_KEY_UNSET' is unset")

    monkeypatch.setattr(
        registry,
        "_default_api_factory",
        lambda endpoint: _Unauthenticatable() if "kata" in endpoint.base_url else empty,
    )
    provider = provider_for("opensandbox")
    # Not found on the one endpoint we could query, and the other was unusable:
    # absence is NOT established.
    assert provider.destroy_attribution_checked("sb-1", None) is False
