# Sandbox backends — 沙箱后端

[English](#english) | [中文](#中文)

---

## English

Open ACE runs autonomous coding agents. Where those agents execute — and what
stops them from reaching anything they should not — is chosen per tenant and per
project by the **sandbox backend**.

Three backends exist:

| Backend | Isolation | When to use it |
| --- | --- | --- |
| `legacy_posix` | per-task HOME/TMP/XDG, filesystem ACLs, cgroup quotas, all on the host | single-tenant, fully trusted repositories and contributors |
| `remote_machine` | none that the control plane can verify | an operator-managed remote machine that is itself the trust boundary |
| `opensandbox` | container + gVisor or Kata, deny-default egress, no host filesystem at all | multi-tenant, untrusted repositories/PRs/dependencies, or any compliance requirement |

This document covers `opensandbox`. See `TEST_LAYERS.md` for how its tests
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

- A CNI that **enforces** `NetworkPolicy` (Calico, Cilium, and most managed
  offerings do; kind's default `kindnet` does not — it accepts the objects and
  ignores them). Every tier's egress rests on it, and on a gVisor tier it is the
  only egress control there is. The provider's boot probe refuses with
  `egress_cni_not_enforced` if it is not in force.

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
  "default_tier": "kata",
  "endpoints": {
    "kata": {
      "base_url": "http://opensandbox-kata.open-ace.svc.cluster.local:8080/v1",
      "api_key_env": "OPENSANDBOX_API_KEY_KATA",
      "execd_token_env": "OPENSANDBOX_EXECD_TOKEN_KATA",
      "runtime_class": "kata-qemu",
      "default_image": "ghcr.io/open-ace/agent@sha256:<64 hex>",
      "webui_image": "ghcr.io/open-ace/webui@sha256:<64 hex>",
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
        "execd_runs_as_exec_identity": true,
        "secure_access_required": true,
        "nonroot_enforced": true,
        "readonly_rootfs": true,
        "seccomp_runtime_default": true,
        "dedicated_service_account": true,
        "pod_pids_limit": 512,
        "ephemeral_storage_enforced": true,
        "inode_quota_enforced": false
      }
    },
    "gvisor": {
      "base_url": "http://opensandbox.open-ace.svc.cluster.local:8080/v1",
      "api_key_env": "OPENSANDBOX_API_KEY_GVISOR",
      "execd_token_env": "OPENSANDBOX_EXECD_TOKEN_GVISOR",
      "runtime_class": "gvisor",
      "default_image": "ghcr.io/open-ace/agent@sha256:<64 hex>",
      "execd_endpoint_host_allowlist": ["opensandbox-gateway.open-ace.example"],
      "egress_allow_hosts": [],
      "attestations": {
        "egress_cni_default_deny": true,
        "metadata_cidr_blocked": true,
        "execd_token_required": true,
        "execd_runs_as_exec_identity": true,
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
  "rollout": {"mode": "allowlist", "tenants": ["42"], "projects": []},
  "production_required_tenants": ["42"],
  "image_allowlist": ["ghcr.io/open-ace/agent@sha256:<64 hex>"],
  "resource_defaults": {"cpu": "2", "memory": "4Gi", "ephemeral-storage": "8Gi"},
  "sandbox_ttl_seconds": 3600
}
```

Points worth knowing before you edit it:

- **Every endpoint attests exactly one egress mechanism, and they are not
  equivalent.** `egress_enforced` is the OpenSandbox egress sidecar: per-sandbox,
  deny-default, FQDN allowlist — and impossible under gVisor, whose netstack has
  no iptables nat table for the sidecar's DNS redirect.
  `egress_cni_default_deny` is the cluster NetworkPolicy on its own: one static
  CIDR rule for every sandbox, denying the metadata service and all private
  ranges while leaving the public internet open. A tier attesting neither is
  refused at config load; a tier attesting both is too, because the second flag
  would contradict the first. Only the sidecar mechanism yields
  `network_egress_policy`, so the two tiers are genuinely different products —
  see §5 and §7.
- **The agent's LLM proxy must be cluster-reachable, and on a sidecar tier
  egress-allowlisted.** The proxy is the one host a run cannot work without. On
  a sidecar tier its hostname has to appear in that tier's `egress_allow_hosts`
  (which must be empty on a CNI tier, where nothing would enforce it). On either
  tier it must not be a loopback address — the control plane's `server_url`
  defaults to `http://localhost:<port>`, which inside the sandbox pod resolves
  to the sandbox itself — and on a CNI tier it must not be a private address or
  a cluster-internal name, both of which that NetworkPolicy denies. The provider
  refuses the turn in each case rather than letting the agent hang on every
  request.
- **`execd_endpoint_host_allowlist` must name the GATEWAY host.** Under gateway
  ingress the server hands back the gateway's address, not a per-sandbox cluster
  name, and the client refuses any execd URL whose host is not on this list. It
  therefore has to match `ingress.gateway.address` in the server ConfigMap; a
  `*.svc.cluster.local` entry left over from direct ingress refuses every call.
- **`webui_image` is optional, and validated by the capability probe — not by the
  parser.** Interactive sandboxed WebUI pods (#3378, see §8) launch from
  `endpoints.<tier>.webui_image` when that key is set. It parses as a plain
  optional string on purpose: the digest-pinned + `image_allowlist` checks happen
  in the isolation-capability probe, so a bad value degrades only the `sandboxed`
  level (with `webui_image_not_pinned` / `webui_image_not_allowed` reasons) and
  never breaks the shared backend config that autonomous tasks depend on. The
  same discipline as `default_image` applies operationally — digest-pinned and
  allowlisted — it is just enforced at a different layer. A reference build lives
  at `scripts/docker/webui-sandbox.Dockerfile`.
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
- **`rollout` decides Legacy vs OpenSandbox; `tenant_tiers` decides which
  endpoint.** Both tiers run agent workloads; what differs is the egress
  guarantee (see §7). They are different questions — `tenant_tiers` cannot route
  a tenant back to Legacy, because every tier is an OpenSandbox endpoint.
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

1. Deploy one tier with `rollout.mode = "allowlist"` and a single project path.
   One repository moves; nothing else changes. Choose gVisor for lower startup
   cost, Kata if you need the FQDN egress allowlist — gVisor cannot run the
   egress sidecar and enforces only the coarse cluster NetworkPolicy (§7).
2. Widen `rollout.tenants` a tenant at a time.
3. If you want separate isolation domains — a dedicated node pool, a different
   image allowlist, an FQDN egress allowlist — add a *second* tier and route
   high-security tenants to it with `tenant_tiers` (this picks *which* tier, not
   *whether* to use one). Skip this step if one tier is enough.
4. Add those tenants to `production_required_tenants` once a missing backend
   should be an error rather than a downgrade.
5. Switch to `rollout.mode = "all"` when the backend is the default everywhere.

---

## 5. What is enforced, and by what

Every capability the provider declares maps to a mechanism you can point at. The
ones that are *not* claimed matter as much as the ones that are.

| Capability | Enforced by |
| --- | --- |
| `NAMESPACE_ISOLATION` | the declared runtime class, checked by a `/proc/version` probe on the first sandbox per endpoint. **The check is one-directional**: a gVisor claim is positively verified (its kernel identifies itself), a Kata claim is only confirmed *not* gVisor — Kata's guest kernel is indistinguishable from an unisolated runc container's, so this cannot prove Kata is in force. Treat the Kata runtime class as an operator attestation backed by `[secure_runtime] k8s_runtime_class` and the RuntimeClass existing on the node. |
| `NETWORK_EGRESS_POLICY` | egress sidecar `deny_all` in `dns+nft` mode, **verified** by probing its `/policy`, plus the cluster NetworkPolicy. **Sidecar tiers only.** A CNI tier (`egress_cni_default_deny` — the only mechanism gVisor can run) still enforces egress, but with one static CIDR rule for every sandbox and no FQDN allowlist, so it does not declare this capability and a spec requiring it fails closed there. Both mechanisms rest on the cluster NetworkPolicy, which the provider verifies from inside the first sandbox by confirming the metadata service and the Kubernetes API server are unreachable. |
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
| `runtime_class_mismatch` | the sandbox kernel contradicts the declared runtime (raised only when a gVisor kernel is seen; see §5 on the one-directional check) | the server's `[secure_runtime]` and the tier's `runtime_class` disagree, or the RuntimeClass is missing on the node |
| `egress_not_deny_default` | sidecar reports `allow` | check `[egress]` in the tier's ConfigMap |
| `egress_mode_insufficient` | sidecar reports `dns`, not `dns+nft` | DNS-only cannot stop a bare-IP connection; set `mode = "dns+nft"` |
| `egress_cni_not_enforced` | the sandbox reached the metadata service or the Kubernetes API server | the cluster NetworkPolicy is not restricting this pod: apply `networkpolicy.yaml`, check its `podSelector` matches the sandbox pod labels, and check your service CIDR falls inside one of its excluded ranges |
| `egress_probe_unavailable` | the cluster-egress probe produced no verdict | it needs `python3` on `PATH` in the sandbox image; an unverifiable attestation is refused rather than trusted |
| `agent_state_unavailable` | a resuming session line on a provider that cannot carry the CLI transcript, or a stored transcript that exists but cannot be read | raised **before** the sandbox is created, so a turn that could not have resumed costs nothing; check `OPENACE_AGENT_STATE_ROOT` is writable |
| `spec_refused` | the request could not be built (image, volumes, egress, pids) | the message names the field |
| `stale_generation` | a handle from before a reconciliation bump | benign; the workflow will re-create |
| `destroy_unconfirmed` | teardown was issued but never observed terminal | the reconciler retries; check server health |
| `not_an_agent_turn` | `get_transport` on a plain command | internal — an agent turn needs an `OpenSandboxTurnSpec` |
| `command_too_long` *(planned)* | assembled env + argv exceeds `MAX_ARG_STRLEN`; currently raised as a plain `SandboxError` without a reason code | trim the environment |
| `pty_stream_lost` | the PTY socket dropped without an exit frame | reported as a crash, never a completion — see §7 |
| `workspace_setup_failed` | the repo synthesis command failed inside the sandbox | usually `git` missing from the image, or `/workspace` not writable |
| `manifest_producer_failed` / `manifest_missing` | the ChangeSet producer failed or left no output | usually `python3` missing from the image |
| `pause_unconfirmed` / `resume_unconfirmed` | the sandbox never reported the expected state | the request was accepted but the transition did not complete; check server health |
| `invalid_snapshot` | `upload_workspace` was given something other than a worktree path | internal |
| `sandbox_unavailable` | a refusal reached the agent runner | the message carries the underlying reason code |

ChangeSet rejections use their own set: `absolute_path`, `path_escape`,
`repo_integrity`, `symlink_escape`, `file_too_large`, `too_many_files`,
`total_too_large`, `unsafe_mode`, `secret_path`.

The sandboxed interactive WebUI launcher (#3378, §8) adds two runtime codes of
its own — `sandbox_create_failed` and `sandbox_endpoint_unresolved` — plus a
zero-pod probe vocabulary (`webui_image_*`, `sandbox_proxy_*`,
`sandbox_runtime_*`) documented in
`../contracts/WORKSPACE_ISOLATION_CAPABILITIES.md` §3.4.

---

## 7. Known limitations

**`pause` / `resume` do not converge on Kubernetes.** Observed twice on a real
cluster, on two independent stacks: `pause` is accepted, the sandbox stays
`Running`, the provider reports `pause_unconfirmed` — correctly, rather than
claiming a pause that did not happen — and the following `resume` is then
rejected with `409 Cannot resume sandbox in state Running, expected Paused`.
Upstream's pause depends on a container freezer that the tested clusters did not
supply. Treat these two calls as **unsupported on the Kubernetes runtime** until
verified on a stack where the freezer works; nothing in the autonomous workflow
calls them today. The refusal is honest either way, so the failure mode is a
rejected request, never a sandbox believed to be paused while it keeps running.

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

**gVisor's egress control is coarser than Kata's, and the difference is real.**
The egress sidecar redirects DNS through the iptables nat table, which gVisor's
netstack does not implement. A real server logs the incompatibility at startup
and then answers every create carrying a `networkPolicy` with
`networkPolicy is not compatible with runtime 'gvisor': ... Use a compatible
runtime (e.g. kata) or remove networkPolicy.` Found by running a real server;
every prior review read the shipped gVisor tier as working, when in fact it
could not create a single sandbox.

A gVisor tier therefore takes upstream's own remedy: the provider omits
`networkPolicy` entirely and egress is enforced one layer down, by the cluster
`NetworkPolicy` in `k8s/extras/opensandbox/networkpolicy.yaml`, which the CNI
applies outside the sandbox kernel where the missing nat table is irrelevant.
Such a tier attests `egress_cni_default_deny` instead of `egress_enforced`
(`parse_backend_config` refuses the sidecar under gVisor, and refuses an
endpoint attesting neither mechanism or both).

**What you give up, precisely.** The cluster policy is CIDR-based and identical
for every sandbox. It denies the instance metadata service, the cluster's own
pod and service ranges, and every private network — the provider verifies that
from inside the first sandbox rather than trusting the attestation — but it
leaves **the whole public internet reachable**. There is no FQDN allowlist and
no per-sandbox variation, so:

- a gVisor tier does not declare `network_egress_policy`, and the effective-policy
  snapshot on the workflow row records its absence;
- a spec carrying its own `network_egress` is refused there rather than run
  under a policy the tier cannot honour;
- `egress_allow_hosts` must be empty for such a tier, because nothing would
  enforce it.

Choose Kata when the allowlist itself is the control you need — an agent that
can reach any public host can exfiltrate to any public host. Choose gVisor when
its lower startup cost matters more and the CIDR boundary is enough. Both tiers
supply `namespace_isolation` identically.

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
than by Open ACE. Should this sweep ever gain a production caller, it MUST keep
the interactive-WebUI exclusion described in §8 — without it the first sweep
destroys every live user WebUI pod.

**Multi-turn `--resume` carries the CLI transcript, and nothing else.** Each
turn gets a fresh sandbox with an empty `HOME`, so the transcript `--resume`
reads is exported before the sandbox is destroyed and imported into the next
one (#3237). Exactly one file moves, under each tool's own layout —
`$HOME/.claude/projects/-workspace/<id>.jsonl` for claude-code,
`$HOME/.qwen/projects/-workspace/chats/<id>.jsonl` for qwen-code-cli (#3319) —
never `.claude.json`, `.credentials.json` or settings: the sandbox environment
is constructed, never inherited, and a credential must not round-trip through
the control plane. A real CLI confirms that this one file is sufficient for
`--resume` to resolve, with the original session id preserved.

**Both stream-json tools carry (#3319).** claude-code and qwen-code-cli both
emit their `session_id` on stdout, so the runner captures it during the turn,
persists it to `agent_sessions.cli_session_id`, and the next milestone's
`_resolve_session_line` maps the line's tracking id to it — resuming the real
CLI session, not the tracking id. A resuming turn for any *other* tool (a future
stream-json CLI with no provider transcript path) is still *refused* with
`agent_state_unavailable` before the sandbox is created, rather than allowed to
start cold and silently lose its history.

No other tool is affected, because no other tool reaches this path. ZCode
speaks its own app-server protocol and the single-shot tools (codex, openclaw)
have no stdin protocol, so both return from `_run_local` before a sandbox
provider is selected and spawn a local process instead — there is no ephemeral
`HOME` for them to carry anything across.

Transcripts rest on the control plane under `OPENACE_AGENT_STATE_ROOT`
(default: alongside the per-task runtime directories), keyed by the session
line's stable tracking id, and are purged when the workflow reaches a terminal
state. That default is on `/run`, which is tmpfs — point the override at
persistent storage if you want transcripts to survive a reboot.

> **Deployment requirement: the state root must be shared by every process
> that runs a workflow, and by the web role.**
>
> This is not a tuning knob. Two independent facts make it a correctness
> requirement rather than a preference:
>
> * **The web role purges what the scheduler role writes.** `stop_workflow`,
>   the acceptance override, and both delete routes drop a workflow's
>   transcripts, and they run in the web process — while the transcripts are
>   written by the scheduler process. On split storage those purges delete an
>   unrelated empty directory and the real transcripts are retained
>   indefinitely, because nothing can identify a deleted workflow afterwards.
> * **The autonomous scheduler is not leader-gated.** `_run_loop` polls on
>   every replica and arbitrates per workflow with a database lock, so
>   ownership of a workflow legitimately moves between replicas across
>   milestones. On per-replica storage, turn N's transcript is invisible to
>   whichever replica takes turn N+1, and the resume silently starts cold —
>   the exact failure this feature exists to remove.
>
> The shipped manifests configure this: `k8s/deployment.yaml` and
> `k8s/scheduler-deployment.yaml` both mount the RWX `open-ace-data` claim at
> `/var/lib/openace/agent-state` (subPath `agent-state`) and set
> `OPENACE_AGENT_STATE_ROOT` to it, and `docker-compose.yml` shares one
> `agent-state` volume between the `open-ace` and `scheduler` services. RWX is
> already required by this deployment (see `k8s/storage.yaml`), so this adds a
> shared *location*, not a new storage class.
>
> A single-process deployment (one systemd unit running both roles) needs
> nothing extra.

A line whose stored transcript is **absent** — its first turn, or a control
plane restart that cleared tmpfs — simply starts a fresh session. That is not a
failure. A line on a provider that cannot carry state at all, or one whose
stored transcript exists but cannot be **read**, is refused with
`agent_state_unavailable` *before* the sandbox is created, so a turn that could
not have resumed spends no tokens. The three cases differ on purpose: the same
split `scripts/openace-run-as.sh` makes between its fail-closed capture
(`exit 70`), its log-only exit-trap capture, and its best-effort restore.

---

## 8. Interactive workspaces (the `sandboxed` isolation level)

Since #3378 this backend also hosts the **interactive workspace**: a deployment
can run each user's qwen-code-webui in its own OpenSandbox pod instead of as a
local process under an OS account. The browser reaches the pod through a local
per-instance port proxy in the web process; identity is a per-instance token
secret, not a host uid. The capability contract — probe reason codes, the
dimension table, the TTL chain, and the honest-declaration list (configuration-
plane vs per-pod verification, crash-loss windows, snapshot ceiling) — lives in
`../contracts/WORKSPACE_ISOLATION_CAPABILITIES.md` §6. This section covers what touches
*this* backend file and the web process that drives it.

**Configuration.** One key in this file, plus two in config.json:

- `endpoints.<tier>.webui_image` — the pod image; see the §3 bullet. Setting it
  is what makes the `sandboxed` level probe-able at all, and a passing probe
  flips the deployment's default launch form to sandboxed.
- config.json `workspace.webui_callback_url` — **required**: the static probe
  uses it as the URL the pod reaches the control-plane LLM proxy through. On a
  sidecar tier the control plane's hostname must be in that tier's
  `egress_allow_hosts`; on a CNI tier it must be publicly reachable (loopback,
  private, and cluster-internal addresses are refused at probe time).
- config.json `workspace.sandbox_tier` — optional; which endpoint tier
  interactive pods launch on, defaulting to the backend's `default_tier`.

**Web-process environment:**

- `OPENACE_WEBUI_ORPHAN_RECONCILE=1` — positive trigger for the WebUI-pod orphan
  reconcile. Set **only** by the web service entrypoint (docker-entrypoint.sh's
  gunicorn path — explicitly *not* the scheduler container — and server.py's dev
  `__main__`). Management scripts import `create_app` without setting it, so
  they can never sweep; a test process is guarded off (`PYTEST_VERSION` /
  `TESTING`) and the sweep itself is fail-soft (a corrupt sandbox-backends.json
  logs and is skipped rather than breaking web boot).
- `OPENACE_WEBUI_STATE_ROOT` — snapshot root for WebUI session history, default
  `<CONFIG_DIR>/webui-agent-state` (i.e. `~/.open-ace/webui-agent-state`), one
  `webui-<user_id>.tar` per user. Implementation note: the design plan placed
  this *alongside* the config directory; the shipped default is *inside* it,
  because CONFIG_DIR is the directory Docker deployments mount for persistence —
  a sibling would be container-local and lose every snapshot on container
  recreation. The reaper never scans this root.
- `OPENACE_WEBUI_STATE_MAX_BYTES` — per-snapshot ceiling, default 16 MiB. Over
  the ceiling the export is skipped with a WARNING and the user's history stays
  frozen at the last good snapshot (see the capabilities doc §6.4).

> **Load-bearing exclusion contract (N7): `reconcile_orphans` must never claim
> WebUI pods.**
>
> Interactive WebUI pods carry `openace.webui.kind=webui` metadata and are NOT
> workflow-bound — no workflow row will ever appear to claim them, so a
> `reconcile_orphans()` sweep without the kind exclusion classifies every live
> user WebUI pod as an orphan and **destroys all of them on its first run**.
> The exclusion is implemented in `provider.reconcile_orphans` (metadata filter
> + client-side re-check) and bound by tests. Anyone wiring that sweep to a new
> production caller MUST keep the exclusion; WebUI pods are reclaimed by the web
> process's own generation-keyed reconcile (`app/services/webui_sandbox.py`),
> not by the workflow live-set.

**Single web process assumption.** The webui-pod reconcile destroys WebUI pods
whose `openace.webui.process_generation` metadata is not the current web
process's — which is only a sound discriminator when exactly one web process
owns the installation. The shipped compose deployment (one `open-ace` app
container) satisfies it; **multiple web replicas or a rolling deploy with
old/new overlap are not supported** and would destroy each other's pods.

---

## 9. Backend comparison

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
| Egress enforcement | cluster NetworkPolicy only: CIDR, static, public internet open | egress sidecar: per-sandbox FQDN allowlist, deny-default |
| Declares `network_egress_policy` | no | yes |
| Typical use | default for all tenants | tenants whose egress must be allowlisted |

*Provenance: the first four rows are the upstream guide plus the runtime
projects' own documentation. The egress rows are this repository's own
behaviour — see §7 — and the gVisor limitation was found by running a real
server, not read from a document.*

**No performance number in this section was measured by this project** — the
overhead figures above are upstream's. That is separate from whether the backend
*works*, which has been tested; here is exactly what has and has not been run
against real infrastructure:

- **The gVisor tier's full lifecycle has been run end to end on a real cluster**
  under `runsc`: create → kernel probe → CNI probe → upload and git synthesis →
  foreground exec with SSE → PTY agent turn → evidence → `collect_changes` →
  `apply_changes` → destroy. That run is what surfaced the wire-level defects a
  green test suite had hidden — file mode encoding, SSE framing, execd's identity
  model, the `networkPolicy` incompatibility, the `/proc/version` probe's false
  refusal, a NetworkPolicy whose podSelector matched no pod, git's
  `dubious ownership` on a root-owned `/workspace`, and a command timeout sent
  in the wrong unit.
- **The CNI egress mechanism has been verified in both directions on a
  policy-enforcing CNI** (Calico): without the manifest the boot probe reads the
  API server as reachable and refuses with `egress_cni_not_enforced`; with it
  applied, both legs read blocked and the run proceeds. Creating a sandbox with
  no `networkPolicy` against a gVisor-configured server is likewise confirmed
  accepted.
- **Kata has never been exercised at all**: it needs `/dev/kvm`, and the
  attempt to stand one up reached nested VT-x and `kata-deploy` before failing
  on a guest kernel with no `vhost_net` module. Every Kata statement in this
  document is therefore design intent, not measurement.
- One piece of the `dubious ownership` fix — the global `safe.directory` that
  covers git commands **the agent itself** runs, as opposed to the repo
  synthesis — was verified locally with git's `GIT_TEST_ASSUME_DIFFERENT_OWNER`
  hook rather than on a cluster.

**Your CNI must actually enforce NetworkPolicy.** Several common development
CNIs (kind's default `kindnet` among them) accept `NetworkPolicy` objects and
ignore them. Under a sidecar tier that only weakens `metadata_cidr_blocked`;
under a CNI tier it means there is no egress control whatsoever. This is why the
boot probe exists and why it fails closed: a cluster whose CNI ignores the
policy is refused with `egress_cni_not_enforced` at the first sandbox rather
than running agents with open egress.

### Cost

Cost is dominated by node capacity, which follows the memory overhead above and
your sandbox concurrency. Kata's per-sandbox VM makes density the deciding
factor; gVisor's overhead is close enough to runc that the practical difference
is scheduling, not footprint. *No dollar figures are given: they depend entirely
on your cluster and provider.*

---

## 10. Troubleshooting

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

---

## 中文

Open ACE 运行自主编码 agent。这些 agent 在哪里执行——以及是什么阻止它们触达
不该触达的任何东西——由**沙箱后端（sandbox backend）**按租户（tenant）、按项目
选定。

现有三种后端：

| 后端 | 隔离方式 | 何时使用 |
| --- | --- | --- |
| `legacy_posix` | 按任务的 HOME/TMP/XDG、文件系统 ACL、cgroup 配额，全部在宿主机上 | 单租户、完全受信任的仓库与贡献者 |
| `remote_machine` | 没有控制平面能够验证的隔离 | 由运维管理的远程机器，机器本身就是信任边界 |
| `opensandbox` | 容器 + gVisor 或 Kata、默认拒绝出站、完全不接触宿主文件系统 | 多租户、不受信任的仓库/PR/依赖，或任何合规要求 |

本文档介绍 `opensandbox`。其测试如何分层见 `TEST_LAYERS.md`，
清单文件见 `k8s/extras/opensandbox/README.md`。

---

## 1. 它是什么

[OpenSandbox](https://github.com/opensandbox-group/OpenSandbox)（Apache-2.0，
CNCF landscape）是一个面向 AI agent 的沙箱运行时。它负责 Kubernetes 与
安全容器这一层；Open ACE 通过两个 REST 接口面与它通信，从不自行接触
Kubernetes API。

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

编码 agent CLI 运行在 pod *内部*，通过 execd 的 PTY WebSocket 以 pipe 模式
驱动——正是它提供了该 CLI 的 `--input-format stream-json` 协议所需的交互式
stdin。

---

## 2. 前置条件

**调度沙箱 pod 的节点**

- gVisor：`runsc` 与 `containerd-shim-runsc-v1`
- Kata：`kata-containers`、硬件虚拟化（VT-x / AMD-V）、KVM、内核 ≥ 5.10
- kubelet：`podPidsLimit: 512`（见 §5——这是唯一真正的 fork 炸弹防御）

**集群**

- 一个**真正强制执行** `NetworkPolicy` 的 CNI（Calico、Cilium 以及多数托管
  方案都可以；kind 默认的 `kindnet` 不行——它接受这些对象却忽略它们）。每个
  tier 的出站控制都依赖它，而在 gVisor tier 上它是唯一存在的出站控制。若它
  未生效，provider 的启动探测会以 `egress_cni_not_enforced` 拒绝。

```bash
kubectl apply -k k8s/extras/opensandbox/
kubectl get runtimeclass          # expect: gvisor, kata-qemu
```

---

## 3. 配置

`/etc/openace/sandbox-backends.json`（或 `$OPENACE_SANDBOX_BACKENDS`，或
`~/.open-ace/sandbox-backends.json`，优先级依次如此）。

```json
{
  "installation_id": "openace-prod-sg",
  "default_tier": "kata",
  "endpoints": {
    "kata": {
      "base_url": "http://opensandbox-kata.open-ace.svc.cluster.local:8080/v1",
      "api_key_env": "OPENSANDBOX_API_KEY_KATA",
      "execd_token_env": "OPENSANDBOX_EXECD_TOKEN_KATA",
      "runtime_class": "kata-qemu",
      "default_image": "ghcr.io/open-ace/agent@sha256:<64 hex>",
      "webui_image": "ghcr.io/open-ace/webui@sha256:<64 hex>",
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
        "execd_runs_as_exec_identity": true,
        "secure_access_required": true,
        "nonroot_enforced": true,
        "readonly_rootfs": true,
        "seccomp_runtime_default": true,
        "dedicated_service_account": true,
        "pod_pids_limit": 512,
        "ephemeral_storage_enforced": true,
        "inode_quota_enforced": false
      }
    },
    "gvisor": {
      "base_url": "http://opensandbox.open-ace.svc.cluster.local:8080/v1",
      "api_key_env": "OPENSANDBOX_API_KEY_GVISOR",
      "execd_token_env": "OPENSANDBOX_EXECD_TOKEN_GVISOR",
      "runtime_class": "gvisor",
      "default_image": "ghcr.io/open-ace/agent@sha256:<64 hex>",
      "execd_endpoint_host_allowlist": ["opensandbox-gateway.open-ace.example"],
      "egress_allow_hosts": [],
      "attestations": {
        "egress_cni_default_deny": true,
        "metadata_cidr_blocked": true,
        "execd_token_required": true,
        "execd_runs_as_exec_identity": true,
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
  "rollout": {"mode": "allowlist", "tenants": ["42"], "projects": []},
  "production_required_tenants": ["42"],
  "image_allowlist": ["ghcr.io/open-ace/agent@sha256:<64 hex>"],
  "resource_defaults": {"cpu": "2", "memory": "4Gi", "ephemeral-storage": "8Gi"},
  "sandbox_ttl_seconds": 3600
}
```

编辑它之前值得了解的要点：

- **每个端点恰好佐证（attest）一种出站机制，且两者并不等价。**
  `egress_enforced` 是 OpenSandbox 出站 sidecar：按沙箱、默认拒绝、FQDN
  白名单——而在 gVisor 下不可能实现，因为其 netstack 没有 iptables nat 表
  供 sidecar 做 DNS 重定向。`egress_cni_default_deny` 则是集群
  NetworkPolicy 独立生效：对所有沙箱只有一条静态 CIDR 规则，拒绝元数据服务
  和全部私有网段，但放行整个公网。既不佐证其中任何一种机制的 tier 在配置
  加载时即被拒绝；同时佐证两者的也一样被拒绝，因为第二个标志会与第一个
  矛盾。只有 sidecar 机制能产出 `network_egress_policy`，所以这两个 tier 是
  真正不同的产品——见 §5 与 §7。
- **agent 的 LLM 代理必须从集群可达，且在 sidecar tier 上必须列入出站
  白名单。** 该代理是任何一次运行都离不开的唯一主机。在 sidecar tier 上，
  其主机名必须出现在该 tier 的 `egress_allow_hosts` 中（在 CNI tier 上该
  列表必须为空，因为没有任何东西会强制执行它）。在任一 tier 上它都不能是
  回环地址——控制平面的 `server_url` 默认为 `http://localhost:<port>`，在
  沙箱 pod 内会解析到沙箱自身——而在 CNI tier 上它也不能是私有地址或集群
  内部名称，这两者都会被该 NetworkPolicy 拒绝。每种情形下 provider 都会
  拒绝该轮次，而不是放任 agent 在每个请求上挂起。
- **`execd_endpoint_host_allowlist` 必须填写 GATEWAY（网关）主机。** 在
  gateway 入口模式下，server 返回的是网关地址而非按沙箱的集群名称，客户端
  会拒绝任何 host 不在此列表中的 execd URL。因此它必须与 server ConfigMap
  中的 `ingress.gateway.address` 匹配；直连入口时代遗留的
  `*.svc.cluster.local` 条目会让每次调用都被拒绝。
- **`webui_image` 可选，由能力探针校验——而不是解析器。** 交互式沙箱
  WebUI pod（#3378，见 §8）在设置了 `endpoints.<tier>.webui_image` 时从该
  镜像启动。它被刻意解析为普通的可选字符串：摘要锁定 + `image_allowlist`
  检查发生在隔离能力探针中，因此一个坏值只会降低 `sandboxed` 级别（附带
  `webui_image_not_pinned` / `webui_image_not_allowed` 原因），绝不会破坏
  自主任务所依赖的共享后端配置。操作上它与 `default_image` 遵循同样的
  纪律——摘要锁定且在白名单内——只是强制执行发生在另一层。参考构建位于
  `scripts/docker/webui-sandbox.Dockerfile`。
- **`installation_id` 必填，且每个部署唯一。** 它被盖在每个沙箱的元数据上，
  孤儿对账会销毁所有带有我们的 provider 标签但没有本地 workflow 行认领的
  沙箱。两个 Open ACE 安装若共享同一个生命周期 server 且标签相同（或都没有
  标签），会把对方存活中的沙箱判定为无人认领并在运行中删除。跨重启保持它
  稳定——改变它会使旧值下创建的沙箱变成无人认领的孤儿。
- **租户键是 `str(tenant_id)`**，即本代码库所携带的整数——不是 slug。任何
  地方都没有 name→id 映射，所以 slug 键什么也匹配不到。
- **`rollout` 决定 Legacy 还是 OpenSandbox；`tenant_tiers` 决定用哪个
  endpoint。** 两个 tier 都运行 agent 工作负载；差别在出站保证（见 §7）。
  这是两个不同的问题——`tenant_tiers` 无法把租户路由回 Legacy，因为每个
  tier 都是 OpenSandbox 端点。
- **`production_required_tenants` 是禁止降级名单。** 名单上的租户要么获得
  OpenSandbox 要么走例外；不存在从 "required" 回到 Legacy 的路径。
- **显式请求的配置路径若不存在则抛异常。** 它不会回退到系统文件——回退会
  静默返回"无后端"，也就是 Legacy。
- **秘密按名引用，从不存储。** `api_key_env` / `execd_token_env` 存的是
  环境变量*名*。
- **完全没有配置也是一种合法状态。** 此时本地路径的行为与该后端出现之前
  完全一致。

---

## 4. 选择 Legacy 还是 OpenSandbox

两个设置决定这件事，而它们回答的是不同的问题。

**`rollout`——这个任务可以用该后端吗？**

```json
"rollout": {
  "mode": "allowlist",
  "tenants": ["42"],
  "projects": ["/srv/repos/pilot"]
}
```

- `mode: "all"`（默认）——此部署上的每个任务都使用 OpenSandbox。
- `mode: "allowlist"`——只有列出的租户和项目路径使用它。**其余一切都运行
  在 Legacy 上**，保持不变。

租户键是 `str(tenant_id)`；项目键是精确匹配的绝对路径。匹配任一列表的
任务即算入选。

**`production_required_tenants`——必须用吗？**

名单上的租户要么获得 OpenSandbox 要么走例外；它永远不能回退到 Legacy。
这是更强的声明，且两者必须一致：一个*被 required* 却被排除在 rollout
之外的租户会在配置加载时被拒绝，而不是让一个设置悄悄压过另一个。

完全没有配置文件时，一切都运行在 Legacy 上——行为上与该后端出现之前完全
相同。这也就是回滚方式：删掉该文件。

### 建议的推进顺序

1. 以 `rollout.mode = "allowlist"` 和单个项目路径部署一个 tier。一个仓库
   迁移，其余一切不变。选择 gVisor 可降低启动成本，若需要 FQDN 出站白名单
   则选 Kata——gVisor 无法运行出站 sidecar，只有粗粒度的集群
   NetworkPolicy（§7）。
2. 一次放宽一个租户地扩大 `rollout.tenants`。
3. 若需要相互独立的隔离域——专用节点池、不同的镜像白名单、FQDN 出站
   白名单——增加*第二个* tier，并用 `tenant_tiers` 把高安全租户路由过去
   （这决定的是*哪个* tier，而不是*是否*使用）。一个 tier 够用就跳过此步。
4. 当后端缺失应当是错误而非降级时，把这些租户加入
   `production_required_tenants`。
5. 当后端在所有地方都是默认时，切换到 `rollout.mode = "all"`。

---

## 5. 强制执行了什么，由什么执行

provider 声明的每个能力都对应一个你可以指认的机制。*未*被声明的与被声明
的同等重要。

| 能力 | 强制执行者 |
| --- | --- |
| `NAMESPACE_ISOLATION` | 所声明的 runtime class，通过每个端点的首个沙箱上的 `/proc/version` 探测检查。**该检查是单向的**：gVisor 的声明会被正面验证（其内核会自我标识），而 Kata 的声明只能确认*不是* gVisor——Kata 的客户机内核与未隔离的 runc 容器内核无法区分，因此无法证明 Kata 生效。应将 Kata runtime class 视为一条运维佐证，由 `[secure_runtime] k8s_runtime_class` 与节点上存在该 RuntimeClass 支撑。 |
| `NETWORK_EGRESS_POLICY` | 出站 sidecar 在 `dns+nft` 模式下的 `deny_all`，通过探测其 `/policy` 加以**验证**，外加集群 NetworkPolicy。**仅限 sidecar tier。** CNI tier（`egress_cni_default_deny`——gVisor 唯一能运行的机制）依然强制出站，但对每个沙箱只有一条静态 CIDR 规则且无 FQDN 白名单，因此它不声明此能力，要求此能力的 spec 在那里 fail-closed。两种机制都依赖集群 NetworkPolicy，provider 会在首个沙箱内部通过确认元数据服务与 Kubernetes API server 不可达来验证它。 |
| `FILESYSTEM_ACL` | pod `securityContext`：非 root、只读 rootfs、丢弃 capabilities、seccomp `RuntimeDefault` |
| `CPU_MEM_PIDS_TIME_QUOTA` | 经 kubelet 的 `resourceLimits` cpu/memory、管 pids 的 `podPidsLimit`、管墙上时钟的沙箱 TTL |
| `PRIVATE_HOME_TMP_XDG` | 每个沙箱一个新容器，显式设置 `HOME`/`TMPDIR`/`XDG_*` |
| `CREDENTIAL_TOKEN_BINDING` | 环境是*构造*出来的，从不继承；任何 GitHub 写凭据都不会进入 |
| `STORAGE_INODE_QUOTA` | **默认关闭**——见下文 |

### 两个刻意不做的声明

**Inode 配额。** `ulimit -f` 限制单个文件的大小；Kubernetes 的
`ephemeral-storage` 限制由 kubelet 驱逐轮询执行，没有 inode 维度。两者都
不约束 inode 数量，因此 `inode_quota_enforced` 默认为 `false`，请求 inode
上限的任务会 fail-closed，而不是在一个没有任何东西提供相应保证的环境里
运行。

**沙箱内部的特权。** execd 运行的每条命令都会继承 execd 自身的环境——
包括其访问令牌——而 `POST /command` 接受调用方提供的 `uid: 0`。沙箱内的
agent 因此可以触达 execd 并在**自己的沙箱内**获得 root。本后端的任何部分
都不主张相反的情形，也没有任何能力依赖于沙箱内机制（例如 `ulimit` 前缀）。

后端真正保证的是爆炸半径：agent 无法触达控制平面的凭据、其他租户的沙箱、
宿主文件系统或 GitHub 写令牌。沙箱之间以及与宿主机之间的隔离由
gVisor/Kata 和 pod 安全上下文强制执行，agent 对这两者都无从影响。

---

## 6. Fail-closed reason codes

每个拒绝都携带一个机器可读的 code，出现在异常与审计事件中。

| Reason code | 含义 | 应对 |
| --- | --- | --- |
| `pool_not_attested` | 请求了 warm pool 但未同时佐证 `egress_preapplied`、`recycle_delete`、`image_digest` 全部三项 | pool 模式绕过镜像白名单、资源限制和出站策略；把三项全部佐证，或停用该模式 |
| `runtime_class_mismatch` | 沙箱内核与所声明的 runtime 矛盾（仅在观察到 gVisor 内核时抛出；单向检查见 §5） | server 的 `[secure_runtime]` 与 tier 的 `runtime_class` 不一致，或节点上缺少该 RuntimeClass |
| `egress_not_deny_default` | sidecar 报告 `allow` | 检查该 tier ConfigMap 中的 `[egress]` |
| `egress_mode_insufficient` | sidecar 报告 `dns` 而非 `dns+nft` | 仅 DNS 无法阻止裸 IP 连接；设置 `mode = "dns+nft"` |
| `egress_cni_not_enforced` | 沙箱触达了元数据服务或 Kubernetes API server | 集群 NetworkPolicy 没有约束这个 pod：apply `networkpolicy.yaml`，检查其 `podSelector` 与沙箱 pod 标签匹配，并确认你的 service CIDR 落在它的某个排除网段内 |
| `egress_probe_unavailable` | 集群出站探测没有得出结论 | 探测需要沙箱镜像的 `PATH` 上有 `python3`；无法验证的佐证会被拒绝而不是被信任 |
| `agent_state_unavailable` | 恢复会话行落在无法携带 CLI 会话记录（transcript）的 provider 上，或已存的会话记录存在但无法读取 | 在沙箱创建**之前**抛出，因此不可能恢复的轮次不花任何成本；检查 `OPENACE_AGENT_STATE_ROOT` 可写 |
| `spec_refused` | 请求无法构建（镜像、卷、出站、pids） | 消息会指名字段 |
| `stale_generation` | 一次对账版本号之前的旧句柄 | 良性；workflow 会重新创建 |
| `destroy_unconfirmed` | 已下发销毁但从未观察到终态 | 对账器会重试；检查 server 健康 |
| `not_an_agent_turn` | 在普通命令上调用 `get_transport` | 内部——agent 轮次需要 `OpenSandboxTurnSpec` |
| `command_too_long` *(计划中)* | 组装出的 env + argv 超过 `MAX_ARG_STRLEN`；目前作为不带 reason code 的普通 `SandboxError` 抛出 | 精简环境 |
| `pty_stream_lost` | PTY 套接字在没有退出帧的情况下断开 | 报告为 crash，绝不报告为完成——见 §7 |
| `workspace_setup_failed` | 仓库合成命令在沙箱内失败 | 通常是镜像缺 `git`，或 `/workspace` 不可写 |
| `manifest_producer_failed` / `manifest_missing` | ChangeSet 生产器失败或没有产出 | 通常是镜像缺 `python3` |
| `pause_unconfirmed` / `resume_unconfirmed` | 沙箱从未报告期望的状态 | 请求被接受但状态迁移未完成；检查 server 健康 |
| `invalid_snapshot` | 传给 `upload_workspace` 的不是 worktree 路径 | 内部 |
| `sandbox_unavailable` | 一个拒绝到达了 agent runner | 消息携带底层 reason code |

ChangeSet 拒绝使用独立的一组：`absolute_path`、`path_escape`、
`repo_integrity`、`symlink_escape`、`file_too_large`、`too_many_files`、
`total_too_large`、`unsafe_mode`、`secret_path`。

沙箱化的交互式 WebUI 启动器（#3378，§8）另有两个自己的运行时
code——`sandbox_create_failed` 与 `sandbox_endpoint_unresolved`——外加一套
零 pod 探测词汇（`webui_image_*`、`sandbox_proxy_*`、`sandbox_runtime_*`），
见 `../contracts/WORKSPACE_ISOLATION_CAPABILITIES.md` §3.4。

---

## 7. 已知限制

**`pause` / `resume` 在 Kubernetes 上不收敛。** 在真实集群上观察到两次，
跨越两套独立技术栈：`pause` 被接受，沙箱保持 `Running`，provider 报告
`pause_unconfirmed`——这是正确的行为，而不是谎称发生了一次并未发生的
暂停——随后的 `resume` 被以 `409 Cannot resume sandbox in state Running,
expected Paused` 拒绝。上游的 pause 依赖容器 freezer，而被测试的集群并未
提供。在 freezer 可用的技术栈上验证之前，应把这两个调用视为**在
Kubernetes runtime 上不受支持**；今天自主工作流中没有任何地方调用它们。
无论哪种情况拒绝都是诚实的，所以失败模式是被拒绝的请求，而不是一个被
相信已暂停却仍在运行的沙箱。

**PTY 套接字断开即终结该轮次。** 重连未实现，而且这是刻意为之而非待办。
重新附着到一个已结束的会话会让 execd 启动一个*新* shell——是第二个 agent
进程，而不是第一个的恢复视图。回放（replay）是按通道合并到达的，无法再
拆回 stdout 和 stderr，把它喂给 stream-json 解析器会损坏数据。而
`GET /pty/{id}` 不带退出码，所以错过的退出帧无法恢复。因此断开的套接字是
终结性的，报告为结构化 crash。

**Warm pool 绕过若干保证。** 上游在 `poolRef` 旁边拒绝 `image`、
`resourceLimits`、`networkPolicy` 与 `volumes`，所以这些都来自 Pool CRD，
而 provider 无法读取它。pool 模式要求三项显式佐证，缺一即被拒绝。

**工作区是一个合成仓库。** agent 拿到的是 `git init` 加上快照的一个
提交——没有 remote、没有凭据 helper、没有与受信任仓库的关联。commit 和
push 留在控制平面侧。`HOME` 位于 `/home/agent`，刻意在 `/workspace` 之外，
因此 agent 的缓存永远不会进入那个仓库。

**镜像必须在 `PATH` 上提供 `git`、`python3` 和 agent CLI。** provider 在
沙箱内运行仓库合成和 ChangeSet 清单生产器；二进制缺失时二者都会以结构化
reason code fail-closed。agent CLI（`claude`、`qwen`……）按**名字**调用，
而不是按控制平面解析到的路径——宿主机上的 `shutil.which` 结果在镜像内
没有意义——所以镜像自己的 `PATH` 必须能找到它。

**镜像必须包含所配置的 `runtime_user` / `runtime_group`。** execd 会把每个
上传的文件 chown 给它们，并在容器内部查找这个名字——因此一个在那里不存在
的用户会让上传以 `500 error chmoding file ...: failed to lookup user
<name>` 失败。由于 `upload_workspace` 是任何一次运行的第一步，整个运行就
死在那里。默认值是 `openace`/`openace`；要么把该用户加进你的 agent 镜像，
要么把两个字段设置为镜像已有的用户。已针对真实 execd 验证。

**控制平面也必须安装 agent CLI。** `_run_local` 在选择 provider 之前就在
宿主机上解析可执行文件，缺失时返回 `CLI tool '<name>' not found`——即使
本次运行将完全在容器内执行也一样。因此一个从不在本地运行 agent 的控制
平面目前无法使用该后端。已列为后续工作；围绕 provider 选择重构命令构建
不在 #2023 范围内。

**Gateway 端点是明文 HTTP，除非你自行终结 TLS。** server 返回裸
主机名，客户端默认 `http://`。`direct` 模式下这些流量是集群内部的；
`gateway` 模式下，工作区快照和按沙箱凭据会穿过通往
`ingress.gateway.address` 的任意路径。`k8s/extras/opensandbox/` 中没有
任何东西提供 TLS——在你的 ingress 终结它，或把网关地址放在你信任的网络
里。把这当作部署要求，而不是可选项。

**gVisor 的出站控制比 Kata 粗，且差别是实质性的。** 出站 sidecar 通过
iptables nat 表重定向 DNS，而 gVisor 的 netstack 不实现它。真实 server
会在启动时记录这一不兼容，然后对所有携带 `networkPolicy` 的 create 一律
回答 `networkPolicy is not compatible with runtime 'gvisor': ... Use a
compatible runtime (e.g. kata) or remove networkPolicy.` 这是运行真实
server 发现的；此前每次评审都把交付的 gVisor tier 当作可用，而事实上它
连一个沙箱都创建不出来。

于是 gVisor tier 采用上游自己的补救：provider 完全省略 `networkPolicy`，
出站由下一层强制执行——即 `k8s/extras/opensandbox/networkpolicy.yaml` 中的
集群 `NetworkPolicy`，由 CNI 在沙箱内核之外施加，缺失的 nat 表在那里
无关紧要。这样的 tier 佐证 `egress_cni_default_deny` 而非 `egress_enforced`
（`parse_backend_config` 在 gVisor 下拒绝 sidecar，也拒绝既不佐证任一机制
又佐证两者的端点）。

**你精确放弃了什么。** 集群策略基于 CIDR 且对所有沙箱相同。它拒绝实例
元数据服务、集群自身的 pod 与 service 网段以及所有私有网络——provider 在
首个沙箱内部验证这一点，而不是信任佐证——但它让**整个公网可达**。没有
FQDN 白名单，也没有按沙箱的变化，因此：

- gVisor tier 不声明 `network_egress_policy`，workflow 行上的有效策略快照
  会记录其缺失；
- 自带 `network_egress` 的 spec 在那里被拒绝，而不是运行在该 tier 无法
  兑现的策略之下；
- 该类 tier 的 `egress_allow_hosts` 必须为空，因为没有东西会强制执行它。

当你需要的控制恰恰是白名单本身时选 Kata——一个能触达任何公网主机的
agent 就能向任何公网主机外传数据。当 gVisor 更低的启动成本更重要且 CIDR
边界足够时选 gVisor。两个 tier 提供的 `namespace_isolation` 完全相同。

**Gateway 入口是必需项，不是可选项。** `secureAccess`——阻止一个沙箱
触达另一个沙箱的 execd 的按沙箱凭据——上游只在 `[ingress] mode =
"gateway"` 的 Kubernetes 沙箱上支持。`k8s/extras/opensandbox/` 相应地
配置了 gateway 模式、`[ingress.gateway]` 和 `OPENSANDBOX_SECURE_ACCESS_*`
签名密钥。**你必须为自己的部署设置 `ingress.gateway.address`**（一个通配
域名，不带 scheme）。无法佐证 `secure_access_required` 的 tier 会在
`create()` 被拒绝，而不是带着敞开的对等边界运行——在 `direct` 下所有沙箱
共享一个静态 `EXECD_ACCESS_TOKEN`，任何 agent 都能从 execd 的环境里读到
它，而 #2023 的 `test_sandbox_cannot_read_host_or_peer_workspace` 正是为了
禁止这一点。

**BatchSandbox CRD 及其 controller 是前置条件。** server 配置了
`workload_provider = "batchsandbox"`，但该 CRD 与对账这些对象的 controller
来自上游的 `opensandbox-controller` Helm chart，而这份 kustomization 刻意
不打包它。先安装并锁定版本，*然后*再 apply 这些清单，否则第一个沙箱
create 会被接受却永远不被对账。见 README。

**孤儿对账仅按 workflow 行进行。** `reconcile_orphans()`——对整个生命周期
server 的元数据范围清扫——没有生产调用方；销毁通过数据库已知行上的
`destroy_attribution` 进行。归因现在在 `create()` 返回 id 的那一刻即被
持久化，因此可能搁置一个无法命名的沙箱的崩溃窗口已经关闭——但一个
workflow 行完全丢失的沙箱仍由其 TTL 而非 Open ACE 回收。若该清扫将来
获得生产调用方，它必须（MUST）保留 §8 描述的交互式 WebUI 排除逻辑——
没有它，第一次清扫就会销毁所有存活的用户 WebUI pod。

**多轮 `--resume` 只携带 CLI 会话记录，别无其他。** 每一轮次拿到的是带
空 `HOME` 的新沙箱，因此 `--resume` 读取的会话记录会在沙箱销毁前导出并
导入下一个沙箱（#3237）。恰好移动一个文件，按各工具自己的布局——
claude-code 是 `$HOME/.claude/projects/-workspace/<id>.jsonl`，qwen-code-cli
是 `$HOME/.qwen/projects/-workspace/chats/<id>.jsonl`（#3319）——绝不是
`.claude.json`、`.credentials.json` 或设置文件：沙箱环境是构造的而非继承
的，凭据不得往返控制平面。真实 CLI 证实这一个文件足以让 `--resume` 完成
解析，且保留原始会话 id。

**两个 stream-json 工具都携带（#3319）。** claude-code 与 qwen-code-cli 都
在 stdout 上输出 `session_id`，runner 在轮次中捕获它，持久化到
`agent_sessions.cli_session_id`，下一个里程碑的 `_resolve_session_line`
把该行的 tracking id 映射到它——恢复的是真实 CLI 会话，而不是 tracking
id。任何*其他*工具（一个未来没有 provider 会话记录路径的 stream-json
CLI）的恢复轮次仍然以 `agent_state_unavailable` 在沙箱创建之前被*拒绝*，
而不是被允许冷启动并静默丢失历史。

其他工具不受影响，因为没有其他工具走到这条路径。ZCode 说自己的
app-server 协议，而单发工具（codex、openclaw）没有 stdin 协议，二者都在
选择沙箱 provider 之前就从 `_run_local` 返回并改为派生本地进程——没有
临时 `HOME` 供它们携带任何东西。

会话记录位于控制平面的 `OPENACE_AGENT_STATE_ROOT`（默认：与按任务的运行
时目录并排）之下，以会话行的稳定 tracking id 为键，并在 workflow 到达
终态时清除。该默认值在 `/run` 上，是 tmpfs——若希望会话记录在重启后
存活，请把覆盖指向持久存储。

> **部署要求：状态根必须被每个运行 workflow 的进程以及 web 角色共享。**
>
> 这不是调优旋钮。两个独立事实使它成为正确性要求而非偏好：
>
> * **web 角色清除 scheduler 角色写入的内容。** `stop_workflow`、验收
>   覆盖和两条删除路由都会删除一个 workflow 的会话记录，而它们运行在
>   web 进程中——会话记录却由 scheduler 进程写入。在分离存储上这些清除
>   只会删掉一个不相干的空目录，而真实会话记录被无限期保留，因为事后
>   没有任何东西能识别一个已删除的 workflow。
> * **自主 scheduler 不受 leader 门控。** `_run_loop` 在每个副本上轮询并
>   按 workflow 用数据库锁仲裁，因此 workflow 的所有权跨里程碑在副本
>   之间移动是正当的。在每副本存储上，第 N 轮的会话记录对拿到第 N+1 轮
>   的副本不可见，恢复会静默冷启动——正是该特性要消除的故障。
>
> 交付的清单已配置好：`k8s/deployment.yaml` 与
> `k8s/scheduler-deployment.yaml` 都把 RWX 的 `open-ace-data` claim 挂载到
> `/var/lib/openace/agent-state`（subPath `agent-state`）并把
> `OPENACE_AGENT_STATE_ROOT` 指向它，`docker-compose.yml` 在 `open-ace` 与
> `scheduler` 服务之间共享一个 `agent-state` 卷。RWX 本就是该部署所要求的
> （见 `k8s/storage.yaml`），所以这里增加的是共享*位置*，而不是新的存储
> 类。
>
> 单进程部署（一个 systemd unit 同时运行两个角色）无需任何额外配置。

一条其已存会话记录**缺失**的行——首轮，或一次清空了 tmpfs 的控制平面
重启——只是开启一个新会话。那不是故障。一条位于完全无法携带状态的
provider 上的行，或一条已存会话记录存在却无法**读取**的行，会以
`agent_state_unavailable` 在沙箱创建*之前*被拒绝，因此不可能恢复的轮次
不花任何 token。三种情形刻意不同：正如 `scripts/openace-run-as.sh` 在其
fail-closed 捕获（`exit 70`）、仅记日志的 exit-trap 捕获与尽力恢复之间
所作的区分。

---

## 8. 交互式工作区（`sandboxed` 隔离级别）

自 #3378 起该后端还承载**交互式工作区**：部署可以把每个用户的
qwen-code-webui 运行在它自己的 OpenSandbox pod 中，而不是作为 OS 账户下
的本地进程。浏览器通过 web 进程中的本地按实例端口代理触达 pod；身份是
一个按实例的令牌 secret，而不是宿主 uid。能力契约——探测 reason code、
维度表、TTL 链以及诚实声明清单（配置平面 vs 按 pod 验证、crash 丢失
窗口、快照上限）——位于 `../contracts/WORKSPACE_ISOLATION_CAPABILITIES.md`
§6。本节只讲涉及*这个*后端文件的内容，以及驱动它的 web 进程。

**配置。** 本文件中的一个键，加 config.json 中的两个：

- `endpoints.<tier>.webui_image`——pod 镜像；见 §3 的对应条目。设置它才使
  `sandboxed` 级别可被探测，而探测通过会把部署的默认启动形态切换为沙箱
  化。
- config.json 的 `workspace.webui_callback_url`——**必填**：静态探测用它
  作为 pod 触达控制平面 LLM 代理的 URL。在 sidecar tier 上控制平面的
  主机名必须在该 tier 的 `egress_allow_hosts` 中；在 CNI tier 上它必须
  公网可达（回环、私有和集群内部地址在探测时被拒绝）。
- config.json 的 `workspace.sandbox_tier`——可选；交互式 pod 启动在哪个
  端点 tier 上，默认为后端的 `default_tier`。

**Web 进程环境：**

- `OPENACE_WEBUI_ORPHAN_RECONCILE=1`——WebUI pod 孤儿对账的正向触发器。
  只由 web 服务入口设置（docker-entrypoint.sh 的 gunicorn 路径——明确
  *不*包括 scheduler 容器——以及 server.py 的 dev `__main__`）。管理脚本
  导入 `create_app` 时不设置它，所以它们永远不能清扫；测试进程被守卫排除
  （`PYTEST_VERSION` / `TESTING`），清扫本身是 fail-soft 的（损坏的
  sandbox-backends.json 会被记录并跳过，而不是破坏 web 启动）。
- `OPENACE_WEBUI_STATE_ROOT`——WebUI 会话历史的快照根，默认
  `<CONFIG_DIR>/webui-agent-state`（即 `~/.open-ace/webui-agent-state`），
  每用户一个 `webui-<user_id>.tar`。实现说明：设计计划把它放在配置目录
  *旁边*；交付的默认值在它*里面*，因为 CONFIG_DIR 是 Docker 部署为持久化
  挂载的目录——一个兄弟目录会是容器本地的，容器重建时会丢掉所有快照。
  回收器从不扫描这个根。
- `OPENACE_WEBUI_STATE_MAX_BYTES`——单快照上限，默认 16 MiB。超过上限时
  导出被跳过并记 WARNING，用户的历史停留在最后一个良好快照（见
  capabilities 文档 §6.4）。

> **承重排除契约（N7）：`reconcile_orphans` 绝不能认领 WebUI pod。**
>
> 交互式 WebUI pod 携带 `openace.webui.kind=webui` 元数据且不绑定
> workflow——永远不会出现 workflow 行来认领它们，因此没有 kind 排除的
> `reconcile_orphans()` 清扫会把每个存活的用户 WebUI pod 判为孤儿并在
> **第一次运行时将它们全部销毁**。排除逻辑实现在
> `provider.reconcile_orphans`（元数据过滤 + 客户端侧复核）并由测试锁定。
> 任何人把该清扫接入新的生产调用方都必须保留排除；WebUI pod 由 web 进程
> 自己的按进程 generation 键控的对账（`app/services/webui_sandbox.py`）
> 回收，而不是由 workflow 存活集回收。

**单 web 进程假设。** webui-pod 对账会销毁其
`openace.webui.process_generation` 元数据不是当前 web 进程的 WebUI pod——
这只有在恰好一个 web 进程拥有该安装时才是可靠的判别。交付的 compose 部署
（单个 `open-ace` 应用容器）满足它；**多 web 副本或存在新旧重叠的滚动部署
不受支持**，会互相销毁对方的 pod。

---

## 9. 后端对比

来源均已标注。这里没有任何一个数字是无出处的。

### 启动与隔离开销

| 运行时 | 隔离 | 启动开销 | 内存开销 |
| --- | --- | --- | --- |
| runc | 进程 cgroups | ~0 ms | 极小 |
| gVisor | 用户态内核、syscall 拦截 | ~10–50 ms | ~50 MB |
| Kata（QEMU） | 完整 VM | ~500 ms | ~20–50 MB |
| Kata（Firecracker） | microVM | ~125 ms | ~5 MB |

*来源：OpenSandbox `docs/guides/secure-container.md`，上游发布。本项目
未测量。*

### 生命周期各阶段

| 阶段 | `legacy_posix` | `opensandbox` |
| --- | --- | --- |
| 冷启动 | 无——进程直接 spawn | 拉取镜像，再加上上述运行时开销 |
| 沙箱创建 | `fork`/`exec` | 一次 `POST /v1/sandboxes`，同步 |
| 工作区传输 | 无——worktree 本来就在本地 | 每文件一次上传，外加仓库合成 |
| 执行 | 本地 `Popen` | PTY WebSocket，或 `POST /command` |
| 收集变更 | 本地 git | 清单下载加控制平面校验 |
| 销毁 | 进程组信号 | `DELETE`，轮询到终态 |

*出处：结构性内容，依据实现推导。**这些阶段没有给出任何墙上时钟数字，
因为本项目没有在集群上测量过。** 在把本表用于容量规划之前，请用你自己的
部署数据填充；issue 所要求的指标已在每次生命周期调用上以审计事件形式
发出。*

### 兼容性

| 关注点 | gVisor | Kata |
| --- | --- | --- |
| syscall 覆盖 | 有文档的子集；罕见 syscall 可能失败 | 完整 Linux 内核 |
| 硬件要求 | 无 | VT-x / AMD-V + KVM |
| 密度 | 高 | 较低——每沙箱一个 VM |
| 出站强制执行 | 仅集群 NetworkPolicy：CIDR、静态、公网开放 | 出站 sidecar：按沙箱 FQDN 白名单、默认拒绝 |
| 是否声明 `network_egress_policy` | 否 | 是 |
| 典型用途 | 所有租户的默认 | 出站必须白名单化的租户 |

*出处：前四行来自上游指南加各 runtime 项目自己的文档。出站各行是本仓库
自身的行为——见 §7——且 gVisor 的限制是运行真实 server 发现的，不是从
文档读来的。*

**本节没有任何性能数字是本项目测量的**——上述开销数字是上游的。这与
后端*能否工作*是两回事，后者已经过测试；以下是在真实基础设施上跑过与
没跑过的确切清单：

- **gVisor tier 的完整生命周期已在真实集群上端到端运行过**（`runsc`
  下）：create → 内核探测 → CNI 探测 → 上传与 git 合成 → 带 SSE 的前台
  exec → PTY agent 轮次 → evidence → `collect_changes` → `apply_changes`
  → destroy。正是那次运行暴露了绿色测试套件曾隐藏的线上级缺陷——文件
  模式编码、SSE 分帧、execd 的身份模型、`networkPolicy` 不兼容、
  `/proc/version` 探测的误拒、一个 podSelector 匹配不到任何 pod 的
  NetworkPolicy、git 在 root 拥有的 `/workspace` 上的 `dubious ownership`、
  以及一个单位发错的命令超时。
- **CNI 出站机制已在强制执行策略的 CNI（Calico）上双向验证**：没有清单时
  启动探测读到 API server 可达并以 `egress_cni_not_enforced` 拒绝；apply
  之后两条腿都读到阻断，运行继续。对 gVisor 配置的 server 不带
  `networkPolicy` 创建沙箱同样确认被接受。
- **Kata 从未被实际运行过**：它需要 `/dev/kvm`，搭建尝试在客户机内核缺少
  `vhost_net` 模块之前，先经历了嵌套 VT-x 与 `kata-deploy` 然后失败。因此
  本文档中关于 Kata 的每句话都是设计意图，不是测量。
- `dubious ownership` 修复中的一块——覆盖 **agent 自己**运行的 git 命令
  （相对于仓库合成而言）的全局 `safe.directory`——是用 git 的
  `GIT_TEST_ASSUME_DIFFERENT_OWNER` 钩子在本地验证的，而非在集群上。

**你的 CNI 必须真正强制执行 NetworkPolicy。** 若干常见开发 CNI（包括
kind 默认的 `kindnet`）会接受 `NetworkPolicy` 对象然后忽略之。在 sidecar
tier 上这只是削弱 `metadata_cidr_blocked`；在 CNI tier 上则意味着完全没有
出站控制。这正是启动探测存在且 fail-closed 的原因：CNI 忽略策略的集群
会在首个沙箱上以 `egress_cni_not_enforced` 被拒绝，而不是带着敞开的出站
运行 agent。

### 成本

成本由节点容量主导，而节点容量取决于上述内存开销和你的沙箱并发。Kata 的
每沙箱 VM 使密度成为决定因素；gVisor 的开销接近 runc，实际差异在调度而非
占用。*不给美元数字：它们完全取决于你的集群和供应商。*

---

## 10. 故障排查

**每个 execd 调用都返回 401。** `execd_token_env` 未设置或指向一个空
变量。注意 execd 的认证中间件对空令牌短路，所以不带 `EXECD_ACCESS_TOKEN`
启动的 server 会接受匿名调用——这正是 `execd_token_required` 强制的
原因。

**首个沙箱上出现 `runtime_class_mismatch`。** server 的
`[secure_runtime]` 与 tier 的 `runtime_class` 不匹配，或调度该 pod 的节点
上未安装 RuntimeClass。

**agent 无法编辑自己的文件。** 检查 `runtime_user`/`runtime_group`。execd
可能以 root 运行，而受限 mode 下 root 拥有的文件对非 root 的 agent 不可
写。

**重启后孤儿清扫一无所毁。** 确认 workflow 行带有
`sandbox_provider = "opensandbox"` 和一个 `sandbox_id`；清扫以这两者为键。

**共享 server 上沙箱不断累积。** 清扫按 `openace.provider` 元数据过滤，
只销毁控制平面不再认领的沙箱。其他系统创建的沙箱绝不会被触碰——这是
刻意的。
