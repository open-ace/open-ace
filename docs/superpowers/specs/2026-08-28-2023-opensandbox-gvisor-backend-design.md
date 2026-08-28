# #2023 — OpenSandbox / gVisor–Kata production sandbox backend

Date: 2026-08-28
Issue: https://github.com/open-ace/open-ace/issues/2023
Status: approved design (revision 4 — after three independent review rounds)

## 0. Revision note

Revision 1 asserted that OpenSandbox's execd has no interactive stdin, and
deferred wiring the agent onto the backend on that basis. **That was wrong.**
`components/execd/pkg/web/router.go:87-92` registers a PTY route group absent
from `execd-api.yaml`, and `components/execd/PTY.md` documents a pipe mode
(`?pty=0`) that is exactly a bidirectional stdin/stdout/stderr transport. The
scope was re-taken with correct facts: the transport is now in scope and the
backend goes live on the agent path. Every claim below has been checked against
upstream source, not only against the published OpenAPI files.

## 1. Problem

`#2022` froze the `SandboxProvider` contract and shipped two backends:
`LegacyPosixProvider` (local POSIX, four capabilities) and
`RemoteMachineProvider` (remote agent, **zero** declared capabilities — the
`#2082` fix). Neither provides namespace isolation or egress control, so
`NAMESPACE_ISOLATION`, `NETWORK_EGRESS_POLICY` and `STORAGE_INODE_QUOTA` exist
in the taxonomy with no provider able to satisfy them, and
`SandboxSpec.network_egress` / `runtime` / `volumes` are carried but never
honored. `registry.provider_for()` raises for every name outside
`{"", "legacy_posix", "remote_machine"}`.

## 2. Upstream: verified facts

OpenSandbox (`opensandbox-group/OpenSandbox`, Apache-2.0, Go, CNCF landscape).

### 2.1 Lifecycle API — server, base path `/v1`, header `OPEN-SANDBOX-API-KEY`

`POST /sandboxes` (→`202`, `status.state: "Running"` — provisioning is
synchronous), `GET|DELETE /sandboxes/{id}` (`DELETE`→`204`, then `Stopping` →
`Terminated`), `GET /sandboxes` (**paginated**, `page` from 1, `pageSize`
default **20**), `POST /sandboxes/{id}/pause|resume` (async: `Pausing`→`Paused`),
`POST /sandboxes/{id}/renew-expiration`, `GET /sandboxes/{id}/endpoints/{port}`,
`PATCH /sandboxes/{id}/metadata`.

`SandboxState`: `Pending`, `Running`, `Pausing`, `Paused`, `Resuming`,
`Stopping`, `Terminated`, `Failed`.

`image` is an object `{uri, auth?}`, not a string. `metadata` values must be
**strings**. `resourceLimits` is an open `{string: string}` map passed through to
Kubernetes limits unchanged except for `gpu`
(`server/.../k8s/provider_common.py::_translate_resource_limits_for_k8s`), so
`ephemeral-storage` reaches the pod. `timeout` is seconds, `minimum: 60`, capped
by the server's `max_sandbox_timeout_seconds`.

`networkPolicy` = `{defaultAction: allow|deny, egress: [{action, target}]}`.
`NetworkRule.target` is documented **"FQDN or wildcard domain … IP/CIDR not yet
supported in the egress MVP"**.

`extensions.poolRef` selects a pre-warmed Pool and **rejects** `image`,
`snapshotId`, `networkPolicy`, `platform`, `volumes`, `lifecycle` and
`credentialProxy.enabled` alongside it — pooled pods are pre-created.

`secureAccess` defaults to **`false`**, and its own description says that when
omitted "endpoints remain accessible **without the additional access token**".

### 2.2 Execd API — inside the sandbox, port `44772`

Auth: **`X-EXECD-ACCESS-TOKEN`** header (apiKey, not Bearer).
`router.go:153-155`: the middleware **short-circuits when the configured token
is empty**, i.e. no auth at all by default.

Documented in `execd-api.yaml`: `POST /command` (SSE),
`GET /command/status/{id}`, `GET /command/{id}/logs`, `DELETE /command?id=`,
`POST /files/upload` (multipart: JSON `metadata` part then file part),
`GET /files/download`, `GET /files/info`, `GET /directories/list`.

**Not in the OpenAPI file** but registered in `router.go:87-92`:

```
POST   /pty                  -> {"session_id": ...}   body {cwd?, command?}
GET    /pty/:sessionId       -> {session_id, running, output_offset}
DELETE /pty/:sessionId
GET    /pty/:sessionId/ws    -> WebSocket
```

### 2.3 Command execution semantics (verified in `runtime/command.go`)

- Both paths do `cmd.Env = mergeEnvs(os.Environ(), extraEnv)` (`:184`, `:347`)
  — every command inherits **execd's own process environment**.
- `uid`/`gid` are caller-supplied (`minimum: 0`) and applied via
  `buildCredential` → `syscall.Credential` (`:105`, `:173`, `:335`).
- **Foreground** (`background: false`) streams `stdout`/`stderr` as separate SSE
  events. On a non-zero exit it emits an SSE **`error`** event with
  `ename: "CommandExecError"` and `evalue: "<exit code>"` and **no**
  `execution_complete` (`:267-300`).
- **Background** (`background: true`) is unusable for our purposes (`:304-403`):
  `OnExecuteComplete` fires immediately after launch (the `Wait` is in a detached
  goroutine); stdout and stderr share one `combinedOutputDescriptor` so they
  cannot be separated; `cmd.Stdin` is `/dev/null`; no stdout/stderr SSE events
  are emitted at all.

### 2.4 PTY pipe-mode transport (verified in `PTY.md`, `runtime/pty_session.go`)

`ws://<execd>/pty/<session_id>/ws?pty=0`. Binary frames: the holder **sends
stdin as `0x00` + raw bytes** and receives `0x01` + bytes (stdout) or `0x02` +
bytes (stderr — pipe mode only). On shell exit the server sends a JSON `exit`
frame carrying `exit_code`, then closes. JSON text frames carry `resize`,
`signal` (e.g. `{"type":"signal","signal":"SIGINT"}`) and `ping`. A second
read/write connection gets HTTP 409 before the upgrade unless `?takeover=1`.

Four further facts, each of which changes the design:

- `CreatePTYSessionRequest` is **only** `{cwd, command}` — no `envs`, no
  `uid`/`gid`. But `command` is real: `buildPTYCommand` (`pty_session.go:242-248`)
  runs `bash -c "<command>"` when it is non-empty. That is the env-delivery
  mechanism (§6.2).
- `pty_session.go:265,333` sets `cmd.Env = os.Environ()` with **no** extra-env
  merge, and pipe mode's `SysProcAttr` is `{Setpgid: true}` with **no
  `Credential`** — so the agent's uid is whatever the container runs as,
  settable only by the pod `securityContext` / image `USER`.
- **Attaching starts a shell.** `pty_ws.go:139-152`: `if !session.IsRunning() {
  session.StartPipe() }`, and `IsRunning()` is `s.pid != 0` with `s.pid = 0` set
  on exit. Re-opening the socket on a finished session therefore **launches a
  second agent process**, not a resumed view of the first.
