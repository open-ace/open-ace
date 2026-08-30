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

from app.modules.workspace.autonomous.sandbox.opensandbox.config import runtime_family

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
    """Gateway mode needs the signing keys the gateway verifies."""
    containers = _deployment(tier)["spec"]["template"]["spec"]["containers"]
    names = {e["name"] for c in containers for e in c.get("env", [])}
    assert "OPENSANDBOX_SECURE_ACCESS_KEYS" in names
    assert "OPENSANDBOX_SECURE_ACCESS_ACTIVE_KEY" in names


@pytest.mark.parametrize("tier", _TIERS)
def test_the_documented_secret_recipe_produces_values_upstream_accepts(tier):
    """Asserting the env-var NAMES exist does not make the VALUES loadable.

    Upstream parses OPENSANDBOX_SECURE_ACCESS_KEYS as `<key_id>=<base64>` with
    key_id exactly one char in [0-9a-z], and the active key must be one of those
    ids. The README first shipped `openssl rand -hex 32` for both — no `=`, and
    a 64-char active key — which fails config validation, so the server never
    becomes ready. That is the same "pods cannot start" class the store fix
    closed, reintroduced through the docs.
    """
    import re

    readme = (_DIR / "README.md").read_text(encoding="utf-8")
    keys = re.search(rf"{tier}-secure-access-keys=\"([^\"]+)\"", readme)
    active = re.search(rf"{tier}-secure-access-active-key=\"([^\"]+)\"", readme)
    assert keys and active, "README no longer documents the secure-access secret"

    for entry in keys.group(1).split(","):
        key_id, sep, secret = entry.partition("=")
        assert sep == "=", f"{entry!r} is not key_id=base64"
        assert re.fullmatch(r"[0-9a-z]", key_id), f"key_id {key_id!r} must be one char [0-9a-z]"
        assert secret, "empty secret"
    assert re.fullmatch(r"[0-9a-z]", active.group(1)), "active key must be one char [0-9a-z]"
    assert active.group(1) in [e.partition("=")[0] for e in keys.group(1).split(",")]


@pytest.mark.parametrize("tier", _TIERS)
def test_the_route_mode_is_one_the_shipped_gateway_can_serve(tier):
    """Upstream's gateway accepts --mode <header|uri>; wildcard is not offered.

    Choosing `wildcard` would have the server hand out `<id>-<port>.<domain>`
    hosts the gateway cannot route, and demand wildcard DNS and a wildcard
    certificate besides.
    """
    gateway = _server_toml(tier)["ingress"]["gateway"]
    assert gateway["route"]["mode"] in ("header", "uri")
    assert not gateway["address"].startswith(
        "*."
    ), "a wildcard address only makes sense for wildcard routing"


def test_the_readme_does_not_tell_operators_to_set_a_wildcard_address():
    """Step 3's prose must agree with the route mode step 2 configures.

    It told operators the address "must be a wildcard domain". Under
    `route.mode = "header"` upstream's validate_ingress_mode refuses a wildcard
    address outright, so following the README produced a config-load failure and
    a server that never becomes ready — the same class as the secret recipe,
    reached through documentation rather than code.
    """
    readme = (_DIR / "README.md").read_text(encoding="utf-8")
    step = readme[readme.index("### 3. Set your gateway address") :]
    step = step[: step.index("### 4.")]
    # Deliberately not matching the old sentence verbatim: that was sensitive to
    # line wrapping, so reflowing the paragraph would defeat it. Assert the
    # instruction that must be present instead.
    assert "no wildcard" in step
    # And the shipped value itself is not a wildcard.
    for tier in _TIERS:
        assert not _server_toml(tier)["ingress"]["gateway"]["address"].startswith("*.")


@pytest.mark.parametrize("tier", _TIERS)
def test_the_store_volume_is_writable_by_the_runtime_user(tier):
    """A PVC mounted without fsGroup is root-owned and UID 1000 cannot write it."""
    pod = _deployment(tier)["spec"]["template"]["spec"]
    sc = pod["securityContext"]
    assert sc.get("runAsUser") == 1000
    assert sc.get("fsGroup") == 1000, (
        "no fsGroup: a freshly provisioned CSI volume mounts root:root 0755, so "
        "the server cannot create its SQLite file and dies at import time"
    )


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


def test_the_kata_tier_backs_the_egress_attestations():
    cfg = _server_toml("kata")
    assert cfg["egress"]["mode"] == "dns+nft"
    assert cfg["egress"]["image"], "required whenever clients send networkPolicy"


