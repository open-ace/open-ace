# #2023 — OpenSandbox / gVisor–Kata production sandbox backend

Date: 2026-08-28
Issue: https://github.com/open-ace/open-ace/issues/2023
Status: approved design

## 1. Problem

`#2022` froze the `SandboxProvider` contract and shipped two backends:
`LegacyPosixProvider` (local POSIX, four isolation capabilities) and
`RemoteMachineProvider` (remote agent, **zero** declared capabilities). Neither
provides namespace isolation or egress control, so
`SandboxCapability.NAMESPACE_ISOLATION`, `NETWORK_EGRESS_POLICY` and
`STORAGE_INODE_QUOTA` exist in the taxonomy with no provider able to satisfy
them, and `SandboxSpec.network_egress` / `runtime` / `volumes` are carried but
never honored. `registry.provider_for()` raises `SandboxError` for every name
outside `{"", "legacy_posix", "remote_machine"}`.

`#2023` adds the production backend that closes those gaps.

## 2. Upstream: what OpenSandbox actually provides

OpenSandbox (`opensandbox-group/OpenSandbox`, Apache-2.0, Go, CNCF landscape)
is a sandbox runtime for AI agents with Docker and Kubernetes runtimes and
secure-container support for gVisor, Kata and Firecracker. It exposes two REST
surfaces, both specified as OpenAPI in the upstream `specs/` directory.

### 2.1 Lifecycle API — server, base path `/v1`

Auth: header `OPEN-SANDBOX-API-KEY`.

| Method + path | Purpose |
| --- | --- |
| `POST /sandboxes` | create (`image`\|`snapshotId`, `entrypoint`, `resourceLimits`, `resourceRequests`, `env`, `metadata`, `timeout`, `networkPolicy`, `volumes`, `extensions`) → `202` with `id` + `status` |
| `GET /sandboxes/{id}` | full sandbox incl. `status.state` |
| `GET /sandboxes` | list, filterable — the reconciliation sweep |
| `DELETE /sandboxes/{id}` | destroy |
| `POST /sandboxes/{id}/pause` / `/resume` | pause / resume |
| `POST /sandboxes/{id}/renew-expiration` | extend TTL |
| `GET /sandboxes/{id}/endpoints/{port}` | public URL (+ required headers) to reach a port inside the sandbox |
| `PATCH /sandboxes/{id}/metadata` | metadata patch |

`SandboxState`: `Pending`, `Running`, `Pausing`, `Paused`, `Resuming`,
`Stopping`, `Terminated`, `Failed`.

`ResourceLimits` is an open `{string: string}` map (`cpu`, `memory`, `gpu`, and
on the Kubernetes runtime any other Kubernetes resource name, notably
`ephemeral-storage`).

`NetworkPolicy` is `{defaultAction: allow|deny, egress: [{action, target}]}`.
`NetworkRule.target` is documented as **"FQDN or wildcard domain … IP/CIDR not
yet supported in the egress MVP"**.

`extensions.poolRef` selects a pre-warmed Pool. Upstream **rejects** `image`,
`snapshotId`, `networkPolicy`, `platform`, `volumes` and
`credentialProxy.enabled` when `poolRef` is set, because pooled pods are
pre-created and the requested policy could not be honored.

### 2.2 Execd API — inside the sandbox, default port `44772`

Auth: bearer access token. Relevant subset:

| Method + path | Purpose |
| --- | --- |
| `POST /command` | run a shell command; returns a **SSE** stream (`init`/`stdout`/`stderr`/`status`/`error`/`result`/`execution_complete`/`ping`). Request carries `command`, `cwd`, `background`, `timeout` (ms), `uid`, `gid`, `envs`. |
| `GET /command/status/{id}` | `{id, running, exit_code, error, started_at, finished_at}` |
| `GET /command/{id}/logs` | plain-text logs for background commands, incremental via `cursor` + `EXECD-COMMANDS-TAIL-CURSOR` header |
| `DELETE /command?id=` | interrupt |
| `POST /files/upload` | multipart: JSON `metadata` part (`path`, `owner`, `group`, `mode`) then the file part |
| `GET /files/download?path=` | download (byte-range or line-based) |
| `GET /files/info`, `GET /directories/list`, `GET /files/search` | manifest construction |