- **Replay is a third frame kind, and it is channel-merged.** `model/pty_ws.go`
  defines `BinReplay = 0x03` as `[8-byte BE offset][raw bytes]`, and
  `pty_ws.go:236-239` sends it to the *holder*, not only to viewers. It comes
  from a single `replayBuffer` shared by both streams — `PTY.md`: "In pipe mode
  this is a combined stream without separate stdout/stderr channels." There is
  no way to re-split it.
- `PTYSessionStatusResponse` is `{session_id, running, output_offset}` — **no
  exit code**. `session.ExitCode()` exists in Go but is not exposed over HTTP,
  so an exit code missed on the socket is unrecoverable.

Together these make PTY **reconnect unusable** for a stream-json session: a
reconnect either relaunches the agent (session finished) or delivers an
interleaved blob that would corrupt the JSON stream (session live). §6.5 fails
closed on a drop instead.

### 2.5 Secure runtime is server-level

`docs/guides/secure-container.md`: `[secure_runtime] type` +
`k8s_runtime_class` are server config; "All sandboxes on that server
transparently use the configured runtime. SDK users and API callers require no
code changes." There is **no** API that reports the effective runtime.

Consequence: gVisor-vs-Kata is **endpoint routing**, not a request field (§4),
and the runtime class is verified by a boot probe (§5.3), not taken on trust.

## 3. Module layout

```
app/modules/workspace/autonomous/sandbox/opensandbox/
    __init__.py       re-exports
    config.py         EndpointConfig / SandboxBackendConfig, loading, attestation validation
    client.py         OpenSandboxApi Protocol + HttpOpenSandboxApi (REST) + endpoint-URL guard
    policy.py         spec+AgentTaskPolicy -> CreateSandboxRequest; capabilities; state mapping
    workspace.py      snapshot build, ChangeSet manifest, control-plane validation, apply
    transport.py      PtyWebSocketTransport (pipe-mode WS) implementing AgentTransport
    provider.py       OpenSandboxProvider
    fake_server.py    in-memory API double (incl. a fake WS transport)
app/modules/workspace/autonomous/sandbox/
    transport.py      AgentTransport Protocol + LocalProcessTransport (wraps Popen)
    isolation_tier.py required-isolation selection gate
    registry.py       (edit) resolve "opensandbox"
    legacy_posix.py   (edit) add get_transport() returning LocalProcessTransport
```

**Also modified** (these are not optional — see §6.5 and §6.6):

- `app/modules/workspace/autonomous/agent_runner.py` — `_run_local` consumes
  `provider.get_transport(exec_handle)` instead of `provider.get_process(...)`;
  `_select_sandbox_provider` routes through `isolation_tier.select_provider`.
- `app/services/autonomous_scheduler.py` — `_destroy_orphan_sandbox` currently
  returns early for every provider except `remote_machine`, so an OpenSandbox
  orphan would never be destroyed while the row is marked `destroyed` anyway.

No new pip dependency: `websockets>=13.0,<17.0` is already required, and the
repo already uses its **synchronous** client
(`app/modules/workspace/vscode_ws_bridge.py`, `terminal_ws_bridge.py`).

### 3.1 Outbound HTTP

`client.py` uses `requests` directly rather than `safe_request`, because the
OpenSandbox server is an in-cluster/private address that
`outbound_url_guard._is_public_address` rejects by design. Per `CLAUDE.md` rule
2 every call passes `proxies={"http": None, "https": None}` and carries the
reason in a comment.

**The lifecycle base URL is operator config and carries no SSRF surface. The
execd URL does not have that property** and must not be treated as if it did:
it comes from `GET /sandboxes/{id}/endpoints/{port}`, i.e. a **server-supplied
string**, and we then POST the entire workspace snapshot to it. So the client:

- validates the resolved endpoint host against a configured
  `execd_endpoint_host_allowlist` (defaulting to the tier `base_url`'s host
  suffix) and refuses anything else;
- sets `allow_redirects=False` on every execd call;
- filters the server-supplied `Endpoint.headers` through a key allowlist before
  forwarding them.

## 4. Configuration

First existing of `OPENACE_SANDBOX_BACKENDS` → `/etc/openace/sandbox-backends.json`
→ `~/.open-ace/sandbox-backends.json`, mirroring
`task_isolation.candidate_agent_task_policy_paths`.

```json
{
  "default_tier": "gvisor",
  "endpoints": {
    "gvisor": {
      "base_url": "http://opensandbox.open-ace.svc.cluster.local:8080/v1",
      "api_key_env": "OPENSANDBOX_API_KEY_GVISOR",
      "runtime_class": "gvisor",
      "default_image": "ghcr.io/open-ace/agent@sha256:0f3c...e91a",
      "execd_port": 44772,
      "execd_endpoint_host_allowlist": ["*.open-ace.svc.cluster.local"],
      "egress_allow_hosts": ["api.anthropic.com", "*.githubusercontent.com"],
      "attestations": {
        "egress_enforced": true,
        "egress_mode_dns_nft": true,
        "metadata_cidr_blocked": true,
        "execd_token_required": true,
        "secure_access_required": true,
        "nonroot_enforced": true,
        "readonly_rootfs": true,
        "seccomp_runtime_default": true,
        "dedicated_service_account": true,
        "pod_pids_limit": 512,
        "ephemeral_storage_enforced": true,
        "inode_quota_enforced": false
      },
      "pool": {"pool_ref": "", "egress_preapplied": false,
               "recycle_delete": false, "image_digest": ""}
    },
    "kata": {
      "base_url": "http://opensandbox-kata.open-ace.svc.cluster.local:8080/v1",
      "api_key_env": "OPENSANDBOX_API_KEY_KATA",
      "runtime_class": "kata-qemu",
      "default_image": "ghcr.io/open-ace/agent@sha256:0f3c...e91a",
      "execd_port": 44772,
      "execd_endpoint_host_allowlist": ["*.open-ace.svc.cluster.local"],
      "egress_allow_hosts": ["api.anthropic.com"],
      "attestations": {
        "egress_enforced": true, "egress_mode_dns_nft": true,
        "metadata_cidr_blocked": true, "execd_token_required": true,
        "secure_access_required": true, "nonroot_enforced": true,
        "readonly_rootfs": true, "seccomp_runtime_default": true,
        "dedicated_service_account": true, "pod_pids_limit": 512,
        "ephemeral_storage_enforced": true, "inode_quota_enforced": false
      }
    }
  },
  "rollout": {"mode": "allowlist", "tenants": ["42"], "projects": []},
  "tenant_tiers": {"42": "kata"},
  "project_tiers": {},
  "production_required_tenants": ["42"],
  "image_allowlist": ["ghcr.io/open-ace/agent@sha256:0f3c...e91a"],
  "image_signer_identity": "https://github.com/open-ace/open-ace/.github/workflows/release.yml@refs/heads/main",
  "resource_defaults": {"cpu": "2", "memory": "4Gi", "ephemeral-storage": "8Gi"},
  "sandbox_ttl_seconds": 3600,
  "changeset_limits": {"max_files": 2000, "max_file_bytes": 10485760,
                       "max_total_bytes": 104857600}
}
```

`egress_allow_hosts` is **per-endpoint**, so a high-security Kata tier can run a
narrower allowlist than the default gVisor tier.

### 4.1 Attestations

