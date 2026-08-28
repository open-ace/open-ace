# OpenSandbox backend manifests (Issue #2023)

These manifests provision the OpenSandbox servers that back the
`OpenSandboxProvider`, and — just as importantly — they are what makes the
`attestations` block in `sandbox-backends.json` *true*.

## The attestation contract

The provider cannot observe pod security context, kubelet limits, the cluster
NetworkPolicy, or the egress sidecar's mode through the sandbox API. It declares
a capability only because an operator asserted the corresponding property, and
it **refuses to run at all** when one of the pod-hardening assertions is absent.

So these files and that config are two halves of one statement. Removing a field
here without removing its attestation does not degrade the backend — it makes
the workflow row lie about what protected the run.

| Attestation | Made true by |
| --- | --- |
| `nonroot_enforced` | `sandbox-podtemplate.yaml` — `runAsNonRoot: true`, `runAsUser: 1000` |
| `readonly_rootfs` | `sandbox-podtemplate.yaml` — `readOnlyRootFilesystem: true` |
| `seccomp_runtime_default` | `sandbox-podtemplate.yaml` — `seccompProfile: RuntimeDefault` |
| `dedicated_service_account` | `rbac.yaml` — a ServiceAccount with no Role, and `automountServiceAccountToken: false` |
| `pod_pids_limit` | kubelet `--pod-max-pids` / `podPidsLimit` on the sandbox nodes (see below) |
| `metadata_cidr_blocked` | `networkpolicy.yaml` |
| `egress_enforced`, `egress_mode_dns_nft` | `configmap-*.yaml` — `[egress] mode = "dns+nft"` |
| `execd_token_required` | `server-*.yaml` — a non-empty `EXECD_ACCESS_TOKEN` |
| `secure_access_required` | the provider always sets `secureAccess: true`; keep it attested so a tier cannot opt out |
| `ephemeral_storage_enforced` | `sandbox-podtemplate.yaml` volume `sizeLimit`s |
| — (not an attestation, but required) | `sandbox-podtemplate.yaml` mounts a **writable** volume at `/home/agent`. `HOME` lives outside `/workspace` on purpose: under `/workspace` the repo synthesis's `git add -A` stages the agent's whole home tree — pip wheels, npm, pre-commit environments — into the initial commit. |
| `inode_quota_enforced` | **nothing here.** Leave it `false` unless the node filesystem carries a real project quota — see below. |

### `pod_pids_limit` is a kubelet setting, not a manifest

Kubernetes has no per-pod pids field. Set it on every node that schedules
sandbox pods, in the kubelet config:

```yaml
podPidsLimit: 512
```

This is the only real defence against a fork bomb. The provider deliberately does
**not** claim pids enforcement from an in-sandbox `ulimit`: an agent can reach
execd (every command inherits execd's environment, including its access token)
and ask for `uid: 0`, so any in-band limit is bypassable from inside.

### `inode_quota_enforced` should normally stay `false`

`ulimit -f` caps a single file's size, and a Kubernetes `ephemeral-storage`
limit is enforced by kubelet eviction polling with no inode dimension. Neither
is an inode quota. Turning this on without an actual project quota (XFS pquota
or equivalent) writes `"enforced": {"inode": true}` into the workflow row for a
guarantee nothing provides.

## The pod template is per-tier

`sandbox-podtemplate.yaml` hardcodes `runtimeClassName: gvisor`. **The Kata tier
needs its own copy** with `runtimeClassName: kata-qemu`, referenced from
`configmap-kata.yaml`. Applying the shipped template for both tiers gives you a
Kata *server* scheduling gVisor *sandboxes* — the provider's `/proc/version`
probe catches it and refuses every run, which is the correct outcome but an
avoidable one.

```bash
sed 's/runtimeClassName: gvisor/runtimeClassName: kata-qemu/; s/opensandbox-sandbox/opensandbox-sandbox-kata/' \
  sandbox-podtemplate.yaml > sandbox-podtemplate-kata.yaml
```

## Two tiers, because the runtime is server-level

gVisor vs Kata is chosen in the OpenSandbox server's own config, not per
request — upstream: "All sandboxes on that server transparently use the
configured runtime. SDK users and API callers require no code changes." Each
tier therefore needs its own Deployment, Service and ConfigMap, and the backend
config routes tenants to the right endpoint.

The provider does not take that on trust: on the first sandbox per endpoint it
reads `/proc/version` and refuses to continue if the kernel does not match the
declared `runtime_class`.

## Applying

```bash
kubectl apply -k k8s/extras/opensandbox/
```

`image-policy.yaml` is **not** in the kustomization: it requires Kyverno. Apply
it separately once a policy controller is installed —

```bash
kubectl apply -f k8s/extras/opensandbox/image-policy.yaml
```

Cosign signature and SBOM verification live there, at admission. The provider
enforces only allowlist membership and digest pinning, so "image allowlist,
signature and SBOM" is a split responsibility, not a single Python check.

## Node prerequisites

- **gVisor** — `runsc` plus `containerd-shim-runsc-v1`.
- **Kata** — `kata-containers`, hardware virtualization (VT-x / AMD-V), KVM,
  and a kernel ≥ 5.10.

`kubectl get runtimeclass` should list both before the servers start; the
OpenSandbox server validates its configured runtime at boot and refuses to
start when it is unavailable.

## Secrets

```bash
kubectl create secret generic opensandbox-keys -n open-ace \
  --from-literal=gvisor-api-key="$(openssl rand -hex 32)" \
  --from-literal=gvisor-execd-token="$(openssl rand -hex 32)" \
  --from-literal=kata-api-key="$(openssl rand -hex 32)" \
  --from-literal=kata-execd-token="$(openssl rand -hex 32)"
```

`sandbox-backends.json` names the environment variables holding these; the
values never appear in that file.