def test_the_gvisor_tier_configures_no_egress_sidecar():
    """gVisor + egress sidecar means every create is rejected upstream.

    The sidecar needs the iptables nat table, which gVisor's netstack lacks. A
    real server logs the incompatibility at startup and then refuses every
    create carrying a networkPolicy — which this provider always sends. This
    tier shipped WITH an egress block and was the default, so not one sandbox
    could have started. Only running a real server surfaced it.
    """
    assert "egress" not in _server_toml("gvisor"), (
        "the gVisor tier configures an egress sidecar it cannot use; every "
        "create would be rejected"
    )


def test_every_namespace_the_manifests_use_is_also_created():
    """`kubectl apply -k` must not require a namespace it does not define.

    Ten objects target `open-ace` and nothing created it, so applying to any
    cluster without it failed — and the README's Apply step named no such
    prerequisite. Caught only by a server-side dry-run against a real API
    server; a purely local `kubectl kustomize` renders happily.
    """
    # Read the resources directly rather than shelling out to `kubectl`: a
    # contributor without it got a hard FileNotFoundError, not a skip, and this
    # assertion needs no rendering — the kustomization is a flat resources list.
    kustomization = yaml.safe_load((_DIR / "kustomization.yaml").read_text(encoding="utf-8"))
    docs = []
    for name in kustomization["resources"]:
        docs.extend(d for d in yaml.safe_load_all((_DIR / name).read_text(encoding="utf-8")) if d)
    defined = {d["metadata"]["name"] for d in docs if d["kind"] == "Namespace"}
    used = {d["metadata"]["namespace"] for d in docs if d.get("metadata", {}).get("namespace")}
    missing = used - defined
    assert not missing, f"objects target namespaces nothing creates: {sorted(missing)}"


def test_the_docs_example_points_at_the_service_for_its_own_tier():
    """A `kata` tier whose base_url is the gVisor Service fails on every create.

    The example was migrated to Kata field by field and its base_url was left on
    `opensandbox`, which selects the gVisor deployment — so an operator copying
    it verbatim would hit the exact `networkPolicy is not compatible with
    runtime 'gvisor'` error the migration existed to eliminate.
    """
    import json
    import re

    md = (_DIR.parents[2] / "docs" / "sandbox-backends.md").read_text(encoding="utf-8")
    raw = json.loads(
        re.search(r"```json\n(\{.*?\n\})\n```", md, re.S).group(1).replace("<64 hex>", "a" * 64)
    )
    services = {}
    for tier in _TIERS:
        for d in yaml.safe_load_all((_DIR / f"server-{tier}.yaml").read_text()):
            if d and d["kind"] == "Service":
                services[tier] = d["metadata"]["name"]

    for tier_name, endpoint in raw["endpoints"].items():
        host = endpoint["base_url"].split("//", 1)[1].split(".", 1)[0]
        # config.runtime_family, NOT a local substring test — re-implementing it
        # here is exactly the drift that classifier exists to prevent, and the
        # substring form is what let `runsc` through in the first place.
        family = runtime_family(endpoint["runtime_class"])
        assert host == services[family], (
            f"tier {tier_name!r} declares runtime_class "
            f"{endpoint['runtime_class']!r} but base_url points at {host!r}, "
            f"which is the {'gvisor' if host == services['gvisor'] else host} Service"
        )


def test_no_document_claims_the_probe_verifies_the_kata_runtime():
    """Three sweeps in a row corrected some sites and missed others.

    The probe is one-directional: it positively verifies gVisor and only rules
    gVisor out for Kata. Kata is the sole usable tier, so an operator-facing
    document promising the kernel is checked against the declared runtime_class
    overstates the guarantee they actually have. This scans every doc rather
    than relying on the next person to grep exhaustively.
    """
    import re

    root = _DIR.parents[2]
    sources = [root / "docs" / "sandbox-backends.md", _DIR / "README.md"]
    # Phrasings that assert the probe enforces the declared class unconditionally.
    overclaims = [
        r"refuses to continue if the kernel does not match",
        r"does not take that on trust",
        r"\*\*verified\*\* by a `/proc/version` probe",
        r"probe would catch it and refuse every run",
    ]
    offenders = []
    for path in sources:
        text = path.read_text(encoding="utf-8")
        for pattern in overclaims:
            for m in re.finditer(pattern, text):
                offenders.append(f"{path.name}: {text[m.start():m.start() + 70]!r}")
    assert not offenders, "documents overstate what the runtime probe proves:\n" + "\n".join(
        offenders
    )