`attestations` records properties the provider **cannot observe through the
API** but that its capability claims depend on — pod `securityContext`, kubelet
`podPidsLimit`, the cluster `NetworkPolicy`, the egress sidecar mode, whether
execd's access token is set. Each one is an operator promise, checked at config
load for type and, where possible, verified at runtime (§5.3).

The rule: **a capability is declared only when its attestation is present.** An
absent or false attestation removes the capability, which makes any spec
requiring it fail closed at `create()`. There is no path where a missing
attestation degrades silently.

### 4.2 Resolution and fail-closed rules

- Tier: `project_tiers` (by project path) → `tenant_tiers` → `default_tier`.
- **Tenant keys are the decimal string of the integer `tenant_id`** this
  repository actually carries (`CommandExecutionEvidence.tenant_id: int`), not a
  slug. There is no name→id mapping anywhere in the codebase, so inventing one
  here would have produced a key nothing could supply.
- `rollout` is the gradual-rollout switch the issue's 上线策略 section asks for:
  `mode: "allowlist"` runs only the listed tenants/projects on OpenSandbox and
  leaves everything else on Legacy. Without it the only switch was whether the
  config file exists, which is all-or-nothing per deployment. It answers a
  *different* question from `tenant_tiers`, which picks gVisor vs Kata and
  cannot route anything back to Legacy. A tenant that is required but excluded
  from the rollout is rejected at parse time — that pair is incoherent, and
  letting either side silently win is the quiet downgrade this design exists to
  prevent.
- `production_required_tenants` lists the tenants for which Legacy is not an
  acceptable answer. It is the sole input to
  `isolation_tier.requires_production_isolation`; without it that predicate had
  no defined source and acceptance criterion 12 rested on nothing.
- A tier with no configured endpoint raises `SandboxConfigError`; it never falls
  back to another tier.
- Malformed config raises rather than defaulting.
- `egress_allow_hosts` entries must be FQDN or `*.`-wildcard; any IP literal is
  rejected outright (upstream cannot express IP rules), as are the metadata
  hostnames.
- `image_allowlist` and `default_image` entries must be digest-pinned
  (`@sha256:` + 64 hex).
- `sandbox_ttl_seconds` must be ≥ 60.

## 5. Capabilities

Derived from the resolved endpoint config, never a module constant — the
`#2082` lesson (`RemoteMachineProvider` copied Legacy's caps and enforced none;
the fix was `_REMOTE_CAPS = frozenset()`).

### 5.1 What we may honestly claim

The review established that an agent inside the sandbox can reach execd itself:
every command inherits execd's environment (§2.3), so the agent can read
`EXECD_ACCESS_TOKEN`, and `POST /command` accepts `uid: 0`. **Any in-band
mechanism — a `ulimit` prefix, a `uid` argument — is therefore bypassable and
cannot support a capability claim.** Enforcement must live at the pod/kernel
layer, which is what the attestations describe.

| Capability | Declared when | Enforced by |
| --- | --- | --- |
| `NAMESPACE_ISOLATION` | always, **and** the §5.3 runtime probe passes | separate container + the server's gVisor/Kata runtime class |
| `NETWORK_EGRESS_POLICY` | `egress_enforced` ∧ `egress_mode_dns_nft` ∧ `metadata_cidr_blocked`, **and** the §5.3 policy probe passes | egress sidecar `deny_all` in `dns+nft` mode + cluster `NetworkPolicy` |
| `PRIVATE_HOME_TMP_XDG` | always | one fresh container per sandbox; `HOME`/`TMPDIR`/`XDG_*` set via `CreateSandboxRequest.env` |
| `FILESYSTEM_ACL` | `nonroot_enforced` ∧ `readonly_rootfs` ∧ `seccomp_runtime_default` ∧ `dedicated_service_account` | pod `securityContext` (`runAsNonRoot`, `readOnlyRootFilesystem`, seccomp `RuntimeDefault`, dropped caps, `allowPrivilegeEscalation: false`) — **not** the `uid` we pass |
| `CPU_MEM_PIDS_TIME_QUOTA` | `pod_pids_limit > 0` | `resourceLimits` cpu/memory (kubelet) + kubelet `podPidsLimit` + the sandbox `timeout` TTL |
| `CREDENTIAL_TOKEN_BINDING` | `execd_token_required` ∧ `secure_access_required` | `env` constructed from an allowlist, never inherited; no GitHub write credential ever enters; execd reachable only with a token, sandbox endpoints only with `secureAccess` |
| `STORAGE_INODE_QUOTA` | `inode_quota_enforced` ∨ `ephemeral_storage_enforced` (both **default `false`**) | a real filesystem quota in the pod template. The disjunction matters: `implied_required_capabilities` requires this capability whenever `policy.ephemeral_storage_limit > 0`, so gating on `inode_quota_enforced` alone made §6.1's `ephemeral-storage` branch unreachable and the attestation inert. §5.2 refusal 8 then refuses the *inode* dimension specifically. |

`STORAGE_INODE_QUOTA` defaults off deliberately. `ulimit -f` caps a single
file's size, and a Kubernetes `ephemeral-storage` limit is enforced by kubelet
*eviction* polling with no inode dimension — neither is an inode or total-storage
quota. Declaring it on those would write `"enforced": {"inode": true}` to the
workflow row via `build_effective_policy`, which is exactly the lie that
module's docstring warns about. With it off, any spec carrying
`policy.inode_limit > 0` fail-closes at `create()`, because
`implied_required_capabilities` requires the capability we do not declare.

`CREDENTIAL_TOKEN_BINDING` is honest but bounded, and §11's guide says so
plainly: the agent can reach execd as root inside its own sandbox. The
guarantee is that it cannot reach the **control plane's** credentials, another
tenant's sandbox, or a GitHub write token — not that it is unprivileged within
its own blast radius.

### 5.2 Fail-closed refusals at `create()`

1. `spec.network_egress.allow_cidrs` non-empty — upstream cannot express IP
   egress rules; dropping them would run a restrictive-looking spec wide open.
2. `spec.network_egress.mode == "unrestricted"`.
3. any host-backed `VolumeSpec`, or a `mount_path` outside the workspace root.
4. resolved image absent from `image_allowlist`, or not digest-pinned.
5. pool mode without all of `egress_preapplied`, `recycle_delete` and a
   digest-pinned allowlisted `image_digest` (§8).
6. a required capability the resolved endpoint does not declare — via the shared
   `provider.validate_spec_capabilities`.
7. `secure_access_required` absent (peer sandboxes would be reachable
   unauthenticated).
8. `policy.inode_limit > 0` while `inode_quota_enforced` is absent — the
   ephemeral-storage attestation alone does not bound inode count.
9. **Any of the pod-hardening attestations absent**: `nonroot_enforced`,
   `readonly_rootfs`, `seccomp_runtime_default`, `dedicated_service_account`,
   `execd_token_required`, or `pod_pids_limit == 0`.

Refusal 9 exists because declaring honestly is not the same as running safely.
Production specs arrive with `required_capabilities=frozenset()`, and
`implied_required_capabilities` only derives `NETWORK_EGRESS_POLICY`,
`NAMESPACE_ISOLATION` and `STORAGE_INODE_QUOTA` from spec fields — nothing
implies `FILESYSTEM_ACL`, `CPU_MEM_PIDS_TIME_QUOTA` or
`CREDENTIAL_TOKEN_BINDING`. Without refusal 9, a tier attesting none of the pod
hardening would *correctly* decline to declare those three capabilities and
then **run the agent anyway** — as root, on a writable rootfs, against an
unauthenticated execd. The declaration would be honest and the execution would
have degraded silently, which is the thing §4.1 promises cannot happen. The
issue's §3 runtime-policy list requires these properties, so the provider
refuses rather than treating them as advisory.

