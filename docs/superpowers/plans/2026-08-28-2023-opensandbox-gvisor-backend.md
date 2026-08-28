# OpenSandbox / gVisor–Kata Production Sandbox Backend — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement issue #2023 — a production `SandboxProvider` backed by OpenSandbox's Kubernetes runtime with gVisor by default and Kata for high-security tenants, with fail-closed policy translation, credential-free workspace transfer, control-plane ChangeSet validation, warm-pool hygiene, orphan reconciliation, and a no-silent-downgrade selection gate.

**Architecture:** A new `opensandbox` package under `app/modules/workspace/autonomous/sandbox/` implements the frozen `#2022` `SandboxProvider` Protocol over OpenSandbox's two REST surfaces (server Lifecycle API `/v1`, in-sandbox Execd API `:44772`). Because OpenSandbox configures gVisor/Kata **server-side**, isolation tiers route to separately configured endpoints rather than passing a runtime field. Capabilities are *derived from the resolved endpoint config* so a declared guarantee always corresponds to a real request field or command wrapper. All HTTP goes through an injectable `OpenSandboxApi` Protocol whose in-memory fake drives every test, so the suite runs in the SQLite `test(3.x)` CI lane with no cluster.

**Tech Stack:** Python 3.10+, `requests` (no new dependency), `dataclasses`, `pytest`. Upstream: OpenSandbox (Apache-2.0, Go) Lifecycle + Execd OpenAPI.

**Spec:** `docs/superpowers/specs/2026-08-28-2023-opensandbox-gvisor-backend-design.md`

---

## File Structure

**Create:**

| File | Responsibility |
| --- | --- |
| `app/modules/workspace/autonomous/sandbox/opensandbox/__init__.py` | package re-exports |
| `.../opensandbox/config.py` | `EndpointConfig`, `ChangesetLimits`, `SandboxBackendConfig`, path precedence, parsing, fail-closed validation |
| `.../opensandbox/client.py` | `OpenSandboxApi` Protocol, `HttpOpenSandboxApi`, `OpenSandboxApiError`, SSE line parser |
| `.../opensandbox/policy.py` | pure `SandboxSpec`+`AgentTaskPolicy` → request-dict translation, capability derivation, state/terminal-reason mapping, fail-closed refusals |
| `.../opensandbox/workspace.py` | snapshot build, manifest parse, ChangeSet validation, all-or-nothing apply |
| `.../opensandbox/provider.py` | `OpenSandboxProvider` — the Protocol implementation |
| `.../opensandbox/fake_server.py` | in-memory `OpenSandboxApi` double with fault injection |
| `.../sandbox/isolation_tier.py` | required-isolation resolution; refuses Legacy downgrade |
| `k8s/extras/opensandbox/*.yaml` | RuntimeClasses, server Deployment/Service, per-tier ConfigMap, NetworkPolicy, ServiceAccount+RBAC, image admission policy |
| `docs/sandbox-backends.md` | operator guide + Legacy/gVisor/Kata performance-cost-compatibility report |
| `tests/unit/test_opensandbox_config.py` | config precedence + fail-closed validation |
| `tests/unit/test_opensandbox_policy.py` | request translation, egress defaults, capability realism |
| `tests/unit/test_opensandbox_client.py` | REST shapes, auth header, SSE parsing, error mapping |
| `tests/unit/test_opensandbox_workspace.py` | snapshot exclusion, ChangeSet rejection classes |
| `tests/unit/test_opensandbox_provider.py` | lifecycle, contract conformance, evidence, warm pool |
| `tests/unit/test_opensandbox_reconcile.py` | `destroy_attribution` + orphan sweep |
| `tests/unit/test_opensandbox_isolation_tier.py` | no-silent-downgrade gate |

**Modify:**

| File | Change |
| --- | --- |
| `app/modules/workspace/autonomous/sandbox/registry.py` | resolve `"opensandbox"`; keep unknown names fail-closed |
| `app/modules/workspace/autonomous/sandbox/__init__.py` | re-export `OpenSandboxProvider` |

Every test file starts with:

```python
pytestmark = [pytest.mark.regression, pytest.mark.issue(2023)]
```

