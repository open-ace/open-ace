"""The shipped manifests must actually back the attestations (#2023).

An attestation is an operator promise the provider cannot verify at runtime.
That makes the manifests in `k8s/extras/opensandbox/` the only thing standing
behind several security capabilities — and nothing else in CI reads them. This
module is the link: it asserts the properties `sandbox-backends.json` is allowed
to attest are genuinely configured, so a manifest edit that quietly removes one
fails here rather than in production.

This is the #2082 discipline applied to YAML: a capability declared but not
enforced is the defect, and the enforcement lives in these files.
"""

from __future__ import annotations

import pathlib
import tomllib

import pytest
import yaml

pytestmark = [pytest.mark.regression, pytest.mark.issue(2023)]

_DIR = pathlib.Path(__file__).resolve().parents[2] / "k8s" / "extras" / "opensandbox"
_TIERS = ("gvisor", "kata")


def _server_toml(tier: str) -> dict:
    doc = yaml.safe_load((_DIR / f"configmap-{tier}.yaml").read_text(encoding="utf-8"))
    return tomllib.loads(doc["data"]["sandbox.toml"])


def _deployment(tier: str) -> dict:
    docs = [d for d in yaml.safe_load_all((_DIR / f"server-{tier}.yaml").read_text()) if d]
    return next(d for d in docs if d["kind"] == "Deployment")


@pytest.mark.parametrize("tier", _TIERS)
def test_gateway_ingress_backs_the_secure_access_attestation(tier):
    """`secure_access_required` is only true under gateway-mode ingress.

    Upstream honours `secureAccess` solely for Kubernetes sandboxes when
    `ingress.mode = "gateway"`. Under `direct` no per-sandbox credential is
    minted, every sandbox shares the static execd token, and a compromised
    agent reaches a peer's execd — which #2023's peer-isolation test forbids.
    """
    cfg = _server_toml(tier)
    assert cfg["ingress"]["mode"] == "gateway"
    gateway = cfg["ingress"]["gateway"]
    assert gateway["route"]["mode"] in ("wildcard", "uri", "header")
    address = gateway["address"]
    assert not address.startswith(("http://", "https://")), "address must carry no scheme"
    if gateway["route"]["mode"] == "wildcard":
        assert address.startswith("*."), "wildcard routing requires a wildcard domain"


@pytest.mark.parametrize("tier", _TIERS)
def test_the_secure_access_signing_keys_are_wired(tier):
    """Gateway mode with no signing keys mints nothing."""
    containers = _deployment(tier)["spec"]["template"]["spec"]["containers"]
    names = {e["name"] for c in containers for e in c.get("env", [])}
    assert "OPENSANDBOX_SECURE_ACCESS_KEYS" in names
    assert "OPENSANDBOX_SECURE_ACCESS_ACTIVE_KEY" in names


@pytest.mark.parametrize("tier", _TIERS)
def test_the_server_has_a_writable_persistent_store(tier):
    """Default store path is unwritable here, and it is touched at import time.

    `[store]` unset means `~/.opensandbox/opensandbox.db`; the pod runs as UID
    1000 with a read-only rootfs and only /tmp writable, and the snapshot
    repository creates its parent directory and schema while the lifecycle
    router is imported — so the server fails before readiness.
    """
    cfg = _server_toml(tier)
    store_path = cfg["store"]["path"]
    dep = _deployment(tier)
    container = dep["spec"]["template"]["spec"]["containers"][0]
    mounts = {m["mountPath"] for m in container["volumeMounts"]}
    assert any(
        store_path.startswith(m.rstrip("/") + "/") for m in mounts
    ), f"store path {store_path} is not under any mounted volume {sorted(mounts)}"
    # Snapshot metadata must survive a restart, so not an emptyDir.
    volumes = {v["name"]: v for v in dep["spec"]["template"]["spec"]["volumes"]}
    mount = next(m for m in container["volumeMounts"] if store_path.startswith(m["mountPath"]))
    assert "persistentVolumeClaim" in volumes[mount["name"]]


@pytest.mark.parametrize("tier", _TIERS)
def test_exactly_one_server_replica(tier):
    """Per-pod SQLite means two replicas keep disagreeing metadata."""
    assert _deployment(tier)["spec"]["replicas"] == 1


@pytest.mark.parametrize("tier", _TIERS)
def test_pod_hardening_attestations_are_backed_by_the_template(tier):
    """nonroot / readonly-rootfs / seccomp / dedicated SA live in one place."""
    doc = yaml.safe_load((_DIR / "configmap-sandbox-template.yaml").read_text())
    tpl = yaml.safe_load(doc["data"]["batchsandbox-template.yaml"])
    pod = tpl["spec"]["template"]["spec"]
    assert pod["securityContext"]["runAsNonRoot"] is True
    assert pod["securityContext"]["seccompProfile"]["type"] == "RuntimeDefault"
    assert pod["serviceAccountName"] == "opensandbox-sandbox"
    assert pod["automountServiceAccountToken"] is False
    sandbox = next(c for c in pod["containers"] if c["name"] == "sandbox")
    assert sandbox["securityContext"]["readOnlyRootFilesystem"] is True
    assert sandbox["securityContext"]["allowPrivilegeEscalation"] is False

    # ...and the template is REFERENCED, or none of the above is applied.
    cfg = _server_toml(tier)
    assert cfg["kubernetes"]["batchsandbox_template_file"]
    container = _deployment(tier)["spec"]["template"]["spec"]["containers"][0]
    mounts = {m["mountPath"] for m in container["volumeMounts"]}
    tpl_path = cfg["kubernetes"]["batchsandbox_template_file"]
    assert any(tpl_path.startswith(m.rstrip("/") + "/") for m in mounts)


@pytest.mark.parametrize("tier", _TIERS)
def test_egress_attestations_are_backed(tier):
    cfg = _server_toml(tier)
    assert cfg["egress"]["mode"] == "dns+nft"
    assert cfg["egress"]["image"], "required whenever clients send networkPolicy"