Because production specs arrive with `runtime`, `network_egress` and
`volumes` all `None` (`agent_runner.py:2645-2656`), the provider **synthesises
them from the resolved tier config before running these refusals**, so the
refusals evaluate the request that will actually be sent. Refusal 6 is
additionally applied against the *synthesised* egress policy, so a tier that
cannot enforce egress fails closed even though the incoming spec asked for
nothing.

### 5.3 Runtime probes — turning attestations into facts

At the first `create()` per endpoint, the provider runs two cheap probes and
caches the result for the process lifetime:

- **Runtime probe** — `cat /proc/version` in the fresh sandbox. gVisor
  advertises itself there; Kata reports a distinct kernel. Mismatch against
  `runtime_class` → `SandboxError`, sandbox destroyed. This converts acceptance
  criteria 3 and 4 from an operator's word into a verified fact.
- **Egress probe** — `GET` the sidecar `/policy` and assert
  `defaultAction == "deny"` **and** `enforcementMode == "dns+nft"`. Mismatch →
  `SandboxError`. Without this, `NETWORK_EGRESS_POLICY` rests on an unverifiable
  boolean.

## 6. Lifecycle mapping

| Contract | Calls |
| --- | --- |
| `create` | `POST /v1/sandboxes` with `image: {uri: <digest-pinned>}`, `entrypoint` (idle supervisor), `resourceLimits` (§6.1), `networkPolicy` (deny + tier allowlist), `timeout` = TTL, `secureAccess: true`, `env` (§6.2), `metadata` = string-valued `{openace.task_id, openace.tenant, openace.generation, openace.provider}` |
| `upload_workspace` | resolve + validate execd URL (§3.1), `POST /files/upload` per file, **then** synthesise the git repository (§7.1) via a foreground `POST /command` — after the files land, never in the entrypoint |
| `exec` | discriminated by `exec_policy` (the contract's `Any` slot, mirroring `RemoteMachineProvider`'s `RemoteTurnSpec`): an `OpenSandboxTurnSpec` selects the PTY transport (§6.5), anything else runs `POST /command` **foreground** with a `shlex.quote`-joined argv. The provider records the branch per `command_id` so `stream`/`stop` resolve the same way. |
| `stream` | §6.3 |
| `pause`/`resume` | `POST /v1/sandboxes/{id}/pause|resume`, then poll `GET /v1/sandboxes/{id}` to `Paused`/`Running` before returning |
| `stop` | PTY: `{"type":"signal","signal":"SIGINT"}` then `DELETE /pty/{id}`; command: `DELETE /command?id=` |
| `inspect` | `GET /v1/sandboxes/{id}` → §6.4 mapping; `404` → `DESTROYED` |
| `destroy` | `DELETE /v1/sandboxes/{id}`, then poll to a terminal state; `404` = success, idempotent |
| `destroy_attribution` | `DELETE /v1/sandboxes/{sandbox_id}` by persisted id; best-effort, never raises |
| `reconcile_orphans` | `GET /v1/sandboxes` **paginated** (`page` from 1 until a short page, bounded by a max-page guard that logs on trip), filtered on `metadata["openace.provider"]`; destroy every id the control plane does not claim |

### 6.1 `AgentTaskPolicy` → limits

`spec.policy` is authoritative; `resource_defaults` fills only the dimensions
the policy leaves at `0`.

| Policy field | Target | Conversion |
| --- | --- | --- |
| `memory_max_bytes` | `resourceLimits["memory"]` | bytes → `"<n>"` (Kubernetes accepts a plain byte count) |
| `cpu_max` | `resourceLimits["cpu"]` | cgroup-v2 `"<max> <period>"` → millicores `f"{max/period*1000:.0f}m"`; `"max"` → `resource_defaults["cpu"]` |
| `ephemeral_storage_limit` | `resourceLimits["ephemeral-storage"]` | bytes → `"<n>"`, only when attested |
| `wall_clock_limit` | sandbox `timeout` (s, ≥60) **and** per-command `timeout` (ms) | TTL is `max(wall_clock_limit, sandbox_ttl_seconds, 60)` when `wall_clock_limit > 0`, else `sandbox_ttl_seconds`. The per-command `timeout` key is **omitted entirely** when `wall_clock_limit == 0`, because upstream's contract is "if omitted, the server will not enforce any timeout" and sending `0` is not the same as omitting it. `wall_clock_limit` defaults to `0` and `read_agent_task_policy` returns all-defaults when no `agent-launcher.conf` exists — the common case — so a naive `max(wall_clock_limit, 60)` would have given every agent run a 60-second lifetime. |
| `pids_max` | *not sent* | enforced by kubelet `podPidsLimit`; a policy `pids_max` above the attested limit is refused at `create()` rather than silently ignored |
| `inode_limit` | *not sent* | fail-closes unless `inode_quota_enforced` (§5.1) |

The applied values — not the requested ones — are what §6.6 records.

### 6.2 Environment

Built from `{}`, never `dict(os.environ)`: `HOME`, `TMPDIR`, `XDG_CACHE_HOME`,
`XDG_CONFIG_HOME`, `XDG_DATA_HOME`, `PATH`, and the LLM-proxy variables. No
`GITHUB_TOKEN`, `GH_TOKEN` or `GH_CONFIG_DIR` is ever emitted.

**Delivery.** `CreateSandboxRequest.env` cannot carry it: the contract is
`create(spec)` with no env parameter, `SandboxSpec` has no env field, and the
runner builds the env — including a **short-lived signed proxy token minted per
run** — only in time for `exec(handle, command, env, exec_policy)`. PTY sessions
take no `envs` either. The delivery vehicle is `CreatePTYSessionRequest.command`,
which `buildPTYCommand` runs as `bash -c "<command>"` (§2.4):

```
command = "export HOME=… TMPDIR=… OPENACE_PROXY_TOKEN=…; exec <cli> …"
```

every value `shlex.quote`d, **and the argv quoted the same way** — the PTY branch
has the identical injection surface as the `/command` branch, so a branch name or
path carrying shell metacharacters must not be treated differently there. This
keeps the token's lifetime short (minted per run, never persisted into the
sandbox spec) and keeps `create()` signature-compatible with the frozen contract.

Three details:

- **Unset is not empty.** An `export` is emitted only for keys with a non-empty
  value. `export K=''` sets `K` to the empty string, and some CLI adapters treat
  an empty `ANTHROPIC_API_KEY` differently from an absent one.
- **A newline is refused, not quoted.** A newline ends the `export` statement and
  introduces a command no downstream quoting can contain.
- **Length is bounded.** The whole string is one `execve` argument, capped by
  Linux `MAX_ARG_STRLEN` (128 KiB) regardless of `ARG_MAX`. Realistic env plus
  argv is a few KB, but exceeding it fails with a bare `E2BIG` that is very hard
  to diagnose, so the provider checks the assembled length and raises a
  structured `command_too_long` refusal instead.

Static, non-secret values (`PATH`, `HOME`, `TMPDIR`, `XDG_*`) additionally ride
on `CreateSandboxRequest.env` because §7.1's repo synthesis runs before any PTY
session exists; the per-run secrets never do.