There is **no interactive stdin**. This is load-bearing for §9.

### 2.3 Secure runtime is server-level, not per-request

From the upstream secure-container guide:

```toml
[runtime]
type = "kubernetes"
[secure_runtime]
type = "gvisor"            # "", "gvisor", "kata", "firecracker"
k8s_runtime_class = "gvisor"   # or "kata-qemu", "kata-fc"
```

> **Server-Level Configuration**: The secure runtime is configured once at the
> server level by administrators. All sandboxes on that server transparently use
> the configured runtime. SDK users and API callers require **no code changes**.

Consequence: a caller **cannot** ask for gVisor on one request and Kata on the
next against the same server. The issue's "default gVisor, high-security tenant
may choose Kata" therefore has to be **routing between separately configured
endpoints**, not a request field. This is §4.

## 3. Module layout

```
app/modules/workspace/autonomous/sandbox/opensandbox/
    __init__.py       re-exports OpenSandboxProvider + config entry points
    config.py         SandboxBackendConfig / EndpointConfig, loading, validation
    client.py         OpenSandboxApi Protocol + HttpOpenSandboxApi (thin REST)
    policy.py         SandboxSpec + AgentTaskPolicy -> CreateSandboxRequest
    workspace.py      snapshot build, ChangeSet manifest, control-plane validation
    provider.py       OpenSandboxProvider
    fake_server.py    in-memory fake of both APIs (test double)
app/modules/workspace/autonomous/sandbox/
    isolation_tier.py required-isolation gate (no silent Legacy downgrade)
    registry.py       (edit) resolve "opensandbox"
```

No new pip dependency. `client.py` uses `requests` directly rather than
`app.utils.outbound_url_guard.safe_request`, because the OpenSandbox endpoint is
an in-cluster/private address which `safe_request` blocks by design
(`_is_public_address`). Per `CLAUDE.md` 出站 HTTP 请求规范 rule 2 this is the
documented-exception path: every call passes
`proxies={"http": None, "https": None}` and carries a comment stating the
reason. The base URL originates from **operator configuration only** and is
never derived from user input, so there is no SSRF surface to guard.

## 4. Configuration — isolation tier to endpoint routing

Loaded from the first existing path of
`OPENACE_SANDBOX_BACKENDS` -> `/etc/openace/sandbox-backends.json` ->
`~/.open-ace/sandbox-backends.json`, mirroring the precedence helper
`task_isolation.candidate_agent_task_policy_paths`.

```json
{
  "default_tier": "gvisor",
  "endpoints": {
    "gvisor": {
      "base_url": "http://opensandbox.open-ace.svc.cluster.local:8080/v1",
      "api_key_env": "OPENSANDBOX_API_KEY_GVISOR",
      "runtime_class": "gvisor",
      "egress_enforced": true,
      "ephemeral_storage_enforced": true,
      "execd_port": 44772,
      "exec_uid": 1000,
      "exec_gid": 1000,
      "pool_ref": "",
      "pool_egress_preapplied": false
    },
    "kata": {
      "base_url": "http://opensandbox-kata.open-ace.svc.cluster.local:8080/v1",
      "api_key_env": "OPENSANDBOX_API_KEY_KATA",
      "runtime_class": "kata-qemu",
      "egress_enforced": true,
      "ephemeral_storage_enforced": true,
      "exec_uid": 1000,
      "exec_gid": 1000,
      "pool_ref": "",
      "pool_egress_preapplied": false
    }
  },
  "tenant_tiers": {"acme-corp": "kata"},
  "project_tiers": {"/srv/repos/high-sec": "kata"},
  "image_allowlist": ["ghcr.io/open-ace/agent@sha256:0f3c...e91a"],
  "image_signer_identity": "https://github.com/open-ace/open-ace/.github/workflows/release.yml@refs/heads/main",
  "egress_allow_hosts": ["api.anthropic.com", "*.githubusercontent.com"],
  "resource_defaults": {"cpu": "2", "memory": "4Gi", "ephemeral-storage": "8Gi"},
  "sandbox_ttl_seconds": 3600,
  "changeset_limits": {"max_files": 2000, "max_file_bytes": 10485760, "max_total_bytes": 104857600}
}
```

