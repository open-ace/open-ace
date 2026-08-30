# Sandbox backends

Open ACE runs autonomous coding agents. Where those agents execute — and what
stops them from reaching anything they should not — is chosen per tenant and per
project by the **sandbox backend**.

Three backends exist:

| Backend | Isolation | When to use it |
| --- | --- | --- |
| `legacy_posix` | per-task HOME/TMP/XDG, filesystem ACLs, cgroup quotas, all on the host | single-tenant, fully trusted repositories and contributors |
| `remote_machine` | none that the control plane can verify | an operator-managed remote machine that is itself the trust boundary |
| `opensandbox` | container + gVisor or Kata, deny-default egress, no host filesystem at all | multi-tenant, untrusted repositories/PRs/dependencies, or any compliance requirement |

This document covers `opensandbox`. See `docs/TEST_LAYERS.md` for how its tests
are laid out and `k8s/extras/opensandbox/README.md` for the manifests.

---

## 1. What it is

[OpenSandbox](https://github.com/opensandbox-group/OpenSandbox) (Apache-2.0,
CNCF landscape) is a sandbox runtime for AI agents. It owns the Kubernetes and
secure-container layer; Open ACE talks to it over two REST surfaces and never
touches the Kubernetes API itself.

```
control plane                    OpenSandbox server            sandbox pod
─────────────                    ──────────────────            ───────────
OpenSandboxProvider  ──/v1──▶    lifecycle API        ──▶      gVisor / Kata
        │                                                       ├── execd :44772
        └──────────── execd + PTY WebSocket ────────────────────┤   ├── /command
                                                                │   ├── /files
                                                                │   └── /pty/ws
                                                                └── egress sidecar :18080
```

The coding-agent CLI runs *inside* the pod, driven over execd's PTY WebSocket in
pipe mode — that is what supplies the interactive stdin its
`--input-format stream-json` protocol needs.

---

## 2. Prerequisites

**Nodes that schedule sandbox pods**

- gVisor: `runsc` and `containerd-shim-runsc-v1`
- Kata: `kata-containers`, hardware virtualization (VT-x / AMD-V), KVM, kernel ≥ 5.10
- kubelet: `podPidsLimit: 512` (see §5 — this is the only real fork-bomb defence)

**Cluster**

```bash
kubectl apply -k k8s/extras/opensandbox/
kubectl get runtimeclass          # expect: gvisor, kata-qemu
```

---

## 3. Configuration

`/etc/openace/sandbox-backends.json` (or `$OPENACE_SANDBOX_BACKENDS`, or
`~/.open-ace/sandbox-backends.json`, in that precedence).

```json
{
  "installation_id": "openace-prod-sg",
  "default_tier": "gvisor",
  "endpoints": {
    "gvisor": {
      "base_url": "http://opensandbox.open-ace.svc.cluster.local:8080/v1",
      "api_key_env": "OPENSANDBOX_API_KEY_GVISOR",
      "execd_token_env": "OPENSANDBOX_EXECD_TOKEN_GVISOR",
      "runtime_class": "gvisor",
      "default_image": "ghcr.io/open-ace/agent@sha256:<64 hex>",
      "execd_endpoint_host_allowlist": ["opensandbox-gateway.open-ace.example"],
      "egress_allow_hosts": [
        "openace.open-ace.svc.cluster.local",
        "api.anthropic.com",
        "*.githubusercontent.com"
      ],
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
      }
    }
  },
  "rollout": {"mode": "allowlist", "tenants": ["42"], "projects": []},
  "production_required_tenants": ["42"],
  "image_allowlist": ["ghcr.io/open-ace/agent@sha256:<64 hex>"],
  "resource_defaults": {"cpu": "2", "memory": "4Gi", "ephemeral-storage": "8Gi"},
  "sandbox_ttl_seconds": 3600
}
```

Points worth knowing before you edit it:

- **The agent's LLM proxy must be egress-allowlisted and cluster-reachable.**
  Egress is deny-default and the proxy is the one host a run cannot work
  without, so its hostname has to appear in that tier's `egress_allow_hosts`.
  It also must not be a loopback address: the control plane's `server_url`
  defaults to `http://localhost:<port>`, which inside the sandbox pod resolves
  to the sandbox itself. The provider refuses the turn in both cases rather
  than letting the agent hang on every request.
- **`execd_endpoint_host_allowlist` must name the GATEWAY host.** Under gateway
  ingress the server hands back the gateway's address, not a per-sandbox cluster
  name, and the client refuses any execd URL whose host is not on this list. It
  therefore has to match `ingress.gateway.address` in the server ConfigMap; a
  `*.svc.cluster.local` entry left over from direct ingress refuses every call.
- **`installation_id` is required and must be unique per deployment.** It is
  stamped on every sandbox's metadata, and orphan reconciliation destroys every
  sandbox carrying our provider tag that no local workflow row claims. Two
  Open ACE installations sharing one lifecycle server with the same tag (or
  none) would each classify the other's live sandboxes as unclaimed and delete
  them mid-run. Keep it stable across restarts — changing it strands the
  sandboxes created under the old value.
- **Tenant keys are `str(tenant_id)`**, the integer this codebase carries — not
  a slug. There is no name→id mapping anywhere, so a slug key would match
  nothing.
- **`rollout` decides Legacy vs OpenSandbox; `tenant_tiers` decides gVisor vs
  Kata.** They are different questions — `tenant_tiers` cannot route a tenant
  back to Legacy, because every tier is an OpenSandbox endpoint.
- **`production_required_tenants` is the no-downgrade list.** A tenant on it
  gets OpenSandbox or an exception; there is no path from "required" to Legacy.
- **An explicitly requested config path that does not exist raises.** It does
  not fall back to the system file — falling back would silently return "no
  backend", which means Legacy.
- **Secrets are named, never stored.** `api_key_env` / `execd_token_env` hold
  environment-variable *names*.
- **No config at all is a valid state.** The local path then behaves exactly as
  it did before this backend existed.

---

## 4. Choosing Legacy or OpenSandbox

Two settings decide this, and they answer different questions.

**`rollout` — may this task use the backend?**

```json
"rollout": {
  "mode": "allowlist",
  "tenants": ["42"],
  "projects": ["/srv/repos/pilot"]
}
```

- `mode: "all"` (the default) — every task on this deployment uses OpenSandbox.
- `mode: "allowlist"` — only the listed tenants and project paths use it.
  **Everything else runs on Legacy**, unchanged.

Tenant keys are `str(tenant_id)`; project keys are absolute paths matched
exactly. A task matching either list is in.

**`production_required_tenants` — must it?**

A tenant on this list gets OpenSandbox or an exception; it can never fall back
to Legacy. This is the stronger statement, and the two must agree: a tenant that
is *required* but excluded from the rollout is rejected at config load rather
than letting one setting quietly win.

With no config file at all, everything runs on Legacy — behaviourally identical
to before this backend existed. That is also the rollback: remove the file.

### A suggested sequence

1. Deploy the gVisor tier with `rollout.mode = "allowlist"` and a single project
   path. One repository moves; nothing else changes.
2. Widen `rollout.tenants` a tenant at a time.
3. Add the Kata tier and route high-security tenants to it with `tenant_tiers`
   (this picks *which* tier, not *whether* to use one).
4. Add those tenants to `production_required_tenants` once a missing backend
   should be an error rather than a downgrade.
5. Switch to `rollout.mode = "all"` when the backend is the default everywhere.

---

## 5. What is enforced, and by what

Every capability the provider declares maps to a mechanism you can point at. The
ones that are *not* claimed matter as much as the ones that are.