### 6.3 `stream` event mapping

`tests/unit/test_sandbox_events.py:64-77` pins the sequence **every** provider
must emit: `PROCESS_STARTED`, `COMMAND_STARTED`, `STDOUT_CHUNK`,
`COMMAND_COMPLETED`, `PROCESS_EXITED`, each carrying `sandbox_id`.

- `PROCESS_STARTED` and `COMMAND_STARTED` are **synthesised** on entering
  `stream()`, as `LegacyPosixProvider.stream` does.
- SSE `stdout`/`stderr` → `STDOUT_CHUNK`/`STDERR_CHUNK`; PTY `0x01`/`0x02`
  frames → the same.
- `execution_complete` → `COMMAND_COMPLETED` (exit 0).
- SSE **`error` with a numeric `evalue`** → `COMMAND_COMPLETED` carrying that
  exit code. This is a normal non-zero exit (§2.3), **not** a sandbox error —
  mapping it to `SANDBOX_ERROR` would report every failing `pytest` run as
  infrastructure failure. `error` with a non-numeric `evalue` → `SANDBOX_ERROR`.
- PTY JSON `exit` frame → `COMMAND_COMPLETED` with its `exit_code`.
- `PROCESS_EXITED` is synthesised after the terminal event.
- Stream exhaustion with no terminal event → poll `GET /command/status/{id}`;
  still running past the deadline → `COMMAND_TIMED_OUT`. A non-completion is
  **never** reported as `COMMAND_COMPLETED`.

### 6.4 State mapping and the contract tests

`Pending`→`CREATED`; `Running`/`Resuming`→`RUNNING`; `Pausing`/`Paused`→`PAUSED`;
`Stopping`→`STOPPED`; `Terminated`→`DESTROYED`; `Failed`→`ERROR`; unknown→`ERROR`.

Three existing contract tests would otherwise fail against upstream's real
timing, and the fix is in the provider, not in the fake:

- `test_inspect_returns_live_status_after_create` expects `CREATED`, but create
  returns `Running`. The provider keeps a **local status overlay**: a handle is
  `CREATED` until its first `exec`, after which `inspect` reports the upstream
  state. The overlay is per-`sandbox_id` and dropped on destroy.
- `test_stop_transitions_to_stopped` expects `STOPPED` after `stop()`.
  Interrupting a command does not change the sandbox state upstream, so `stop()`
  sets the overlay to `STOPPED`.
- `test_destroy_marks_destroyed` expects `DESTROYED` immediately. `destroy()`
  polls to a terminal state and sets the overlay to `DESTROYED` **only when the
  poll actually observed `Terminated` or a `404`**. On timeout, or a `409`/`5xx`
  from `DELETE`, the overlay is left unset so `inspect` reports the true
  `STOPPED`/`ERROR`, and an audit event fires with reason code
  `destroy_unconfirmed` so the reconciler retries. Reporting `DESTROYED` for a
  sandbox still consuming quota and network would be the same lie as
  `_reconcile_orphan_sandboxes` marking a row `destroyed` without destroying
  anything — the bug §6.6 exists to fix.

The `STOPPED` overlay is cleared on the next `exec`. Otherwise cancelling one
agent turn would make `inspect` report `STOPPED` for the rest of the sandbox's
life, including while a later `exec` is running.

`fake_server.py` models upstream's **real** timing (create → `Running`, delete →
`Stopping` then `Terminated`) so the overlay is genuinely exercised rather than
papered over.

### 6.5 Agent transport — the replaceable local seam

`_run_local` drives the CLI over an interactive stdin
(`--input-format stream-json`) through the Legacy-only escape hatch
`get_process()`. The coupling is wider than a first read suggests; the complete
verified inventory in `agent_runner.py` is:

| Site | Use | Transport equivalent |
| --- | --- | --- |
| 2661 | `build_launch_argv(...)` for a log line | guard with `hasattr` — it is a Legacy-only hatch, not on the Protocol, and calling it unconditionally raises `AttributeError` on any other provider **before `exec` runs** |
| 2676 | `get_process(exec_handle)` | `get_transport(exec_handle)` |
| 2727 | `_on_pid_registered(session_id, process.pid)` | skip when `transport.pid is None` |
| 2756, 2761 | `completed`-gated `returncode is None` → `wait(timeout=5)` | `poll()` / `wait(timeout=5)`, kept **outside** `shutdown()`. This window exists so the isolated launcher can finish its `.git` integrity check after the CLI's terminal event; folding it into `shutdown()` would start signalling mid-check. |
| 2764–2770 | `os.killpg(os.getpgid(process.pid), SIGTERM/SIGKILL)` + waits | `transport.shutdown(grace=5.0)` — "signal the process group" has no meaning for a container backend, and `os.getpgid(None)` raises `TypeError`, which none of the surrounding `except` clauses catch |
| 2801, 2842 | `returncode` → `_classify_isolated_exit_code` / `_classify_sidebar_start_failure` | `poll()` |
| **4146** | `if session.process is None: return False` in `_write_stdin` | `session.transport is None` — **the single most important row here.** Under §6.5's "`process` populated only by `LocalProcessTransport`" rule this guard is `True` for OpenSandbox, so `_send_sdk_init` and `_send_message` silently write nothing: the agent is launched and never given the prompt. |
| 4148–4149 | `stdin.write` / `flush` | `write_stdin` |
| **4179** | `if session.process is None: break` in `_read_stdout` | `session.transport is None` — otherwise the reader breaks on its first iteration and no stream-json is ever parsed |
| 4181 | `stdout.readline` | `readline_stdout` |
| 4371–4372 | `stdin.close` | `close_stdin` |
| 4534–4536 | `poll()` / `returncode` | `poll()` |
| **4543** | `if session.process is None: break` in `_read_stderr` | `session.transport is None` — otherwise `session.last_stderr` stays empty, which also feeds `_classify_isolated_exit_code` at 2801 |
| 4545 | `stderr.readline` | `readline_stderr` |
| 4600, 4628, 4637 | `returncode` / `wait` in `stop_session` | `poll()` / `wait` |
| 4655, 4683 | `not session.process` guard in `pause_session` / `resume_session` | **reorder** — see below |
| 4718, 4730 | `mark_session_*_by_pid` matching `process.pid == pid` | inapplicable — see below |

```python
class AgentTransport(Protocol):
    def write_stdin(self, data: bytes) -> None: ...
    def close_stdin(self) -> None: ...
    def readline_stdout(self) -> bytes: ...   # b"" at EOF
    def readline_stderr(self) -> bytes: ...
    def poll(self) -> int | None: ...         # may reap, like Popen.poll()
    def wait(self, timeout: float | None = None) -> int | None: ...
    def shutdown(self, grace: float = 5.0) -> None: ...   # graceful -> forceful
    @property
    def pid(self) -> int | None: ...          # None for non-local backends
    @property
    def returncode(self) -> int | None: ...   # cached, NEVER reaps
```

`returncode` and `poll` are deliberately separate. `pause_session` and
`resume_session` read `Popen.returncode` today — a plain attribute, no syscall.
Routing their guard through `poll()` would add a `waitpid(WNOHANG)` on a path
that runs concurrently with `_wait_for_completion`'s own `poll()`, and `Popen`
is not documented as safe for concurrent reaping from several threads. The
reordered guards read `returncode`; only the polling loop calls `poll`.