Rules:

- `runtime_class` is a **declaration of what the operator configured on that
  server**, not a request field. It is recorded in the effective-policy snapshot
  and in audit events so a run can be attributed to a runtime after the fact.
- API keys are read from the named environment variable, never stored in the
  JSON.
- Resolution order for a task's tier: explicit spec/tier override ->
  `project_tiers` -> `tenant_tiers` -> `default_tier`.
- A tier that resolves to a missing/unconfigured endpoint raises
  `SandboxError` at provider construction. It never falls back to another tier.
  This is the acceptance item "production required policy must not silently
  fall back".
- Malformed config raises rather than degrading to defaults.

## 5. Capability declaration derived from config

`#2082` taught that a provider copying another provider's capability set is a
fail-closed violation (`RemoteMachineProvider` declared four capabilities it did
not enforce; the fix was `_REMOTE_CAPS = frozenset()`). So
`OpenSandboxProvider.capabilities()` is **computed from the resolved endpoint
config**, not a module constant.

| Capability | Declared when | Enforcement |
| --- | --- | --- |
| `NAMESPACE_ISOLATION` | always | separate container + the server's gVisor/Kata runtime class |
| `CREDENTIAL_TOKEN_BINDING` | always | `env` is constructed from an explicit allowlist; the control plane's environment is never inherited; no GitHub write credential is ever placed in the sandbox |
| `PRIVATE_HOME_TMP_XDG` | always | one fresh container per sandbox; `HOME`, `TMPDIR`, `XDG_CACHE_HOME`, `XDG_CONFIG_HOME`, `XDG_DATA_HOME` set explicitly into the sandbox tree |
| `FILESYSTEM_ACL` | always | container filesystem namespace, non-root uid/gid on every exec, zero host mounts |
| `NETWORK_EGRESS_POLICY` | `egress_enforced` | egress sidecar with `defaultAction: deny` plus the host allowlist |
| `CPU_MEM_PIDS_TIME_QUOTA` | always | `cpu`/`memory` via `resourceLimits`; **pids via a `ulimit -u` prefix** on the exec command; wall clock via `RunCommandRequest.timeout` and the sandbox `timeout` TTL |
| `STORAGE_INODE_QUOTA` | `ephemeral_storage_enforced` | `resourceLimits["ephemeral-storage"]` plus `ulimit -f`; file **count** is enforced control-plane side during ChangeSet validation |

Capability-realism probes in the test suite assert each declared capability
corresponds to an observable request field or command wrapper, so a future edit
cannot re-introduce the `#2082` failure silently.

### 5.1 Fail-closed refusals

Raised from `create()` (all `SandboxError`/`CapabilityUnsupported` subclasses):

1. `spec.network_egress.allow_cidrs` non-empty — upstream cannot express
   IP/CIDR egress rules. Dropping them silently would allow a spec that *looks*
   restrictive to run wide open.
2. `spec.network_egress.mode == "unrestricted"` — the issue mandates
   default-deny egress.
3. Any `VolumeSpec` with a host-path backend — the issue forbids exposing the
   trusted Git common-dir writable to the sandbox.
4. `spec.runtime.image` not in `image_allowlist`, or not digest-pinned.
5. Warm pool requested while the tier's pool template does not already carry an
   egress policy (see §8).

## 6. Lifecycle mapping

