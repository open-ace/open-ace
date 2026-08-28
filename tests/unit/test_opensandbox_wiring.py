"""Production wiring for the OpenSandbox backend (#2023).

These are the tests that would have caught a provider that exists, is correct,
and is never actually reached — the failure mode that made both the isolation
gate and the orphan sweep pass green while production kept running Legacy.
"""

from __future__ import annotations

import json

import pytest

from app.modules.workspace.autonomous.sandbox import registry
from app.modules.workspace.autonomous.sandbox.opensandbox.fake_server import FakeOpenSandboxApi
from app.services.autonomous_scheduler import _destroy_orphan_sandbox

pytestmark = [pytest.mark.regression, pytest.mark.issue(2023)]

_DIGEST = "ghcr.io/open-ace/agent@sha256:" + "a" * 64


@pytest.fixture
def api(tmp_path, monkeypatch):
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
    monkeypatch.setenv("OSB_KEY", "k")
    monkeypatch.setenv("OSB_EXECD_TOKEN", "t")
    monkeypatch.setenv("OPENACE_SANDBOX_BACKENDS", str(path))
    fake = FakeOpenSandboxApi()
    fake.sandboxes["sb-1"] = {"id": "sb-1", "status": {"state": "Running"}, "metadata": {}}
    monkeypatch.setattr(registry, "_default_api_factory", lambda endpoint: fake)
    return fake


def test_node_and_control_plane_restart_reconcile_sandbox(api):
    # Asserted at the SCHEDULER layer, not on the provider method. The provider
    # method alone would pass green while production leaked: _destroy_orphan_sandbox
    # returned early for every provider except remote_machine, and
    # _reconcile_orphan_sandboxes then marked the row destroyed regardless.
    wf = {
        "workflow_id": "w1",
        "sandbox_provider": "opensandbox",
        "sandbox_id": "sb-1",
        "sandbox_remote_session_id": None,
    }
    _destroy_orphan_sandbox(wf, remote_session_manager=None)
    assert "sb-1" in api.deleted


def test_opensandbox_row_without_a_sandbox_id_is_a_no_op(api):
    wf = {
        "workflow_id": "w1",
        "sandbox_provider": "opensandbox",
        "sandbox_id": None,
        "sandbox_remote_session_id": None,
    }
    _destroy_orphan_sandbox(wf, remote_session_manager=None)
    assert not api.deleted


def test_legacy_row_still_no_ops(api):
    wf = {
        "workflow_id": "w1",
        "sandbox_provider": "legacy_posix",
        "sandbox_id": "sb-1",
        "sandbox_remote_session_id": None,
    }
    _destroy_orphan_sandbox(wf, remote_session_manager=None)
    assert not api.deleted


def test_remote_machine_reconcile_path_is_unchanged():
    stopped: list[str] = []

    class _Manager:
        def stop_session(self, session_id):
            stopped.append(session_id)

    wf = {
        "workflow_id": "w1",
        "sandbox_provider": "remote_machine",
        "sandbox_id": "sb-1",
        "sandbox_remote_session_id": "rs-9",
    }
    _destroy_orphan_sandbox(wf, remote_session_manager=_Manager())
    assert stopped == ["rs-9"]


def test_sweep_survives_a_provider_failure_on_one_row(api, monkeypatch):
    def _boom(endpoint):
        raise RuntimeError("registry exploded")

    monkeypatch.setattr(registry, "_default_api_factory", _boom)
    wf = {
        "workflow_id": "w1",
        "sandbox_provider": "opensandbox",
        "sandbox_id": "sb-1",
        "sandbox_remote_session_id": None,
    }
    # One bad row must never abort a sweep that walks many.
    _destroy_orphan_sandbox(wf, remote_session_manager=None)