- `LocalProcessTransport` wraps the existing `Popen` one-to-one, with
  `shutdown()` performing exactly today's `SIGTERM` → wait → `SIGKILL`
  process-group escalation. **Zero behaviour change** on the local path, which
  is what makes this safe to land.
- `PtyWebSocketTransport` opens `POST /pty` (carrying the env-bearing `command`
  from §6.2) then `ws://…/pty/{id}/ws?pty=0` via `websockets.sync.client`, writes
  stdin as `0x00` + bytes, demultiplexes `0x01`/`0x02` into two line-buffered
  queues, and resolves `poll`/`wait` from the JSON `exit` frame. `pid` is `None`;
  `shutdown()` sends `{"type":"signal","signal":"SIGINT"}` then
  `DELETE /pty/{id}` (upstream delivers signals to the process group, so this
  reaches the whole tree). It takes a `connect_factory` so unit tests drive a
  fake connection rather than standing up a real socket.

**`_LocalSession` keeps one source of truth.** `transport` is always set;
`process` is populated **only** by `LocalProcessTransport` (exposing the wrapped
`Popen`), so the Legacy path keeps its current object identity while every
call site above reads `transport`. Setting `process=None` universally would
change Legacy behaviour at a dozen sites on the hot path of every autonomous
workflow — the opposite of the rollout guarantee.

**Pause/resume must be reordered.** `pause_session` (`:4655`) and
`resume_session` (`:4683`) both begin
`if not session or not session.process or session.process.returncode is not None: return False`,
**before** the provider branch at `:4661`/`:4687`. With a pidless transport they
would return `False` without ever reaching the provider, making pause and resume
permanently unavailable for this backend — while acceptance criterion 2 requires
them and `#2022`'s `test_pause_resume_transitions_status` exercises them. The
guard becomes "session exists and `transport.poll() is None`", and the
docstring's "reached only for local sessions with a live process" invariant is
rewritten.

`mark_session_paused_by_pid` / `mark_session_resumed_by_pid` (`:4718`, `:4730`)
match on `process.pid` and are structurally inapplicable to a pidless transport.
They are the Strategy-2/3 fallbacks that freeze `_wait_for_completion`'s timeout
budget; for OpenSandbox, `pause_session` is the only path, and the provider
branch there sets `session._paused`, which is what actually freezes the budget.
This is documented rather than papered over.

**No reconnect.** §2.4 establishes that re-attaching either relaunches the agent
(finished session) or delivers a channel-merged `0x03` replay blob that would
corrupt the stream-json parser (live session), and that a missed `exit` frame is
unrecoverable because `GET /pty/{id}` carries no exit code. So a dropped socket
is **terminal**: the transport stops, `poll()`/`wait()` resolve via
`GET /pty/{id}`, and `running: false` with no exit frame yields a structured
`CRASH` with reason code `pty_stream_lost` — never a hang and never a silent
`COMPLETED`. `wait(timeout=)` always honours its deadline so
`_wait_for_completion` cannot block indefinitely.

**Threading.** `_read_stdout`/`_read_stderr` are `threading.Thread(daemon=True)`,
and both `server.py` and `app/scheduler_worker.py` call `monkey.patch_all()`, so
those are greenlets and the transport's blocking socket reads stay cooperative —
the same basis on which `vscode_ws_bridge.py` and `terminal_ws_bridge.py` already
drive `websockets.sync.client` in this process.

**Rollout:** the OpenSandbox path is reachable only when a backend config exists
*and* the task is inside `rollout` (§4). With no config the local path is
**behaviourally equivalent** to today — not byte-identical, since Task 11's edits
are unconditional. The pin for that equivalence is the pre-existing local-path
suite, which must pass unchanged: `tests/unit/test_agent_runner_signal_routing.py`
and everything under `pytest tests/unit -k "agent_runner"`.

### 6.6 Selection gate, reconciliation and audit

- `isolation_tier.select_provider` must reach **`_run_local`**, not merely
  `_select_sandbox_provider`. That method has exactly one caller —
  `agent_runner.py:3724`, inside `_run_remote` — while `_run_local` uses the
  constructor-injected `self._sandbox_provider` directly at nine sites (2645,
  2661, 2665, 2676, 2682, 2695, 2714, 2779, 2881), and `__init__:788` defaults
  it to `LegacyPosixProvider()`. Routing only the selector through the gate
  would leave the local path on Legacy and turn the gate into the helper
  nothing calls — the same failure shape as the unreachable
  `destroy_attribution` below, one level up. So `_run_local` resolves a
  per-run `provider = self._select_sandbox_provider("local", tenant=…,
  project_path=project_path)` and threads it through all nine sites, including
  `_LocalSession.sandbox_provider` and both `_stamp_sandbox_attribution` calls.
  A tenant in `production_required_tenants` gets OpenSandbox **or a raise**;
  there is no code path from "required" to Legacy.
- **Tenant supply.** `_run_local` has no tenant parameter today (it carries
  `user_id`, and the runner derives `tenant_id` ad hoc by user lookup elsewhere).
  It gains an explicit `tenant_id: int | None = None`, threaded from
  `run_agent_task`, rather than an inline lookup — the value must be the same
  one the evidence rows are stamped with, and a second derivation path would
  drift from the first.
- **Injection is preserved.** `__init__:788` is
  `sandbox_provider or LegacyPosixProvider()`, and
  `tests/unit/test_agent_runner_signal_routing.py` injects a provider seven
  times. With no backend config the gate returns `self._sandbox_provider`
  **unchanged** — it must not construct a fresh `LegacyPosixProvider()`, which
  would silently discard the injected one.
- `autonomous_scheduler._destroy_orphan_sandbox` currently returns before
  `provider_for(...)` for anything that is not `remote_machine`
  (`:1717`), while `_reconcile_orphan_sandboxes` then marks the row
  `destroyed`. Left alone, every OpenSandbox sandbox survives a control-plane
  restart with the DB claiming otherwise. The gate becomes "does the persisted
  provider own an external resource" — `remote_machine` with a session id, or
  `opensandbox` with a `sandbox_id`.
- Audit events: the provider takes an optional `event_sink` callable
  (`Callable[[str, dict], None]`) and `provider_for` gains **two** keyword-only
  optional parameters, `event_sink` and `api_factory`, which keeps
  `tests/unit/test_sandbox_registry.py` green. `api_factory` is not decoration:
  `_destroy_orphan_sandbox` builds its provider through `provider_for`, so
  without it the scheduler-layer reconciliation test has no seam to inject a
  fake API through and would silently construct a real HTTP client.
  Every lifecycle call **and every refusal** emits one. Refusals also carry a
  structured `reason_code` on the raised `SandboxError` so a caller that has no
  sink still surfaces the cause.
- `runtime_class` and `tier` are recorded in **audit events only**.
  `build_effective_policy` is left unchanged, and §4's earlier claim that the
  snapshot records `runtime_class` is withdrawn — the function has no such
  parameter and changing it is out of scope.

## 7. Workspace transfer and ChangeSet

```
control plane prepares trusted base/worktree
  -> upload credential-free snapshot
  -> agent edits inside the sandbox
  -> supervisor produces manifest
  -> control plane validates path/size/count/mode/symlink/secret
  -> apply to trusted worktree
  -> GitHubOps commit/push (control plane only)
```