| `SandboxProvider` method | OpenSandbox calls |
| --- | --- |
| `create` | `POST /v1/sandboxes` with digest-pinned allowlisted image, `entrypoint` (long-lived supervisor shell), translated `resourceLimits`, `networkPolicy`, `timeout` = TTL, `env` allowlist, `metadata` = `{openace.task_id, openace.tenant, openace.generation, openace.provider}` |
| `upload_workspace` | resolve execd endpoint via `GET /v1/sandboxes/{id}/endpoints/44772`, then `POST /files/upload` per file with explicit `mode` |
| `exec` | `POST :44772/command` `{command: "ulimit -u N -f M; exec <cmd>", cwd, background: true, timeout_ms, uid, gid, envs}`. `uid`/`gid` come from the endpoint config (`exec_uid`/`exec_gid`, default `1000`); the provider refuses to exec with `uid == 0`. |
| `stream` | consume the SSE stream; map `stdout`/`stderr`/`status`/`error`/`execution_complete` to `SandboxEventKind`; fall back to `GET /command/status/{id}` + `/command/{id}/logs?cursor=` polling if the stream drops |
| `pause` / `resume` | `POST /v1/sandboxes/{id}/pause` / `/resume`, then poll `GET /v1/sandboxes/{id}` until `Paused` / `Running` |
| `stop` | `DELETE :44772/command?id=<command_id>` |
| `inspect` | `GET /v1/sandboxes/{id}`; `status.state` mapped to `SandboxStatus`; `404` -> `DESTROYED` |
| `collect_changes` | §7 |
| `collect_execution_evidence` | §7.3 |
| `destroy` | `DELETE /v1/sandboxes/{id}`; `404` treated as success (idempotent) |
| `destroy_attribution` | `DELETE /v1/sandboxes/{sandbox_id}` using only the persisted id; best-effort, never raises |
| orphan reconcile | `GET /v1/sandboxes` filtered on `metadata.openace.provider`; any sandbox the workflow table does not claim as live is destroyed; long-running claimed sandboxes get `renew-expiration` |

`SandboxState` mapping: `Pending` -> `CREATED`; `Running`/`Resuming` ->
`RUNNING`; `Pausing`/`Paused` -> `PAUSED`; `Stopping` -> `STOPPED`;
`Terminated` -> `DESTROYED`; `Failed` -> `ERROR`.

### 6.1 Generation handling

`SandboxHandle.generation` is written into `metadata["openace.generation"]` at
creation. Every lifecycle call re-reads it via `inspect` and refuses to operate
when the handle's generation does not match, reusing
`provider.is_current_generation`. A handle minted before a reconciliation bump
cannot act on the new sandbox.

### 6.2 Audit events and effective-policy snapshot

Every lifecycle transition the provider performs (`create`, `upload_workspace`,
`exec`, `stop`, `pause`, `resume`, `destroy`, `destroy_attribution`, reconcile
sweep, and each fail-closed refusal with its reason code) emits a structured
audit event through the existing autonomous event emitter, carrying
`sandbox_id`, `generation`, `tenant`, `tier`, `runtime_class` and the outcome.
Refusals are audited as loudly as successes — a silently refused sandbox is
indistinguishable from a missing one otherwise.

`provider_name` is `"opensandbox"`, so the existing
`effective_policy.build_effective_policy` snapshot (`#2020` Phase B) records the
declared capability set and the derived `enforced` map on the workflow row with
no change to that module. `registry.provider_for("opensandbox")` constructs the
provider from the loaded config; when no config is present it raises
`SandboxError` rather than returning a weaker provider.

### 6.3 Image trust

In-process the provider enforces two things: the image must be in
`image_allowlist`, and it must be **digest-pinned** (`@sha256:...`); a tag-only
reference is refused. Cosign signature verification and SBOM attestation are
*not* re-implemented here — upstream publishes keylessly-signed images with
provenance, and verification belongs at admission time. The config records the
expected signer identity (`image_signer_identity`) and
`k8s/extras/opensandbox/` ships the admission policy that enforces it, with the
operator guide documenting the verification step. The spec is explicit about
this split so nobody reads "image allowlist, signature and SBOM" as fully
enforced in Python when only the allowlist and digest pinning are.

### 6.4 Metadata, private CIDR and DNS rebinding

Egress defence is layered and none of the layers is ours alone to claim:

1. `networkPolicy.defaultAction: deny` plus a host allowlist that never contains
   the cloud metadata address or a private range (the provider rejects such an
   entry in config validation, not just at request time).
2. The upstream egress sidecar in `dns+nft` mode resolves and pins allowlisted
   names, which is what closes the DNS-rebinding window; `credentialProxy` is
   the upstream feature that depends on it.
3. A Kubernetes `NetworkPolicy` on the sandbox namespace, shipped in
   `k8s/extras/opensandbox/`, blocking the metadata address and private CIDRs at
   the cluster layer regardless of sidecar state.

