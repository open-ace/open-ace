"""Issue #3378: build_create_request extensions + fake background:true modelling.

The sandboxed WebUI launcher (webui_sandbox.py) reuses the ONE create-request
builder. These tests pin the three optional overrides — entrypoint suffix
(bootstrap preserved verbatim), timeout override, extra metadata merge — and the
fake server's ``background: true`` /command semantics the launcher's restore and
snapshot sequences rely on.
"""

from __future__ import annotations

import pytest

from app.modules.workspace.autonomous.sandbox.opensandbox.config import parse_backend_config
from app.modules.workspace.autonomous.sandbox.opensandbox.fake_server import FakeOpenSandboxApi
from app.modules.workspace.autonomous.sandbox.opensandbox.policy import build_create_request
from app.modules.workspace.autonomous.sandbox.types import SandboxSpec

pytestmark = [pytest.mark.issue(3378)]

_DIGEST = "ghcr.io/open-ace/agent@sha256:" + "a" * 64

_FULL_ATTESTATIONS = {
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
    "ephemeral_storage_enforced": True,
}


def _cfg():
    return parse_backend_config(
        {
            "installation_id": "openace-test",
            "default_tier": "kata",
            "endpoints": {
                "kata": {
                    "base_url": "http://osb.open-ace.svc.cluster.local:8080/v1",
                    "api_key_env": "OSB_KEY",
                    "execd_token_env": "OSB_EXECD_TOKEN",
                    "runtime_class": "kata-qemu",
                    "default_image": _DIGEST,
                    "egress_allow_hosts": ["api.anthropic.com"],
                    "attestations": _FULL_ATTESTATIONS,
                }
            },
            "image_allowlist": [_DIGEST],
            "sandbox_ttl_seconds": 3600,
        }
    )


def _spec() -> SandboxSpec:
    return SandboxSpec(task_id="webui-1", project_path="/workspace/repo", cli_tool="qwen-code")


def _create(**kwargs):
    cfg = _cfg()
    return build_create_request(
        _spec(), cfg, cfg.endpoint_for(tenant=None, project_path=None), generation=1, **kwargs
    )


# ── entrypoint suffix ─────────────────────────────────────────────────


def test_entrypoint_suffix_preserves_bootstrap_verbatim():
    suffix = "exec qwen-code-webui --host 0.0.0.0 --port 3100"
    body = _create(entrypoint_suffix=suffix)
    entrypoint = body["entrypoint"]
    assert entrypoint[:2] == ["/bin/sh", "-c"]
    script = entrypoint[2]
    # The bootstrap half is preserved BYTE-FOR-BYTE: same explicit directory
    # list (F34: no brace expansion — dash), same chown, same safe.directory.
    bootstrap_prefix = (
        "mkdir -p /home/agent/tmp /home/agent/.cache /home/agent/.config "
        "/home/agent/.local/share /home/agent /workspace"
    )
    assert script.startswith(bootstrap_prefix)
    assert f"chown -R 1000:1000 /home/agent /workspace || true; " in script
    assert "git config --global --add safe.directory /workspace || true; " in script
    # The suffix REPLACES the placeholder exec, and follows the bootstrap.
    assert "exec tail -f /dev/null" not in script
    assert script.endswith(suffix)


def test_entrypoint_without_suffix_unchanged():
    """No suffix → the historical agent entrypoint, exactly as before."""
    with_suffix = _create()
    assert with_suffix["entrypoint"][2].endswith("exec tail -f /dev/null")


def test_entrypoint_suffix_replaces_placeholder_before_exec():
    """A restore gate between the bootstrap and the exec stays in order."""
    suffix = "i=0; until [ -f /workspace/.openace-restore-done ] || [ \"$i\" -ge 300 ]; do sleep 0.2; i=$((i+1)); done; exec qwen-code-webui --port 3100"
    script = _create(entrypoint_suffix=suffix)["entrypoint"][2]
    assert script.index("mkdir -p") < script.index(".openace-restore-done")
    assert script.index(".openace-restore-done") < script.index("exec qwen-code-webui")