### 7.1 Snapshot

`build_snapshot(worktree)` yields `(relative_path, bytes, mode)`, excluding
`.git/` and `.ssh/` at any depth, `.git-credentials`, `.netrc`, `.npmrc`,
`.pypirc`, `.env*`, `*.pem`, `*.key`, `id_rsa*`, and any symlink resolving
outside the worktree. Files are uploaded `0644`, directories `0755`, with
`owner`/`group` set to the names matching the container's runtime user — execd
may run as root, and root-owned files under a restrictive mode would leave the
agent unable to edit its own workspace.

Excluding `.git/` means the agent sees a working tree, not a repository — and
now that the agent actually runs in the sandbox (§6.5), that would break it. The
repo's own `agent_bin/` ships guard wrappers for `git`, `gh`, `pytest`,
`python`, and `_build_agent_env` sets `OPENACE_REAL_GIT` / `OPENACE_REAL_GH` /
`GH_CONFIG_DIR` / `GIT_TERMINAL_PROMPT=0` precisely because the agent shells out
to git. Without a repository, every `git status` / `git diff` / `git log` fails
and `pre-commit` cannot run.

So the provider **synthesises a fresh repository** inside the sandbox: `git init`
plus a single commit of the uploaded snapshot, with no remote, no credential
helper and no link to the trusted common-dir. The agent gets a working `git`
whose entire history is the snapshot it was given; the control plane still owns
the real repository, and the ChangeSet remains a file manifest rather than a git
diff. The read-only-clone variant stays out of scope (§9) — it would expose real
history and remote metadata for no benefit this does not already provide.

Three details decide whether this actually works.

**It runs after the upload, not in the entrypoint.** The lifecycle order is
`create` → `upload_workspace` → `exec`, and the container entrypoint runs during
`create`. An entrypoint that ran `git init && git add -A && git commit` would
execute against an empty `/workspace`, commit nothing, and leave the snapshot
landing afterwards as entirely untracked files — `git diff` empty, `git status`
showing the whole tree as new, `pre-commit` staging against a `HEAD` that does
not exist. Synthesis is therefore an explicit step **between**
`upload_workspace` and the first `exec`, issued as a foreground `POST /command`.

**It carries its own identity.** Refusal 9 makes `readonly_rootfs` and
`nonroot_enforced` mandatory, so there is no writable `~/.gitconfig` and no
ambient identity; `git commit` would fail with "Please tell me who you are" and
silently produce a repo with no `HEAD`. The command is self-contained:

```
git init -q && git add -A && git -c user.name='Open ACE' -c user.email='agent@open-ace.invalid'     -c commit.gpgsign=false commit -q -m 'snapshot'
```

**`HOME` must exist and be writable before it runs.** §6.2 delivers the agent
env through the PTY command string, which does not exist yet at this point. So
the static, non-secret half — `HOME`, `TMPDIR`, `XDG_*` — additionally rides on
`CreateSandboxRequest.env`, and §11's pod template mounts a writable volume at
`HOME` alongside the read-only rootfs. The per-run secrets still travel only via
the PTY command.

### 7.2 Manifest and validation

The manifest is JSON: `{"files": [{path, mode, size, sha256, symlink_target}],
"deleted": [path]}`. The explicit `deleted` list exists because the control
plane has no baseline to diff the returned tree against, so a deletion is
otherwise invisible.

**The supervisor excludes `.git/` from the manifest** — the same exclusion
`build_snapshot` applies on the way in. §7.1 puts a real repository inside the
sandbox, so a manifest built by naively walking `/workspace` would carry hundreds
of `.git/objects/**` entries; every one is rejected as `repo_integrity`, and
because apply is all-or-nothing, **no ChangeSet would ever be applicable**. The
validator keeps rejecting `.git` as defence in depth; the supervisor not
producing it is what makes the pipeline work.

**Apply is additive-plus-explicit-deletes, never a full sync.** It writes the
`files` entries and removes exactly the `deleted` entries. It never removes a
path absent from the manifest — a full sync would delete the trusted
repository's `.git` directory, since `.git` can never appear in a manifest.

`validate_changeset` runs entirely control-plane side and returns **all**
rejections (not first-fail) so the audit event lists everything wrong:

`absolute_path`, `path_escape` (any `..`, or a `realpath` outside the root),
`symlink_escape`, `file_too_large`, `too_many_files`, `total_too_large`,
`unsafe_mode` (setuid/setgid/sticky/non-regular), `secret_path`. The
`deleted` list gets the same path checks.

The secret-pattern set is defined in `workspace.py` as a concrete tuple —
`*.pem`, `*.key`, `id_rsa*`, `.env*`, `.git-credentials`, `.netrc`, `.npmrc`,
`.pypirc`, `**/.ssh/**`, `**/.aws/credentials` — not a reference to a module
that does not exist.

`apply_changeset` validates the whole manifest first and raises before the first
write; writes land in a temp dir and are moved into place, so an I/O failure
cannot leave a partial tree.

### 7.3 Execution evidence

The provider does **not** reimplement the terminal-reason mapping. It gathers
the raw signals and calls `command_evidence.types.derive_terminal_reason`, the
single canonical mapper:

- decode `128+n` exit codes into `signal=n` **before** calling it — otherwise
  `derive_terminal_reason(exit_code=137)` returns `COMPLETED` and the two paths
  disagree about the same run;
- `timed_out` / `cancelled` from the terminal event `stream()` reached;
- `has_result=False` when no status row exists.

Fields filled on `CommandExecutionEvidence`: `command_id`, `sandbox_id`,
`sandbox_generation`, `cwd`, `exit_code`, `signal`, `terminal_reason`,
`stderr_digest`, `stderr_artifact`, `started_at`, `completed_at` — the last two
from `CommandStatusResponse.started_at`/`finished_at`. Using foreground
`/command` (§2.3) is what makes `stderr_*` obtainable at all.

**Pod-level kills.** A `resourceLimits["memory"]` breach OOM-kills the whole
container **including execd**, so `/command/status/{id}` becomes unreachable and
there is no exit code. The provider falls back to `GET /v1/sandboxes/{id}`: a
`Failed` state whose `status.reason`/`message` indicates OOM or eviction maps to
`SIGNAL`; any other `Failed` maps to `CRASH`. `fake_server.py` models this
shape — execd unreachable plus `Failed` — rather than a convenient exit 137,
because the convenient version would test a situation that cannot occur.

## 8. Warm pool

Pool mode uses `extensions.poolRef`, and upstream rejects `image`,
`resourceLimits`, `networkPolicy` and `volumes` alongside it. So in pool mode
the image allowlist, the digest pinning, the resource limits **and** the egress
policy all come from the Pool CRD template, none of which the provider can read
— there is no pool-inspection endpoint.

Additionally `kubernetes/apis/sandbox/v1alpha1/pool_types.go` defines
`RecycleStrategy.Type ∈ {Delete, Restart, Noop}`. Under `Noop` the previous
tenant's pod — its `/workspace`, its `HOME`, anything on disk — is handed to the
next allocation unchanged. `Restart` restarts containers but keeps volumes.