| Capability | Enforced by |
| --- | --- |
| `NAMESPACE_ISOLATION` | the gVisor/Kata runtime class, **verified** by a `/proc/version` probe on the first sandbox per endpoint |
| `NETWORK_EGRESS_POLICY` | egress sidecar `deny_all` in `dns+nft` mode, **verified** by probing its `/policy`, plus the cluster NetworkPolicy |
| `FILESYSTEM_ACL` | pod `securityContext`: non-root, read-only rootfs, dropped capabilities, seccomp `RuntimeDefault` |
| `CPU_MEM_PIDS_TIME_QUOTA` | `resourceLimits` cpu/memory via kubelet, `podPidsLimit` for pids, sandbox TTL for wall clock |
| `PRIVATE_HOME_TMP_XDG` | a fresh container per sandbox with `HOME`/`TMPDIR`/`XDG_*` set explicitly |
| `CREDENTIAL_TOKEN_BINDING` | the environment is *constructed*, never inherited; no GitHub write credential ever enters |
| `STORAGE_INODE_QUOTA` | **off by default** — see below |

### Two claims deliberately not made

**Inode quotas.** `ulimit -f` caps one file's size; a Kubernetes
`ephemeral-storage` limit is enforced by kubelet eviction polling and has no
inode dimension. Neither bounds inode count, so `inode_quota_enforced` defaults
to `false` and a task requesting an inode limit fails closed rather than running
under a guarantee nothing provides.

**Privilege inside the sandbox.** Every command execd runs inherits execd's own
environment — including its access token — and `POST /command` accepts a
caller-supplied `uid: 0`. An agent inside the sandbox can therefore reach execd
and obtain root **within its own sandbox**. Nothing in this backend claims
otherwise, and no capability rests on an in-sandbox mechanism such as a `ulimit`
prefix.

What the backend does guarantee is the blast radius: the agent cannot reach the
control plane's credentials, another tenant's sandbox, the host filesystem, or a
GitHub write token. Isolation between sandboxes and from the host is enforced by
gVisor/Kata and the pod security context, neither of which the agent can affect.

---

## 6. Fail-closed reason codes

Every refusal carries a machine-readable code, on the exception and in the audit
event.

| Reason code | Meaning | What to do |
| --- | --- | --- |
| `pool_not_attested` | warm pool requested without all of `egress_preapplied`, `recycle_delete`, `image_digest` | pool mode bypasses the image allowlist, resource limits and egress policy; attest all three or stop using it |
| `runtime_class_mismatch` | the sandbox kernel is not the declared runtime | the server's `[secure_runtime]` and the tier's `runtime_class` disagree, or the RuntimeClass is missing on the node |
| `egress_not_deny_default` | sidecar reports `allow` | check `[egress]` in the tier's ConfigMap |
| `egress_mode_insufficient` | sidecar reports `dns`, not `dns+nft` | DNS-only cannot stop a bare-IP connection; set `mode = "dns+nft"` |
| `spec_refused` | the request could not be built (image, volumes, egress, pids) | the message names the field |
| `stale_generation` | a handle from before a reconciliation bump | benign; the workflow will re-create |
| `destroy_unconfirmed` | teardown was issued but never observed terminal | the reconciler retries; check server health |
| `not_an_agent_turn` | `get_transport` on a plain command | internal — an agent turn needs an `OpenSandboxTurnSpec` |
| `command_too_long` | assembled env + argv exceeds `MAX_ARG_STRLEN` | trim the environment |
| `pty_stream_lost` | the PTY socket dropped without an exit frame | reported as a crash, never a completion — see §7 |
| `workspace_setup_failed` | the repo synthesis command failed inside the sandbox | usually `git` missing from the image, or `/workspace` not writable |
| `manifest_producer_failed` / `manifest_missing` | the ChangeSet producer failed or left no output | usually `python3` missing from the image |
| `pause_unconfirmed` / `resume_unconfirmed` | the sandbox never reported the expected state | the request was accepted but the transition did not complete; check server health |
| `invalid_snapshot` | `upload_workspace` was given something other than a worktree path | internal |
| `sandbox_unavailable` | a refusal reached the agent runner | the message carries the underlying reason code |

ChangeSet rejections use their own set: `absolute_path`, `path_escape`,
`repo_integrity`, `symlink_escape`, `file_too_large`, `too_many_files`,
`total_too_large`, `unsafe_mode`, `secret_path`.