# ── timeout override ──────────────────────────────────────────────────


def test_timeout_override_replaces_derived_ttl():
    body = _create(timeout_seconds=86400)
    assert body["timeout"] == 86400


def test_timeout_override_clamped_to_upstream_minimum():
    body = _create(timeout_seconds=30)
    # Upstream CreateSandboxRequest.timeout has minimum 60.
    assert body["timeout"] == 60


def test_timeout_defaults_to_config_ttl_without_override():
    body = _create()
    assert body["timeout"] == 3600


# ── extra metadata ────────────────────────────────────────────────────


def test_extra_metadata_merges_over_standard_keys():
    body = _create(
        extra_metadata={
            "openace.webui.kind": "webui",
            "openace.webui.process_generation": "abc123",
            "openace.webui.owner": "42",
        }
    )
    meta = body["metadata"]
    # The webui namespace rides ALONGSIDE the existing keys — F33:
    # openace.generation is the workflow-generation key and must survive.
    assert meta["openace.generation"] == "1"
    assert meta["openace.task_id"] == "webui-1"
    assert meta["openace.webui.kind"] == "webui"
    assert meta["openace.webui.process_generation"] == "abc123"
    assert meta["openace.webui.owner"] == "42"


def test_extra_metadata_values_coerced_to_str():
    body = _create(extra_metadata={"openace.webui.owner": 42})
    assert body["metadata"]["openace.webui.owner"] == "42"


def test_extra_metadata_can_shadow_a_standard_key():
    """A caller key wins the merge — one definition, no silent divergence."""
    body = _create(extra_metadata={"openace.tenant": "sandbox-tenant"})
    assert body["metadata"]["openace.tenant"] == "sandbox-tenant"


# ── fake server: background:true modelling ────────────────────────────


def _fake_with_sandbox() -> tuple[FakeOpenSandboxApi, str]:
    fake = FakeOpenSandboxApi()
    record = fake.create_sandbox({"metadata": {}})
    return fake, str(record["id"])


def test_fake_background_command_completes_immediately_without_output_events():
    fake, sid = _fake_with_sandbox()
    events = list(fake.run_command(sid, {"command": "tar -xf /tmp/webui-state.tar", "background": True}))
    kinds = [e["type"] for e in events]
    # Upstream: execution_complete fires immediately after launch; NO
    # stdout/stderr SSE events at all (policy.py:652-657).
    assert kinds == ["init", "execution_complete"]
    assert all("text" not in e or e["type"] == "init" for e in events)


def test_fake_background_command_status_is_pollable():
    fake, sid = _fake_with_sandbox()
    events = list(
        fake.run_command(sid, {"command": "touch /workspace/.openace-restore-done", "background": True})
    )
    command_id = next(e["text"] for e in events if e["type"] == "init")
    status = fake.command_status(sid, command_id)
    assert status is not None
    assert status["running"] is False
    assert status["exit_code"] == 0


def test_fake_foreground_command_still_streams_stdout():
    """The foreground path is untouched — the provider's evidence contract."""
    fake, sid = _fake_with_sandbox()
    events = list(fake.run_command(sid, {"command": "echo hi", "background": False}))
    kinds = [e["type"] for e in events]
    assert "stdout" in kinds
    assert kinds[-1] == "execution_complete"


def test_fake_metadata_filtered_list_already_exists():
    """Governance check: the metadata filter on list_sandboxes predates #3378."""
    fake, sid = _fake_with_sandbox()
    other = fake.create_sandbox({"metadata": {"openace.webui.kind": "webui"}})
    rows = fake.list_sandboxes({"openace.webui.kind": "webui"})
    assert [r["id"] for r in rows] == [other["id"]]
    assert sid not in [r["id"] for r in rows]