Pool mode is therefore permitted **only** when the endpoint config attests all
three: `egress_preapplied`, `recycle_delete` (the template sets
`recycleStrategy.type: Delete`) and a digest-pinned, allowlisted `image_digest`.
Any missing attestation refuses pool mode rather than running with weaker
guarantees than requested.

Provider-side hygiene: per-sandbox state lives in dicts keyed by `sandbox_id`
and is popped on destroy; each allocation gets a freshly constructed `env`, a
fresh uploaded workspace and a fresh evidence namespace. No secret, HOME,
workspace, token or evidence state crosses allocations.

## 9. Scope

**In scope:** everything above, including the PTY transport and the
`agent_runner` / `autonomous_scheduler` wiring that makes the backend actually
reachable in production.

**Out of scope**, per the issue's own exclusion list: the `SandboxProvider`
contract (`#2022`), Legacy minimal hardening (`#2020-A`), Git worktree/branch
cleanup (`#2043`), verify-before-act (`#2045`), test parser and verdict
(`#2046`), transcript semantics (`#2047`), phase-handler split (`#2044`).

Also out of scope, and stated in `docs/sandbox-backends.md` rather than left
implicit: cosign signature and SBOM verification, which belong at admission time
(§11) — the provider enforces allowlist membership and digest pinning only;
and the read-only-clone variant of workspace transfer (§7.1 ships the
credential-free snapshot branch).

## 10. Tests

`tests/unit/`, marked `pytest.mark.regression` + `pytest.mark.issue(2023)`,
driven by `fake_server.py`, running in the SQLite `test(3.x)` lane.

The eight the issue requires:

| Test | Asserts |
| --- | --- |
| `test_sandbox_cannot_read_host_or_peer_workspace` | host-backed volumes refused; `.git`/credential paths excluded from the snapshot; **and** `secureAccess: true` is set so a peer sandbox cannot reach this one's endpoint unauthenticated; a peer request without the token is rejected by the fake |
| `test_default_egress_blocks_metadata_private_cidr_and_unknown_domain` | generated policy is `defaultAction: deny`; metadata/private/unlisted targets absent; `allow_cidrs` refused; **and** the §5.3 egress probe runs and fails closed when the sidecar reports `enforcementMode: dns` instead of `dns+nft` |
| `test_changeset_rejects_absolute_path_symlink_escape_and_oversize_file` | each rejection class returns a structured reason and applies nothing |
| `test_resource_limits_return_structured_terminal_reason` | both shapes — a child killed under the cgroup (exit `128+9` → `SIGNAL`) and a pod-level OOM (execd unreachable + `Failed` → `SIGNAL`); timeout → `TIMEOUT`; never `COMPLETED` |
| `test_warm_pool_does_not_reuse_tenant_state` | a pool endpoint missing any of the three attestations is refused; an attested pool gets fresh env/workspace/evidence per allocation |
| `test_node_and_control_plane_restart_reconcile_sandbox` | asserted at the **scheduler** layer: `_destroy_orphan_sandbox` on an `opensandbox` row reaches `destroy_attribution`; the paginated sweep destroys unclaimed sandboxes across more than one page; never raises |
| `test_required_production_policy_cannot_fallback_to_legacy` | asserted through `agent_runner._select_sandbox_provider`, not a standalone helper: a required-isolation tenant yields OpenSandbox or raises, never Legacy |
| `test_execution_evidence_matches_provider_contract` | `sandbox_id`, `sandbox_generation`, `exit_code`, `signal`, `terminal_reason`, `stderr_digest`, `started_at`, `completed_at`; the reason comes from `derive_terminal_reason`, not a parallel table |

Regression cases the second review round added, each pinning a defect that would
otherwise have shipped:

- `test_run_local_resolves_its_provider_through_the_isolation_gate` — drives
  `_run_local` itself, not the selector in isolation.
- `test_pty_transport_refuses_to_reconnect_to_a_stopped_session` and
  `test_dropped_socket_without_exit_frame_resolves_to_crash_not_hang`.
- `test_teardown_does_not_dereference_pid_for_a_pidless_transport` — the
  `os.getpgid(None)` `TypeError` no surrounding `except` catches.
- `test_pause_and_resume_reach_the_provider_for_a_pidless_transport`.
- `test_tier_without_pod_hardening_attestations_is_refused`.
- `test_zero_wall_clock_falls_back_to_configured_ttl_not_60s` and
  `test_zero_wall_clock_omits_command_timeout_rather_than_sending_zero`.
- `test_destroy_that_times_out_does_not_report_destroyed` and
  `test_stopped_overlay_clears_on_next_exec`.
- `test_agent_can_run_git_in_the_sandbox` — the synthesised repository (§7.1).
- `test_build_launch_argv_is_not_called_for_a_non_legacy_provider`.

Round three adds:

- `test_agent_receives_the_prompt_over_a_pidless_transport` — drives `_run_local`
  with a fake pidless transport and asserts the SDK-init and prompt bytes reached
  `write_stdin` and that a stdout line was parsed. An end-to-end "the agent was
  actually spoken to" assertion, not a call-site swap assertion.
- `test_repo_synthesis_runs_after_upload_and_produces_a_commit`.
- `test_manifest_from_a_sandbox_with_a_synthesised_repo_applies_cleanly`.
- `test_pause_guard_reads_returncode_without_reaping`.
- `test_gate_returns_the_injected_provider_when_no_config_is_present`.
- `test_pty_command_over_the_length_cap_is_refused`.

Plus: `#2022` contract-conformance against `OpenSandboxProvider` with the fake
modelling upstream's real timing (§6.4); capability-realism probes asserting each
declared capability maps to an attestation or a passing runtime probe; a Kata
tier test asserting the runtime probe rejects a gVisor kernel on a `kata-qemu`
endpoint (acceptance criterion 4); a fork-bomb case producing a structured
`SIGNAL` under the attested `podPidsLimit`; a network-scan case refused by the
fake sidecar (acceptance criterion 8); `shlex.quote` argv escaping; execd
endpoint-host validation rejecting an off-allowlist URL; PTY transport
round-trip including frame demultiplexing and reconnect-with-replay;
`LocalProcessTransport` behavioural equivalence with the current `Popen` path.

## 11. Deployment and documentation

`k8s/extras/opensandbox/`: `RuntimeClass` for `gvisor` and `kata-qemu`;
per-tier server `Deployment`/`Service`/`ConfigMap` (`sandbox.toml` with
`[secure_runtime]` and `[egress] mode = "dns+nft"`); a sandbox pod template
carrying `runAsNonRoot`, `readOnlyRootFilesystem`, dropped capabilities,
`allowPrivilegeEscalation: false`, seccomp `RuntimeDefault` and a dedicated
`ServiceAccount` — the things the §4 attestations promise; a cluster
`NetworkPolicy` blocking the metadata address and RFC1918; kubelet
`podPidsLimit` guidance; and `image-policy.yaml` enforcing cosign signature and
provenance against `image_signer_identity`.

`docs/sandbox-backends.md`: operator guide (runsc / Kata install, tier config,
key management, the attestation checklist and what each one must be true for,
rollout by tenant/project), the fail-closed reason-code catalogue, and the
Legacy/gVisor/Kata performance-cost-compatibility report — every number labelled
with its provenance, upstream-published or measured. Plus an explicit security
note: inside its own sandbox the agent can reach execd as root, so the
guarantees are about the control plane, other tenants and GitHub credentials,
not about privilege inside the blast radius.