---

## 7. Known limitations

**A dropped PTY socket ends the turn.** Reconnecting is not implemented, and
that is deliberate rather than pending. Re-attaching to a finished session makes
execd start a *new* shell — a second agent process, not a resumed view of the
first. Replay arrives channel-merged and cannot be split back into stdout and
stderr, so feeding it to the stream-json parser would corrupt it. And
`GET /pty/{id}` carries no exit code, so a missed exit frame is unrecoverable.
A dropped socket is therefore terminal, reported as a structured crash.

**Warm pools bypass several guarantees.** Upstream rejects `image`,
`resourceLimits`, `networkPolicy` and `volumes` alongside `poolRef`, so those
come from the Pool CRD, which the provider cannot read. Pool mode requires three
explicit attestations and is refused without them.

**The workspace is a synthesised repository.** The agent gets `git init` plus
one commit of the snapshot — no remote, no credential helper, no link to the
trusted repository. Commit and push stay control-plane side. `HOME` is at
`/home/agent`, deliberately outside `/workspace`, so the agent's caches never
enter that repository.

**The image must provide `git`, `python3`, and the agent CLI on `PATH`.** The
provider runs the repo synthesis and the ChangeSet manifest producer inside the
sandbox; both fail closed with a structured reason code if the binaries are
absent. The agent CLI (`claude`, `qwen`, …) is invoked by **name**, not by the
path the control plane resolved it to — the host's `shutil.which` result has no
meaning inside the image — so the image's own `PATH` must find it.

**The image must contain the configured `runtime_user` / `runtime_group`.**
execd chowns every uploaded file to them, and looks the name up inside the
container — so a user that does not exist there fails the upload with
`500 error chmoding file ...: failed to lookup user <name>`. Since
`upload_workspace` is the first thing any run does, the whole run dies there.
The defaults are `openace`/`openace`; either add that user to your agent image
or set both fields to one it already has. Verified against a real execd.

**The control plane must also have the agent CLI installed.** `_run_local`
resolves the executable on the host before selecting a provider, and returns
`CLI tool '<name>' not found` if it is missing — even for a run that would
execute entirely inside a container. A control plane that never runs agents
locally therefore cannot yet use this backend. Tracked as follow-up work;
restructuring command construction around provider selection is out of scope
for #2023.

**Gateway endpoints are plain HTTP unless you terminate TLS yourself.** The
server returns a bare host and the client defaults to `http://`. Under `direct`
that traffic was cluster-internal; under `gateway` the workspace snapshot and
the per-sandbox credential traverse whatever path reaches
`ingress.gateway.address`. Nothing in `k8s/extras/opensandbox/` provides TLS —
terminate it at your ingress, or keep the gateway address on a network you
trust. Treat this as a deployment requirement, not a nicety.

**Gateway ingress is required, not optional.** `secureAccess` — the per-sandbox
credential that stops one sandbox reaching another's execd — is honoured by
upstream only for Kubernetes sandboxes under `[ingress] mode = "gateway"`.
`k8s/extras/opensandbox/` configures gateway mode, `[ingress.gateway]`, and the
`OPENSANDBOX_SECURE_ACCESS_*` signing keys accordingly. **You must set
`ingress.gateway.address` for your own deployment** (a wildcard domain, no
scheme). A tier that cannot attest `secure_access_required` is refused at
`create()` rather than run with the peer boundary open — under `direct` every
sandbox shares one static `EXECD_ACCESS_TOKEN` that any agent can read from
execd's environment, which #2023's `test_sandbox_cannot_read_host_or_peer_workspace`
exists to forbid.

**The BatchSandbox CRD and its controller are a prerequisite.** The server is
configured with `workload_provider = "batchsandbox"`, but the CRD and the
controller that reconciles those objects come from upstream's
`opensandbox-controller` Helm chart, which this kustomization deliberately does
not vendor. Install and pin it *before* applying these manifests, or the first
sandbox create is accepted and never reconciled. See the README.