Layer 2 and 3 are operator-configured; the provider *verifies* layer 1 and
refuses to run when the endpoint config claims `egress_enforced` without an
allowlist. It does not claim to enforce 2 and 3 itself.

## 7. Workspace transfer and ChangeSet

The pipeline from the issue, unchanged:

```
control plane prepares trusted base/worktree
  -> upload credential-free snapshot
  -> agent edits inside the sandbox
  -> supervisor produces manifest / ChangeSet
  -> control plane validates path/size/count/mode/symlink/secret
  -> apply to trusted worktree
  -> GitHubOps commit/push (control plane only)
```

### 7.1 Snapshot upload

`workspace.build_snapshot(worktree_path)` walks the trusted worktree and yields
`(relative_path, bytes, mode)` entries, **excluding**: `.git/` (the Git
common-dir is never exposed), anything matching the credential exclusion set
(`.git-credentials`, `.netrc`, `**/.ssh/**`, `.env*`, `gh` config, the
`GH_CONFIG_DIR` tree), and paths above the worktree root. Uploaded via
`POST /files/upload` with an explicit non-executable default mode.

Excluding `.git/` means the agent sees a working tree, not a repository, and the
ChangeSet is a file manifest rather than a git diff. That is deliberate: it is
the "credential-free snapshot" branch of the issue's pipeline, and it removes
the possibility of a sandbox reaching the trusted common-dir or a stored
credential helper. The issue's alternative branch — a controlled read-only clone
— is left to the follow-up issue in §9, where an agent that needs `git log`
inside the sandbox actually has a use for it.

No GitHub write credential is ever uploaded; commit and push stay control-plane
side through the existing `GitHubOps`.

### 7.2 ChangeSet manifest and validation

The in-sandbox supervisor emits a JSON manifest (path, mode, size, sha256,
symlink target) which the provider downloads. `workspace.validate_changeset`
runs **entirely control-plane side, before any write touches the trusted
worktree**, and rejects:

- absolute paths, and any path containing a `..` component
- paths resolving outside the worktree root after normalization
- symlinks whose target escapes the worktree root
- files above `max_file_bytes`, or a total above `max_total_bytes`
- entry count above `max_files`
- non-regular, setuid, setgid or sticky modes
- entries matching the secret-pattern set

Validation is all-or-nothing: the full manifest is validated first, and only a
fully clean manifest is applied. A partial apply is impossible. Rejections carry
a structured reason code.

### 7.3 Structured execution evidence

`collect_execution_evidence` maps `GET /command/status/{id}` onto
`CommandExecutionEvidence` from `#2046-A`, filling `command_id`, `sandbox_id`,
`sandbox_generation`, `cwd`, `exit_code` and `terminal_reason`. Mapping:

| Observation | `TerminalReason` |
| --- | --- |
| `running: false`, `exit_code` present | `COMPLETED` (exit code stays authoritative) |
| SSE terminal event was `COMMAND_TIMED_OUT`, or execd reports the timeout kill | `TIMEOUT` |
| exit code encodes a signal (`128+n`), or OOM/pids kill observed | `SIGNAL` |
| provider-initiated stop | `CANCELLED` |
| terminal without a usable exit code | `CRASH` |
| no status row for the command | `MISSING_RESULT` |

A resource-limit kill therefore never surfaces as `COMPLETED`. This mirrors the
`#2078 P1#3` discipline already applied in `RemoteMachineProvider`.

## 8. Warm pool

Warm pool uses `extensions.poolRef`. Because upstream rejects `networkPolicy`,
`volumes` and `snapshotId` alongside `poolRef`, a pooled sandbox cannot carry a
per-task egress policy. The provider therefore permits pool mode **only** when
the endpoint config marks the pool template as already carrying the tier's
egress policy (`pool_egress_preapplied: true`); otherwise it refuses rather than
running a task with a weaker network policy than requested.

Tenant-state hygiene: nothing tenant-scoped is ever reused across allocations —
no secret, no `HOME` tree, no workspace content, no proxy token, no evidence
state. Each allocation gets a freshly constructed `env`, a fresh uploaded
workspace, and a fresh evidence namespace keyed by the new `sandbox_id`. The
provider holds no cross-allocation mutable tenant state.

