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
  "default_tier": "gvisor",
  "endpoints": {
    "gvisor": {
      "base_url": "http://opensandbox.open-ace.svc.cluster.local:8080/v1",
      "api_key_env": "OPENSANDBOX_API_KEY_GVISOR",
      "execd_token_env": "OPENSANDBOX_EXECD_TOKEN_GVISOR",
      "runtime_class": "gvisor",
      "default_image": "ghcr.io/open-ace/agent@sha256:<64 hex>",
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
      }
    }
  },
  "tenant_tiers": {"42": "kata"},
  "production_required_tenants": ["42"],
  "image_allowlist": ["ghcr.io/open-ace/agent@sha256:<64 hex>"],
  "resource_defaults": {"cpu": "2", "memory": "4Gi", "ephemeral-storage": "8Gi"},
  "sandbox_ttl_seconds": 3600
}
```

Points worth knowing before you edit it:

- **Tenant keys are `str(tenant_id)`**, the integer this codebase carries — not
  a slug. There is no name→id mapping anywhere, so a slug key would match
  nothing.
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

## 4. Rollout

1. Deploy the gVisor tier only. Leave `tenant_tiers` and
   `production_required_tenants` empty — every tenant still resolves to the
   default tier, so this is already live for everyone; roll back by removing the
   config file.
2. To pilot narrowly instead, use `project_tiers` to route a single repository
   path.
3. Add the Kata tier and move high-security tenants onto it with
   `tenant_tiers`.
4. Add those tenants to `production_required_tenants` once you want a missing
   backend to be an error rather than a downgrade.

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
trusted repository. Commit and push stay control-plane side.

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