**Orphan reconciliation is per-workflow-row only.** `reconcile_orphans()`, the
metadata-scoped sweep of the whole lifecycle server, has no production caller;
teardown happens through `destroy_attribution` on rows the database already
knows about. Attribution is now persisted the moment `create()` returns an id,
so the crash window that could strand an unnameable sandbox is closed — but a
sandbox whose workflow row is lost entirely is still reclaimed by its TTL rather
than by Open ACE.

**Multi-turn `--resume` does not carry session history.** Each turn gets a fresh
sandbox with an empty `HOME`, so a `--resume` on turn 2 finds no local session
state from turn 1. The prompt and the workspace carry over; the CLI's own
session cache does not.

---

## 8. Backend comparison

Sources are labelled. Nothing here is an unattributed number.

### Startup and isolation overhead

| Runtime | Isolation | Startup overhead | Memory overhead |
| --- | --- | --- | --- |
| runc | process cgroups | ~0 ms | minimal |
| gVisor | user-space kernel, syscall interception | ~10–50 ms | ~50 MB |
| Kata (QEMU) | full VM | ~500 ms | ~20–50 MB |
| Kata (Firecracker) | microVM | ~125 ms | ~5 MB |

*Source: OpenSandbox `docs/guides/secure-container.md`, upstream-published.
Not measured by this project.*

### Lifecycle phases

| Phase | `legacy_posix` | `opensandbox` |
| --- | --- | --- |
| Cold start | none — the process is spawned directly | image pull, then the runtime overhead above |
| Sandbox create | `fork`/`exec` | one `POST /v1/sandboxes`, synchronous |
| Workspace transfer | none — the worktree is already local | one upload per file, plus repo synthesis |
| Exec | local `Popen` | PTY WebSocket, or `POST /command` |
| Collect changes | local git | manifest download plus control-plane validation |
| Destroy | process-group signal | `DELETE`, polled to terminal |

*Provenance: structural, derived from the implementation. **No wall-clock
figures are given for these phases, because this project has not measured them
on a cluster.** Populate this table from your own deployment before using it for
capacity planning; the metrics the issue asks for are emitted as audit events on
every lifecycle call.*

### Compatibility

| Concern | gVisor | Kata |
| --- | --- | --- |
| Syscall coverage | a documented subset; unusual syscalls may fail | full Linux kernel |
| Hardware requirement | none | VT-x / AMD-V + KVM |
| Density | high | lower — a VM per sandbox |
| Typical use | default for all tenants | high-security tenants |

*Provenance: upstream guide plus the runtime projects' own documentation.*

### Cost

Cost is dominated by node capacity, which follows the memory overhead above and
your sandbox concurrency. Kata's per-sandbox VM makes density the deciding
factor; gVisor's overhead is close enough to runc that the practical difference
is scheduling, not footprint. *No dollar figures are given: they depend entirely
on your cluster and provider.*

---

## 9. Troubleshooting

**Every execd call returns 401.** `execd_token_env` is unset or names an empty
variable. Note that execd's auth middleware short-circuits on an empty token, so
a server started without `EXECD_ACCESS_TOKEN` accepts anonymous calls — which is
why `execd_token_required` is mandatory.

**`runtime_class_mismatch` on the first sandbox.** The server's
`[secure_runtime]` does not match the tier's `runtime_class`, or the
RuntimeClass is not installed on the node that scheduled the pod.

**The agent cannot edit its own files.** Check `runtime_user`/`runtime_group`.
execd may run as root, and root-owned files under a restrictive mode are
unwritable by the non-root agent.

**The orphan sweep destroyed nothing after a restart.** Confirm the workflow row
carries `sandbox_provider = "opensandbox"` and a `sandbox_id`; the sweep keys off
both.

**Sandboxes accumulate on a shared server.** The sweep filters on
`openace.provider` metadata and only destroys what the control plane no longer
claims. Sandboxes created by other systems are never touched — that is
intentional.