Placement follows `docs/TEST_LAYERS.md`: canonical layer `tests/unit/`, never `tests/issues/` (retired by #2429), never a duplicate copy.

---

### Task 1: Config — tier→endpoint routing, fail-closed

**Files:**
- Create: `app/modules/workspace/autonomous/sandbox/opensandbox/__init__.py`
- Create: `app/modules/workspace/autonomous/sandbox/opensandbox/config.py`
- Test: `tests/unit/test_opensandbox_config.py`

- [ ] **Step 1: Write the failing tests**

```python
"""OpenSandbox backend config — precedence and fail-closed validation (#2023)."""

from __future__ import annotations

import json

import pytest

from app.modules.workspace.autonomous.sandbox.opensandbox.config import (
    SandboxConfigError,
    load_backend_config,
    parse_backend_config,
)

pytestmark = [pytest.mark.regression, pytest.mark.issue(2023)]


def _raw(**overrides) -> dict:
    base = {
        "default_tier": "gvisor",
        "endpoints": {
            "gvisor": {
                "base_url": "http://osb.svc:8080/v1",
                "api_key_env": "OSB_KEY_GVISOR",
                "runtime_class": "gvisor",
                "egress_enforced": True,
                "ephemeral_storage_enforced": True,
            }
        },
        "image_allowlist": ["ghcr.io/open-ace/agent@sha256:" + "a" * 64],
        "egress_allow_hosts": ["api.anthropic.com"],
    }
    base.update(overrides)
    return base


def test_resolves_default_tier_endpoint():
    cfg = parse_backend_config(_raw())
    assert cfg.endpoint_for(tenant=None, project_path=None).runtime_class == "gvisor"


def test_tenant_tier_overrides_default():
    raw = _raw(
        endpoints={
            **_raw()["endpoints"],
            "kata": {
                "base_url": "http://osb-kata.svc:8080/v1",
                "api_key_env": "OSB_KEY_KATA",
                "runtime_class": "kata-qemu",
                "egress_enforced": True,
                "ephemeral_storage_enforced": True,
            },
        },
        tenant_tiers={"acme": "kata"},
    )
    cfg = parse_backend_config(raw)
    assert cfg.endpoint_for(tenant="acme", project_path=None).runtime_class == "kata-qemu"
    assert cfg.endpoint_for(tenant="other", project_path=None).runtime_class == "gvisor"


def test_tier_pointing_at_missing_endpoint_fails_closed():
    # A tenant mapped to a tier with no configured endpoint must RAISE, never
    # silently fall back to a weaker tier. This is the acceptance item
    # "production required policy cannot silently fall back".
    cfg = parse_backend_config(_raw(tenant_tiers={"acme": "kata"}))
    with pytest.raises(SandboxConfigError):
        cfg.endpoint_for(tenant="acme", project_path=None)


def test_egress_enforced_without_allowlist_is_rejected():
    with pytest.raises(SandboxConfigError):
        parse_backend_config(_raw(egress_allow_hosts=[]))


@pytest.mark.parametrize(
    "host",
    ["169.254.169.254", "metadata.google.internal", "10.0.0.5", "127.0.0.1", "192.168.1.1"],
)
def test_metadata_and_private_hosts_rejected_from_allowlist(host):
    with pytest.raises(SandboxConfigError):
        parse_backend_config(_raw(egress_allow_hosts=["api.anthropic.com", host]))


def test_tag_only_image_rejected_from_allowlist():
    with pytest.raises(SandboxConfigError):
        parse_backend_config(_raw(image_allowlist=["ghcr.io/open-ace/agent:v1"]))


def test_missing_config_file_returns_none(tmp_path):
    assert load_backend_config(str(tmp_path / "absent.json")) is None


def test_malformed_config_raises_rather_than_defaulting(tmp_path):
    path = tmp_path / "sandbox-backends.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(SandboxConfigError):
        load_backend_config(str(path))


def test_explicit_path_wins_over_system_paths(tmp_path, monkeypatch):
    path = tmp_path / "sandbox-backends.json"
    path.write_text(json.dumps(_raw()), encoding="utf-8")
    monkeypatch.setenv("OPENACE_SANDBOX_BACKENDS", str(path))
    cfg = load_backend_config()
    assert cfg is not None
    assert cfg.default_tier == "gvisor"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_opensandbox_config.py -q`
Expected: collection error — `ModuleNotFoundError: ...sandbox.opensandbox.config`

- [ ] **Step 3: Implement `config.py`**

Public surface (exact signatures — later tasks depend on these names):

```python
class SandboxConfigError(SandboxError): ...

@dataclass(frozen=True)
class ChangesetLimits:
    max_files: int = 2000
    max_file_bytes: int = 10 * 1024 * 1024
    max_total_bytes: int = 100 * 1024 * 1024

@dataclass(frozen=True)
class EndpointConfig:
    tier: str
    base_url: str
    api_key_env: str
    runtime_class: str
    egress_enforced: bool = False
    ephemeral_storage_enforced: bool = False
    execd_port: int = 44772
    exec_uid: int = 1000
    exec_gid: int = 1000
    pool_ref: str = ""
    pool_egress_preapplied: bool = False
    def api_key(self) -> str: ...          # reads os.environ[api_key_env]; raises if unset/empty

@dataclass(frozen=True)
class SandboxBackendConfig:
    default_tier: str
    endpoints: Mapping[str, EndpointConfig]
    tenant_tiers: Mapping[str, str]
    project_tiers: Mapping[str, str]
    image_allowlist: frozenset[str]
    egress_allow_hosts: tuple[str, ...]
    resource_defaults: Mapping[str, str]
    sandbox_ttl_seconds: int
    changeset_limits: ChangesetLimits
    def tier_for(self, *, tenant: str | None, project_path: str | None) -> str: ...
    def endpoint_for(self, *, tenant: str | None, project_path: str | None) -> EndpointConfig: ...

def candidate_backend_config_paths(explicit: str | None = None) -> tuple[str, ...]: ...
def resolve_backend_config_path(explicit: str | None = None) -> str | None: ...
def parse_backend_config(raw: Mapping[str, object]) -> SandboxBackendConfig: ...
def load_backend_config(explicit: str | None = None) -> SandboxBackendConfig | None: ...
```

Behaviour requirements:

- `candidate_backend_config_paths` precedence: `explicit` → `$OPENACE_SANDBOX_BACKENDS` → `/etc/openace/sandbox-backends.json` → `~/.open-ace/sandbox-backends.json`. Mirror the shape of `task_isolation.candidate_agent_task_policy_paths` (dedupe, preserve order, skip falsy).
- `load_backend_config` returns `None` when no candidate exists; raises `SandboxConfigError` on unreadable-but-present or malformed JSON. Never silently defaults.
- `tier_for` resolution order: `project_tiers` exact path match → `tenant_tiers` → `default_tier`.
- `endpoint_for` raises `SandboxConfigError` when the resolved tier has no endpoint.
- Validation at parse time, all raising `SandboxConfigError`:
  - `default_tier` present in `endpoints`.
  - every endpoint has non-empty `base_url`, `api_key_env`, `runtime_class`; `base_url` scheme is `http`/`https`.
  - `exec_uid`/`exec_gid` are non-zero (root exec is refused at config load, not only at exec time).
  - any endpoint with `egress_enforced` requires a non-empty `egress_allow_hosts`.
  - every `egress_allow_hosts` entry is an FQDN or `*.`-wildcard, is not an IP literal, and does not resolve to a reserved name. Reject the metadata hostnames (`metadata.google.internal`, `metadata`, `instance-data`) and any literal IP; reject any entry parseable as an IP address whether or not it is private, since upstream cannot express IP rules at all.
  - every `image_allowlist` entry is digest-pinned: contains `@sha256:` followed by 64 lowercase hex chars.
  - `changeset_limits` values are positive ints.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_opensandbox_config.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add app/modules/workspace/autonomous/sandbox/opensandbox/ tests/unit/test_opensandbox_config.py
git commit -m "feat(#2023): OpenSandbox backend config with tier->endpoint routing"
```

---

### Task 2: REST client and `OpenSandboxApi` seam

**Files:**
- Create: `app/modules/workspace/autonomous/sandbox/opensandbox/client.py`
- Test: `tests/unit/test_opensandbox_client.py`

- [ ] **Step 1: Write the failing tests**

Cover, with a stubbed `requests.Session` (no network):

```python
def test_create_sandbox_sends_api_key_header_and_disables_proxies():
    # Asserts header OPEN-SANDBOX-API-KEY and proxies={"http": None, "https": None}
    # on EVERY call — the gevent-recursion + CLAUDE.md outbound rule.

def test_delete_sandbox_treats_404_as_success():
    # destroy() must be idempotent per the #2022 contract.

def test_non_2xx_raises_openSandbox_api_error_with_status_and_code():
    # {"code": "INVALID_REQUEST", "message": "..."} -> OpenSandboxApiError

def test_sse_parser_yields_typed_events_and_ignores_ping_and_comments():
    raw = (
        b": keepalive\n\n"
        b"data: {\"type\":\"init\"}\n\n"
        b"data: {\"type\":\"ping\"}\n\n"
        b"data: {\"type\":\"stdout\",\"text\":\"hello\\n\"}\n\n"
        b"data: {\"type\":\"execution_complete\",\"execution_time\":12}\n\n"
    )
    assert [e["type"] for e in iter_sse_events(_fake_response(raw))] == [
        "init", "stdout", "execution_complete",
    ]

def test_sse_parser_tolerates_multiline_data_and_truncated_tail():
    # A stream cut mid-event must not raise; the partial trailing event is dropped.

def test_command_status_maps_running_and_exit_code():
def test_upload_file_builds_metadata_then_file_multipart_parts():
    # metadata part is JSON {"path","mode"} and precedes the file part, per spec.
def test_get_execd_base_url_uses_endpoints_port_response():
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_opensandbox_client.py -q`
Expected: `ModuleNotFoundError: ...opensandbox.client`

- [ ] **Step 3: Implement `client.py`**

```python
class OpenSandboxApiError(SandboxError):
    def __init__(self, message: str, *, status_code: int | None = None, code: str = "") -> None: ...

class OpenSandboxApi(Protocol):
    # ── Lifecycle API (server, base_url ends in /v1) ──
    def create_sandbox(self, body: dict) -> dict: ...
    def get_sandbox(self, sandbox_id: str) -> dict | None: ...        # None on 404
    def list_sandboxes(self) -> list[dict]: ...
    def delete_sandbox(self, sandbox_id: str) -> None: ...            # 404 == success
    def pause_sandbox(self, sandbox_id: str) -> None: ...
    def resume_sandbox(self, sandbox_id: str) -> None: ...
    def renew_expiration(self, sandbox_id: str, expires_at: str) -> None: ...
    def get_endpoint(self, sandbox_id: str, port: int) -> dict: ...   # {"endpoint":..., "headers":{...}}
    # ── Execd API (inside the sandbox) ──
    def upload_file(self, sandbox_id: str, path: str, data: bytes, mode: int) -> None: ...
    def download_file(self, sandbox_id: str, path: str) -> bytes: ...
    def run_command(self, sandbox_id: str, body: dict) -> Iterator[dict]: ...  # SSE events
    def command_status(self, sandbox_id: str, command_id: str) -> dict | None: ...
    def command_logs(self, sandbox_id: str, command_id: str, cursor: int = 0) -> tuple[str, int]: ...
    def interrupt_command(self, sandbox_id: str, command_id: str) -> None: ...

def iter_sse_events(response) -> Iterator[dict]: ...

class HttpOpenSandboxApi:
    def __init__(self, endpoint: EndpointConfig, *, session=None, timeout: float = 30.0) -> None: ...
```

Implementation requirements:

- One private `_request(method, url, **kw)` used by every call. It **must** pass
  `proxies={"http": None, "https": None}` and the
  `OPEN-SANDBOX-API-KEY: <endpoint.api_key()>` header. Carry this comment
  verbatim above it, because `scripts/lint` and reviewers look for it:

```python
# 直接调用原因：OpenSandbox 服务端是集群内地址（如 *.svc.cluster.local / 私网 IP），
# app.utils.outbound_url_guard.safe_request 按设计拒绝私网目标，无法用于本调用。
# base_url 仅来自运维配置（sandbox-backends.json），永不来自用户输入，故无 SSRF 面。
# proxies=None 关闭代理查找，避免 gevent 环境下的 RecursionError（CLAUDE.md #2237）。
```

- `delete_sandbox` and `get_sandbox` translate `404` (never raise / return `None`).
- Everything else raises `OpenSandboxApiError` carrying `status_code` and the
  upstream `code` field.
- Execd base URL is resolved lazily per sandbox via `get_endpoint(sandbox_id, execd_port)` and cached; the returned `headers` map is merged into every execd request.
- `iter_sse_events` parses `text/event-stream`: accumulate `data:` lines until a blank line, `json.loads` the joined payload, skip `:` comments, skip `{"type":"ping"}`, drop a truncated trailing event rather than raising.

- [ ] **Step 4: Run tests** — `python -m pytest tests/unit/test_opensandbox_client.py -q` → PASS

- [ ] **Step 5: Commit**

```bash
git add app/modules/workspace/autonomous/sandbox/opensandbox/client.py tests/unit/test_opensandbox_client.py
git commit -m "feat(#2023): thin OpenSandbox REST client behind an injectable Protocol"
```

---

### Task 3: Policy translation, capability derivation, fail-closed refusals

**Files:**
- Create: `app/modules/workspace/autonomous/sandbox/opensandbox/policy.py`
- Test: `tests/unit/test_opensandbox_policy.py`

- [ ] **Step 1: Write the failing tests**

Includes required test #2 verbatim by name:

```python
def test_default_egress_blocks_metadata_private_cidr_and_unknown_domain():
    body = build_create_request(_spec(), cfg, endpoint, generation=1)
    policy = body["networkPolicy"]
    assert policy["defaultAction"] == "deny"
    targets = {rule["target"] for rule in policy["egress"]}
    assert all(rule["action"] == "allow" for rule in policy["egress"])
    assert "169.254.169.254" not in targets
    assert not any(t.startswith(("10.", "192.168.", "172.16.", "127.")) for t in targets)
    assert "evil.example.com" not in targets
    assert targets == {"api.anthropic.com"}


def test_allow_cidrs_fail_closed_because_upstream_egress_has_no_ip_rules():
    # Upstream NetworkRule.target: "FQDN or wildcard domain ... IP/CIDR not yet
    # supported in the egress MVP". Silently dropping the CIDR list would run a
    # spec that LOOKS restrictive with those rules simply absent.
    spec = _spec(network_egress=NetworkEgressPolicy(mode="allow_explicit", allow_cidrs=("10.0.0.0/8",)))
    with pytest.raises(SandboxError):
        build_create_request(spec, cfg, endpoint, generation=1)


def test_unrestricted_egress_mode_refused():
def test_host_backed_volume_refused():
def test_image_outside_allowlist_refused():
def test_tag_only_image_refused():
def test_capabilities_include_egress_only_when_endpoint_enforces_it():
def test_capabilities_include_storage_quota_only_when_endpoint_enforces_it():
def test_every_declared_capability_has_an_observable_enforcement_artifact():
    # Capability-realism probe (the #2082 lesson). For each declared capability,
    # assert a concrete artifact exists in the built request / command wrapper.
def test_resource_limits_carry_cpu_memory_and_ephemeral_storage_from_policy():
def test_ulimit_prefix_sets_pids_and_file_size_from_policy():
def test_env_is_constructed_from_allowlist_and_never_inherits_process_env(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret")
    monkeypatch.setenv("GH_TOKEN", "ghp_secret")
    env = build_env(_spec(), cfg, endpoint, proxy_token="t")
    assert "GITHUB_TOKEN" not in env and "GH_TOKEN" not in env
def test_sandbox_state_mapping_is_total():
    # every documented SandboxState maps to a SandboxStatus; unknown -> ERROR
def test_metadata_carries_task_id_tenant_and_generation():
```

- [ ] **Step 2: Run tests to verify they fail** — `ModuleNotFoundError: ...opensandbox.policy`

- [ ] **Step 3: Implement `policy.py`**

```python
def derive_capabilities(endpoint: EndpointConfig) -> frozenset[SandboxCapability]: ...
def validate_spec_for_endpoint(spec: SandboxSpec, cfg: SandboxBackendConfig,
                               endpoint: EndpointConfig) -> None: ...
    # NOTE: distinct from provider.validate_spec_capabilities (the shared #2022
    # gate). This one adds the OpenSandbox-specific refusals, then delegates to
    # that shared gate. Do not collapse the two names.
def build_network_policy(spec: SandboxSpec, cfg: SandboxBackendConfig) -> dict: ...
def build_resource_limits(policy: AgentTaskPolicy | None, cfg: SandboxBackendConfig,
                          endpoint: EndpointConfig) -> dict[str, str]: ...
def build_env(spec: SandboxSpec, cfg: SandboxBackendConfig, endpoint: EndpointConfig,
              *, proxy_token: str = "", extra: Mapping[str, str] | None = None) -> dict[str, str]: ...
def build_ulimit_prefix(policy: AgentTaskPolicy | None) -> str: ...
def build_create_request(spec: SandboxSpec, cfg: SandboxBackendConfig,
                         endpoint: EndpointConfig, *, generation: int,
                         tenant: str | None = None) -> dict: ...
def map_state(state: str) -> SandboxStatus: ...
def terminal_reason_for(*, exit_code: int | None, timed_out: bool, cancelled: bool,
                        has_status: bool) -> TerminalReason: ...
```

Requirements:

- `derive_capabilities`: always `NAMESPACE_ISOLATION`, `CREDENTIAL_TOKEN_BINDING`, `PRIVATE_HOME_TMP_XDG`, `FILESYSTEM_ACL`, `CPU_MEM_PIDS_TIME_QUOTA`; add `NETWORK_EGRESS_POLICY` iff `endpoint.egress_enforced`; add `STORAGE_INODE_QUOTA` iff `endpoint.ephemeral_storage_enforced`.
- `validate_spec_for_endpoint` raises `SandboxError` for: `network_egress.allow_cidrs` non-empty; `network_egress.mode == "unrestricted"`; any `VolumeSpec` that is host-backed or whose `mount_path` is not under the sandbox workspace root; `runtime.image` absent from `cfg.image_allowlist`; `exec_uid == 0`. Then calls the shared `provider.validate_spec_capabilities(derive_capabilities(endpoint), spec)` so the standard fail-closed gate still runs.
- `build_network_policy`: always `{"defaultAction": "deny", "egress": [{"action": "allow", "target": h} for h in cfg.egress_allow_hosts]}` merged with `spec.network_egress.allow_hosts` **intersected** against `cfg.egress_allow_hosts` — a spec can narrow the operator allowlist, never widen it.
- `build_resource_limits`: `cpu`/`memory` from `AgentTaskPolicy` when set (`memory_max_bytes` → `"<n>"`, `cpu_max` passed through), else `cfg.resource_defaults`; add `ephemeral-storage` only when `endpoint.ephemeral_storage_enforced` and `policy.ephemeral_storage_limit > 0`.
- `build_env`: start from `{}` — never `dict(os.environ)`. Set `HOME`, `TMPDIR`, `XDG_CACHE_HOME`, `XDG_CONFIG_HOME`, `XDG_DATA_HOME` under the sandbox workspace tree, `PATH`, and the LLM-proxy vars only. Explicitly assert no `GITHUB_TOKEN`/`GH_TOKEN`/`GH_CONFIG_DIR` key is ever emitted.
- `build_ulimit_prefix`: `"ulimit -u <pids_max> -f <blocks>; "` when the policy sets them, else `""`. `-f` takes 512-byte blocks — convert from `ephemeral_storage_limit` and document the unit.
- `map_state`: `Pending`→`CREATED`; `Running`,`Resuming`→`RUNNING`; `Pausing`,`Paused`→`PAUSED`; `Stopping`→`STOPPED`; `Terminated`→`DESTROYED`; `Failed`→`ERROR`; unknown→`ERROR`.

- [ ] **Step 4: Run tests** → PASS
- [ ] **Step 5: Commit** — `feat(#2023): fail-closed policy translation and config-derived capabilities`

---

### Task 4: Workspace snapshot and ChangeSet validation

**Files:**
- Create: `app/modules/workspace/autonomous/sandbox/opensandbox/workspace.py`
- Test: `tests/unit/test_opensandbox_workspace.py`

- [ ] **Step 1: Write the failing tests**

Includes required tests #1 and #3 by name:

```python
def test_sandbox_cannot_read_host_or_peer_workspace(tmp_path):
    # .git/, credential files and anything outside the worktree never enter the
    # snapshot, so the trusted Git common-dir is unreachable from the sandbox.
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("[remote]", encoding="utf-8")
    (tmp_path / ".git-credentials").write_text("https://x:y@github.com", encoding="utf-8")
    (tmp_path / ".netrc").write_text("machine github.com", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET=1", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print(1)", encoding="utf-8")
    paths = {e.path for e in build_snapshot(str(tmp_path))}
    assert paths == {"src/main.py"}


def test_changeset_rejects_absolute_path_symlink_escape_and_oversize_file(tmp_path):
    limits = ChangesetLimits(max_files=10, max_file_bytes=100, max_total_bytes=1000)
    entries = [
        ChangeSetEntry(path="/etc/passwd", mode=0o644, size=1),
        ChangeSetEntry(path="../../outside.txt", mode=0o644, size=1),
        ChangeSetEntry(path="link", mode=0o120000, size=1, symlink_target="/etc/shadow"),
        ChangeSetEntry(path="big.bin", mode=0o644, size=999),
        ChangeSetEntry(path="setuid.sh", mode=0o104755, size=1),
        ChangeSetEntry(path="ok.py", mode=0o644, size=1),
    ]
    rejections = validate_changeset(entries, root=str(tmp_path), limits=limits)
    reasons = {r.path: r.reason for r in rejections}
    assert reasons["/etc/passwd"] == "absolute_path"
    assert reasons["../../outside.txt"] == "path_escape"
    assert reasons["link"] == "symlink_escape"
    assert reasons["big.bin"] == "file_too_large"
    assert reasons["setuid.sh"] == "unsafe_mode"
    assert "ok.py" not in reasons


def test_apply_is_all_or_nothing_when_any_entry_is_rejected(tmp_path):
    # One bad entry means NOTHING is written — a partial apply must be impossible.

def test_changeset_rejects_over_file_count_and_total_size():
def test_changeset_rejects_secret_bearing_paths():
def test_snapshot_excludes_ssh_and_gh_config_trees(tmp_path):
def test_snapshot_refuses_symlink_pointing_outside_worktree(tmp_path):
```

- [ ] **Step 2: Run tests to verify they fail** — `ModuleNotFoundError: ...opensandbox.workspace`

- [ ] **Step 3: Implement `workspace.py`**

```python
@dataclass(frozen=True)
class SnapshotEntry:
    path: str          # worktree-relative, POSIX separators
    data: bytes
    mode: int

@dataclass(frozen=True)
class ChangeSetEntry:
    path: str
    mode: int
    size: int
    sha256: str = ""
    symlink_target: str = ""

@dataclass(frozen=True)
class ChangeSetRejection:
    path: str
    reason: str        # absolute_path | path_escape | symlink_escape | file_too_large
                       # | too_many_files | total_too_large | unsafe_mode | secret_path
    detail: str = ""

_EXCLUDED_DIRS = frozenset({".git", ".ssh"})
_EXCLUDED_NAMES = frozenset({".git-credentials", ".netrc", ".npmrc", ".pypirc"})
_EXCLUDED_GLOBS = (".env", ".env.*", "*.pem", "*.key", "id_rsa*", "hosts.yml")

def build_snapshot(worktree_path: str) -> Iterator[SnapshotEntry]: ...
def parse_manifest(payload: bytes | str) -> list[ChangeSetEntry]: ...
def validate_changeset(entries: Sequence[ChangeSetEntry], *, root: str,
                       limits: ChangesetLimits) -> list[ChangeSetRejection]: ...
def apply_changeset(entries: Sequence[ChangeSetEntry], *, root: str,
                    limits: ChangesetLimits, fetch: Callable[[str], bytes]) -> None: ...
```

Requirements:

- `build_snapshot` walks the worktree, skips `_EXCLUDED_DIRS` **at any depth**, skips excluded names/globs, skips symlinks whose resolved target leaves the worktree, and yields regular files only with `mode & 0o777`.
- `validate_changeset` returns **all** rejections (not first-fail) so the audit event lists everything wrong. Path checks run on the normalized path: reject `os.path.isabs`, reject any `..` component, reject when `os.path.realpath(join(root, path))` is not under `os.path.realpath(root)`. Mode check rejects setuid/setgid/sticky and any non-regular type except an in-tree symlink. Symlink targets get the same escape check.
- `apply_changeset` calls `validate_changeset` first and raises `SandboxError` when it returns anything — **before** the first write. Writes go to a temp dir then move into place, so an I/O failure mid-way cannot leave a partial tree.

- [ ] **Step 4: Run tests** → PASS
- [ ] **Step 5: Commit** — `feat(#2023): credential-free snapshot and all-or-nothing ChangeSet validation`

---

### Task 5: In-memory fake OpenSandbox server

**Files:**
- Create: `app/modules/workspace/autonomous/sandbox/opensandbox/fake_server.py`
- Test: exercised by Tasks 6–8 (no standalone test file; a test double with no tests of its own would be redundant)

- [ ] **Step 1: Implement `FakeOpenSandboxApi`**

Implements `OpenSandboxApi` fully in memory:

- `create_sandbox` records the body verbatim (so policy tests can assert on it), mints `sb-<n>`, sets `status.state = "Running"`.
- `run_command` returns a scripted SSE event list, defaulting to
  `init` → `stdout` → `execution_complete`, and records the request body.
- Fault injection knobs on the constructor: `fail_create`, `fail_status`,
  `state_after_create`, `scripted_events`, `scripted_exit_code`,
  `scripted_signal`, `scripted_timeout`, `drop_stream_after`, `not_found_ids`.
  `scripted_timeout=True` makes `run_command` end its stream with no terminal
  event and `command_status` report `running: True` past the deadline, which is
  what drives `test_resource_limits_return_structured_terminal_reason`.
- Public inspection attributes the tests assert on: `created_bodies`
  (list of every create body), `command_bodies`, `uploaded` (`{sandbox_id: {path: bytes}}`),
  `deleted` (set of destroyed ids), `sandboxes` (`{id: sandbox dict}`).
- `list_sandboxes` returns everything not deleted — the reconcile sweep's input.
- Deleting an unknown id is a no-op (matching the real 404-as-success rule).

- [ ] **Step 2: Commit** — `test(#2023): in-memory OpenSandbox API double`

---

### Task 6: `OpenSandboxProvider` lifecycle

**Files:**
- Create: `app/modules/workspace/autonomous/sandbox/opensandbox/provider.py`
- Modify: `app/modules/workspace/autonomous/sandbox/__init__.py`
- Test: `tests/unit/test_opensandbox_provider.py`

- [ ] **Step 1: Write the failing tests**

Contract conformance plus required tests #4, #5 and #8 by name:

```python
def test_provider_satisfies_sandbox_provider_protocol():
    # Every method on the frozen Protocol exists with a compatible signature.
    for name in ("capabilities", "create", "upload_workspace", "exec", "stream",
                 "pause", "resume", "stop", "collect_changes",
                 "collect_execution_evidence", "destroy", "destroy_attribution",
                 "inspect"):
        assert callable(getattr(provider, name))


def test_create_rejects_required_capability_the_endpoint_does_not_enforce():
    # endpoint with egress_enforced=False + spec requiring NETWORK_EGRESS_POLICY
    with pytest.raises(CapabilityUnsupported):
        provider.create(spec)


def test_destroy_is_idempotent_and_404_is_success():
def test_inspect_maps_upstream_state_to_sandbox_status():
def test_stale_generation_handle_is_refused():


def test_resource_limits_return_structured_terminal_reason():
    # An OOM kill (exit 137) and a timeout must NEVER surface as COMPLETED.
    api = FakeOpenSandboxApi(scripted_exit_code=137)
    ...
    evidence = provider.collect_execution_evidence(handle)[0]
    assert evidence.terminal_reason == TerminalReason.SIGNAL.value
    assert evidence.exit_code == 137

    api = FakeOpenSandboxApi(scripted_timeout=True)
    ...
    assert evidence.terminal_reason == TerminalReason.TIMEOUT.value


def test_execution_evidence_matches_provider_contract():
    evidence = provider.collect_execution_evidence(handle)
    assert isinstance(evidence, list)
    row = evidence[0]
    assert row.sandbox_id == handle.sandbox_id
    assert row.sandbox_generation == handle.generation
    assert row.cwd == handle.spec.project_path
    assert row.exit_code == 0
    assert row.terminal_reason == TerminalReason.COMPLETED.value


def test_warm_pool_does_not_reuse_tenant_state():
    # Two allocations from the same pool: the second must carry none of the
    # first's env, proxy token, workspace content or evidence rows.
    ...
    assert second_body["env"] != first_body["env"]
    assert provider.collect_execution_evidence(handle2) == []  # fresh evidence namespace


def test_warm_pool_refused_when_pool_egress_not_preapplied():
    # Upstream rejects networkPolicy alongside poolRef, so a pool without a
    # pre-applied egress policy would silently run with weaker networking.
    with pytest.raises(SandboxError):
        provider.create(spec)


def test_stream_emits_normalized_lifecycle_events():
    kinds = [e.kind for e in provider.stream(exec_handle)]
    assert kinds[0] == SandboxEventKind.PROCESS_STARTED
    assert SandboxEventKind.COMMAND_STARTED in kinds
    assert kinds[-1] == SandboxEventKind.PROCESS_EXITED


def test_stream_falls_back_to_status_polling_when_sse_drops_midway():
def test_exec_never_runs_as_root():
def test_upload_workspace_sends_no_git_or_credential_files():


def test_effective_policy_snapshot_reports_declared_capabilities():
    # #2020 Phase B: build_effective_policy derives `enforced` from the DECLARED
    # capability set, so it must line up with what the endpoint actually enforces.
    snapshot = build_effective_policy("opensandbox", provider.capabilities(), policy)
    assert snapshot["provider"] == "opensandbox"
    assert snapshot["enforced"]["ephemeral_storage"] is endpoint.ephemeral_storage_enforced
    assert snapshot["enforced"]["memory"] is True
```

- [ ] **Step 2: Run tests to verify they fail** — `ModuleNotFoundError: ...opensandbox.provider`

- [ ] **Step 3: Implement `provider.py`**

```python
class OpenSandboxProvider:
    def __init__(self, config: SandboxBackendConfig, *, api_factory=HttpOpenSandboxApi,
                 tenant: str | None = None, event_sink: Callable[[dict], None] | None = None) -> None: ...
```

Implements every Protocol method per spec §6. Notes that the implementer will
otherwise get wrong:

- `capabilities()` calls `derive_capabilities(self._endpoint)` — never a module constant.
- `create()` order: resolve endpoint → `validate_spec_for_endpoint` → `build_create_request` → `api.create_sandbox` → mint `SandboxHandle(provider_name="opensandbox", generation=1)`. On any failure, best-effort `delete_sandbox` then re-raise.
- Per-sandbox state lives in dicts keyed by `sandbox_id` and is **popped** on `destroy`, so nothing survives into a later allocation.
- `exec()` builds `{"command": build_ulimit_prefix(policy) + shlex.join(command), "cwd": spec.project_path, "background": True, "timeout": ms, "uid": endpoint.exec_uid, "gid": endpoint.exec_gid, "envs": build_env(...)}`. Refuse `uid == 0`.
- `stream()` maps SSE `stdout`/`stderr` → `STDOUT_CHUNK`/`STDERR_CHUNK`, `execution_complete` → `COMMAND_COMPLETED`, `error` → `SANDBOX_ERROR`; wraps with `PROCESS_STARTED`/`COMMAND_STARTED` at the head and `PROCESS_EXITED` at the tail. On stream exhaustion without a terminal event, poll `command_status` and emit `COMMAND_TIMED_OUT` or `COMMAND_COMPLETED` accordingly — **never** synthesize `COMMAND_COMPLETED` for a non-completion.
- Record the last exit code / terminal kind per sandbox so `collect_execution_evidence` reports the truth, exactly as `RemoteMachineProvider` does.
- Every lifecycle call and every refusal emits an audit event through `event_sink`.

- [ ] **Step 4: Run tests** → PASS
- [ ] **Step 5: Commit** — `feat(#2023): OpenSandboxProvider lifecycle over the #2022 contract`

---

### Task 7: Reconciliation and registry wiring

**Files:**
- Modify: `app/modules/workspace/autonomous/sandbox/registry.py`
- Test: `tests/unit/test_opensandbox_reconcile.py`

- [ ] **Step 1: Write the failing tests**

Required test #6 by name, plus registry behaviour:

```python
def test_node_and_control_plane_restart_reconcile_sandbox():
    # After a restart the per-call provider instance and its handle map are gone;
    # only the persisted sandbox_id remains. destroy_attribution must still work.
    provider = OpenSandboxProvider(cfg, api_factory=lambda ep: api)
    provider.destroy_attribution("sb-1", None)
    assert "sb-1" in api.deleted

    # It must never raise — the sweep walks many rows.
    provider.destroy_attribution("sb-unknown", None)

    # The orphan sweep destroys sandboxes the control plane no longer claims.
    orphans = provider.reconcile_orphans(live_sandbox_ids={"sb-2"})
    assert orphans == ["sb-3"]


def test_registry_resolves_opensandbox_name():
def test_registry_raises_when_opensandbox_config_absent():
    # Fail closed on a missing config rather than handing back a weaker provider.
def test_registry_still_rejects_unknown_provider_names():
```

- [ ] **Step 2: Run tests to verify they fail**

- [ ] **Step 3: Implement**

Add to `provider.py`:

```python
def reconcile_orphans(self, live_sandbox_ids: Collection[str]) -> list[str]: ...
```

which lists sandboxes carrying `metadata["openace.provider"] == "opensandbox"`,
destroys any id absent from `live_sandbox_ids`, and returns the destroyed ids.
Best-effort per row: one failure never aborts the sweep.

In `registry.py`, extend `provider_for` with an `"opensandbox"` branch that loads
the backend config and raises `SandboxError` when it is absent. Keep the existing
final `raise SandboxError(f"unknown sandbox_provider: {name!r}")` untouched so
unknown names stay fail-closed.

- [ ] **Step 4: Run tests** → PASS
- [ ] **Step 5: Commit** — `feat(#2023): orphan reconciliation and registry resolution`

---

### Task 8: No-silent-downgrade isolation gate

**Files:**
- Create: `app/modules/workspace/autonomous/sandbox/isolation_tier.py`
- Test: `tests/unit/test_opensandbox_isolation_tier.py`

- [ ] **Step 1: Write the failing test**

Required test #7 by name:

```python
def test_required_production_policy_cannot_fallback_to_legacy():
    cfg = parse_backend_config(_raw())
    # A tenant marked as requiring production isolation resolves to the
    # OpenSandbox backend...
    provider = select_provider(tenant="acme", requires_production_isolation=True, config=cfg)
    assert isinstance(provider, OpenSandboxProvider)

    # ...and when no backend is configured it RAISES rather than degrading.
    with pytest.raises(SandboxError):
        select_provider(tenant="acme", requires_production_isolation=True, config=None)

    # Under no circumstances does a required-isolation tenant get Legacy.
    with pytest.raises(SandboxError):
        select_provider(tenant="acme", requires_production_isolation=True,
                        config=parse_backend_config(_raw(endpoints={})))


def test_non_required_tenant_still_gets_legacy_when_unconfigured():
    provider = select_provider(tenant="dev", requires_production_isolation=False, config=None)
    assert isinstance(provider, LegacyPosixProvider)
```

- [ ] **Step 2: Run tests to verify they fail**

- [ ] **Step 3: Implement `isolation_tier.py`**

```python
def requires_production_isolation(tenant: str | None, config: SandboxBackendConfig | None) -> bool: ...
def select_provider(*, tenant: str | None, requires_production_isolation: bool,
                    config: SandboxBackendConfig | None,
                    project_path: str | None = None) -> SandboxProvider: ...
```

`select_provider` raises `SandboxError` whenever production isolation is required
and an OpenSandbox provider cannot be constructed. It returns `LegacyPosixProvider`
only when production isolation is **not** required. There is no code path from
"required" to Legacy.

- [ ] **Step 4: Run tests** → PASS
- [ ] **Step 5: Commit** — `feat(#2023): production-isolation gate refuses Legacy downgrade`

---

### Task 9: Kubernetes manifests

**Files:**
- Create: `k8s/extras/opensandbox/runtimeclasses.yaml`, `server-gvisor.yaml`, `server-kata.yaml`, `configmap-gvisor.yaml`, `configmap-kata.yaml`, `networkpolicy.yaml`, `rbac.yaml`, `image-policy.yaml`, `kustomization.yaml`, `README.md`

- [ ] **Step 1: Write the manifests**

- `runtimeclasses.yaml`: `RuntimeClass` `gvisor` (`handler: runsc`) and `kata-qemu` (`handler: kata-qemu`).
- `configmap-*.yaml`: the upstream `sandbox.toml` for each tier, with
  `[runtime] type = "kubernetes"` and `[secure_runtime] type/k8s_runtime_class`
  set to that tier's runtime, plus `[egress] mode = "dns+nft"`.
- `server-*.yaml`: `Deployment` + `Service` per tier, non-root, read-only rootfs,
  dropped capabilities, `allowPrivilegeEscalation: false`, seccomp
  `RuntimeDefault`.
- `networkpolicy.yaml`: sandbox namespace egress denies `169.254.169.254/32` and
  RFC1918 ranges, allows DNS and the LLM proxy — the cluster-layer defence that
  holds even if the sidecar is misconfigured.
- `rbac.yaml`: per-sandbox `ServiceAccount` with no API permissions.
- `image-policy.yaml`: the admission policy enforcing cosign signature +
  provenance against `image_signer_identity`, which spec §6.3 assigns to
  admission rather than to the Python provider. Ship it with a comment stating
  that the provider enforces only allowlist membership and digest pinning, so
  the split of responsibility is legible at the point of deployment.
- `README.md`: how to apply, and the explicit note that gVisor/Kata selection is
  server-level so each tier needs its own Deployment + ConfigMap.

- [ ] **Step 2: Validate YAML**

Run: `python -c "import yaml,glob,sys; [list(yaml.safe_load_all(open(f))) for f in glob.glob('k8s/extras/opensandbox/*.yaml')]; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit** — `feat(#2023): Kubernetes manifests for gVisor and Kata OpenSandbox tiers`

---

### Task 10: Operator guide and backend comparison report

**Files:**
- Create: `docs/sandbox-backends.md`

- [ ] **Step 1: Write the guide**

Sections: architecture; installing `runsc` + `containerd-shim-runsc-v1`; Kata
prerequisites (KVM, kernel ≥5.10); configuring `sandbox-backends.json`; API-key
management; rollout by tenant/project; the fail-closed refusal catalogue with
each reason code and what to do about it; troubleshooting.

Plus the acceptance-required comparison table across Legacy / gVisor / Kata for
cold start, sandbox create, workspace transfer, exec, collect changes and
destroy. **Every number carries its provenance** — upstream-published figures
are cited as upstream; anything we measured is labelled with how. No unlabelled
numbers.

Finally, a prominent limitation section: this backend is not yet on the agent
execution path, because the CLI's interactive `--input-format stream-json` stdin
has no execd equivalent (spec §9). Link the follow-up issue.

- [ ] **Step 2: Commit** — `docs(#2023): OpenSandbox operator guide and backend comparison`

---

### Task 11: Full verification

- [ ] **Step 1: Run the new suite**

Run: `python -m pytest tests/unit/test_opensandbox_*.py tests/unit/test_sandbox_*.py -q`
Expected: all pass, zero errors.

- [ ] **Step 2: Confirm no regression in the sandbox contract suite**

Run: `python -m pytest tests/unit -k sandbox -q`
Expected: all pass.

- [ ] **Step 3: Run the pinned lint chain (CI parity — bare black/ruff is NOT equivalent)**

Run: `pre-commit run --files $(git diff --name-only origin/main...HEAD | tr '\n' ' ')`
Expected: all hooks pass or autofix cleanly; re-stage and re-run after autofix.

- [ ] **Step 4: Open the PR**

Push via `scripts/push.sh` (never bare `git push` — see `CLAUDE.md`), then open
the PR with a body that states the §9 limitation explicitly.