## 9. Not in scope — follow-up issue

Running the coding-agent CLI itself inside the sandbox is **not** part of this
change.

`_run_local` drives the CLI with `--input-format stream-json` over an
interactive stdin and reads the raw `Popen` through the Legacy-only escape
hatches `get_process()` / `build_launch_argv()`, which are deliberately not on
the `SandboxProvider` Protocol. `agent_runner.py:2669` already records this:
reusing that path from a container backend "requires abstracting the IO into a
provider-returned transport handle (the 'replaceable local seam')". Execd offers
SSE plus discrete commands and no interactive stdin, so bridging it needs an
in-sandbox supervisor image and a new transport protocol — a change to the
production hot path of every local autonomous workflow.

That work gets its own issue. This change delivers the backend, its policy
translation, its validation machinery and its selection gate; the limitation is
stated in the PR body and in `docs/sandbox-backends.md` so the backend is not
mistaken for being live on the agent execution path.

Also out of scope, per the issue's own exclusion list: the `SandboxProvider`
contract (`#2022`), Legacy minimal hardening (`#2020-A`), Git worktree/branch
cleanup (`#2043`), verify-before-act (`#2045`), test parser and verdict
(`#2046`), transcript semantics (`#2047`), phase handler split (`#2044`).

## 10. Tests

Canonical layers per `docs/TEST_LAYERS.md`, marked `pytest.mark.regression` and
`pytest.mark.issue(2023)`, driven by `fake_server.py` so they run in the SQLite
`test(3.x)` CI lane with no cluster.

The eight tests the issue requires:

| Test | Asserts |
| --- | --- |
| `test_sandbox_cannot_read_host_or_peer_workspace` | no host-path volume is ever sent; a host-backed `VolumeSpec` is refused; `.git` and credential paths are excluded from the snapshot |
| `test_default_egress_blocks_metadata_private_cidr_and_unknown_domain` | generated `networkPolicy` is `defaultAction: deny`; metadata IP, private CIDR and unlisted domains are absent from the allowlist; `allow_cidrs` is refused |
| `test_changeset_rejects_absolute_path_symlink_escape_and_oversize_file` | each rejection class returns a structured reason and applies nothing |
| `test_resource_limits_return_structured_terminal_reason` | OOM / pids / timeout kills map to `SIGNAL` / `TIMEOUT`, never `COMPLETED` |
| `test_warm_pool_does_not_reuse_tenant_state` | a second pool allocation sees no prior env, workspace, token or evidence; pool without pre-applied egress is refused |
| `test_node_and_control_plane_restart_reconcile_sandbox` | `destroy_attribution` destroys by persisted id with no live handle; the sweep destroys unclaimed sandboxes and never raises |
| `test_required_production_policy_cannot_fallback_to_legacy` | a required-isolation tenant resolves to `OpenSandboxProvider` or raises; it never yields `LegacyPosixProvider` |
| `test_execution_evidence_matches_provider_contract` | evidence rows carry `sandbox_id`, `sandbox_generation`, real `exit_code` and a correct `terminal_reason` |

Plus: `#2022` contract-conformance reuse against `OpenSandboxProvider`,
capability-realism probes (§5), config loading and fail-closed validation,
lifecycle state mapping, generation rejection, and SSE parsing including a
mid-stream drop falling back to status polling.

## 11. Deployment and documentation

- `k8s/extras/opensandbox/`: `RuntimeClass` for `gvisor` and `kata-qemu`,
  OpenSandbox server `Deployment` + `Service`, `sandbox.toml` `ConfigMap` for
  each tier, `NetworkPolicy` restricting sandbox namespace egress, per-sandbox
  `ServiceAccount` + minimal RBAC.
- `docs/sandbox-backends.md`: operator guide (installing runsc and
  `containerd-shim-runsc-v1`, Kata prerequisites, tier configuration, key
  management, rollout by tenant/project) and the Legacy / gVisor / Kata
  performance, cost and compatibility report the acceptance criteria require —
  cold start, sandbox create, workspace transfer, exec and destroy, using the
  upstream published overhead figures plus our own measured numbers where a
  cluster is available, with each number's provenance labelled.
