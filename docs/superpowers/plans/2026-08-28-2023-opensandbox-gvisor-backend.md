# OpenSandbox / gVisor–Kata Production Sandbox Backend — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement issue #2023 — a production `SandboxProvider` backed by OpenSandbox's Kubernetes runtime (gVisor default, Kata for high-security tenants), wired far enough that the coding agent actually runs inside the sandbox.

**Architecture:** A new `opensandbox` package implements the frozen `#2022` Protocol over OpenSandbox's Lifecycle API (`/v1`) and Execd API (`:44772`). gVisor-vs-Kata is server-level upstream, so isolation tiers route to separately configured endpoints. Capabilities derive from operator **attestations** plus two runtime probes — never from in-band mechanisms, which an agent that can reach execd as root would bypass. The agent's interactive `--input-format stream-json` stdin runs over execd's PTY **pipe mode** WebSocket, reached through a new `AgentTransport` seam whose local implementation wraps today's `Popen` one-to-one.

**Tech Stack:** Python 3.10+, `requests`, `websockets.sync.client` (both already required), `pytest`.

**Spec:** `docs/superpowers/specs/2026-08-28-2023-opensandbox-gvisor-backend-design.md` (revision 2)

---

## Ordering rationale

Tasks 1–5 are pure additions with no production reachability. Task 6 introduces
the transport seam with `LocalProcessTransport` **first**, proving behavioural
equivalence on the existing local path before any container code depends on it —
that is what makes touching `_run_local` safe. Tasks 7–9 build the provider.
Tasks 10–11 wire production. Nothing before Task 10 can change existing
behaviour, and with no backend config present the behaviour after Task 11 is
byte-identical to today.

## File Structure

**Create:** `.../sandbox/opensandbox/{__init__,config,client,policy,workspace,transport,provider,fake_server}.py`,
`.../sandbox/transport.py`, `.../sandbox/isolation_tier.py`,
`k8s/extras/opensandbox/*.yaml`, `docs/sandbox-backends.md`,
`tests/unit/test_opensandbox_{config,client,policy,workspace,transport,provider,reconcile,wiring}.py`,
`tests/unit/test_agent_transport.py`

**Modify:** `.../sandbox/registry.py`, `.../sandbox/__init__.py`,
`.../sandbox/legacy_posix.py` (add `get_transport`),
`app/modules/workspace/autonomous/agent_runner.py`,
`app/services/autonomous_scheduler.py`

Every test file: `pytestmark = [pytest.mark.regression, pytest.mark.issue(2023)]`, in `tests/unit/` per `docs/TEST_LAYERS.md`.

---

### Task 1: Config with attestations

**Files:** Create `opensandbox/__init__.py`, `opensandbox/config.py`; Test `tests/unit/test_opensandbox_config.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_resolves_default_tier_endpoint():
def test_tenant_tier_overrides_default():
def test_tier_pointing_at_missing_endpoint_fails_closed():
    # RAISES; never falls back to a weaker tier.
def test_missing_attestation_removes_the_capability_not_the_check():
def test_egress_enforced_without_allowlist_is_rejected():
@pytest.mark.parametrize("host", ["169.254.169.254", "metadata.google.internal",
                                  "10.0.0.5", "127.0.0.1", "192.168.1.1", "::1"])
def test_ip_literals_and_metadata_hosts_rejected_from_allowlist(host):
    # Upstream cannot express IP egress rules at all, so an IP literal in the
    # allowlist is meaningless, not merely risky.
def test_tag_only_image_rejected_from_allowlist():
def test_default_image_must_be_in_allowlist_and_digest_pinned():
def test_ttl_below_upstream_minimum_60_is_rejected():
def test_egress_allow_hosts_are_per_endpoint():
def test_missing_config_file_returns_none(tmp_path):
def test_malformed_config_raises_rather_than_defaulting(tmp_path):
def test_explicit_env_path_wins(tmp_path, monkeypatch):
```

- [ ] **Step 2: Run to verify failure** — `python -m pytest tests/unit/test_opensandbox_config.py -q` → `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
class SandboxConfigError(SandboxError): ...

@dataclass(frozen=True)
class Attestations:
    egress_enforced: bool = False
    egress_mode_dns_nft: bool = False
    metadata_cidr_blocked: bool = False
    execd_token_required: bool = False
    secure_access_required: bool = False
    nonroot_enforced: bool = False
    readonly_rootfs: bool = False
    seccomp_runtime_default: bool = False
    dedicated_service_account: bool = False
    pod_pids_limit: int = 0
    ephemeral_storage_enforced: bool = False
    inode_quota_enforced: bool = False

@dataclass(frozen=True)
class PoolConfig:
    pool_ref: str = ""
    egress_preapplied: bool = False
    recycle_delete: bool = False
    image_digest: str = ""
    def usable(self) -> bool:   # all three attestations + digest-pinned image
        ...

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
    default_image: str
    execd_port: int = 44772
    execd_endpoint_host_allowlist: tuple[str, ...] = ()
    egress_allow_hosts: tuple[str, ...] = ()
    attestations: Attestations = Attestations()
    pool: PoolConfig = PoolConfig()
    def api_key(self) -> str: ...   # from os.environ[api_key_env]; raises if unset

@dataclass(frozen=True)
class SandboxBackendConfig:
    default_tier: str
    endpoints: Mapping[str, EndpointConfig]
    tenant_tiers: Mapping[str, str]
    project_tiers: Mapping[str, str]
    image_allowlist: frozenset[str]
    image_signer_identity: str
    resource_defaults: Mapping[str, str]
    sandbox_ttl_seconds: int
    changeset_limits: ChangesetLimits
    def tier_for(self, *, tenant, project_path) -> str: ...
    def endpoint_for(self, *, tenant, project_path) -> EndpointConfig: ...

def candidate_backend_config_paths(explicit: str | None = None) -> tuple[str, ...]: ...
def resolve_backend_config_path(explicit: str | None = None) -> str | None: ...
def parse_backend_config(raw: Mapping[str, object]) -> SandboxBackendConfig: ...
def load_backend_config(explicit: str | None = None) -> SandboxBackendConfig | None: ...
```

Validation rules are spec §4.2 verbatim. Path precedence mirrors
`task_isolation.candidate_agent_task_policy_paths`.

- [ ] **Step 4: Run tests** → PASS
- [ ] **Step 5: Commit** — `feat(#2023): OpenSandbox backend config with operator attestations`

---

### Task 2: REST client with execd endpoint guard

**Files:** Create `opensandbox/client.py`; Test `tests/unit/test_opensandbox_client.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_every_call_sends_api_key_header_and_disables_proxies():
def test_execd_calls_use_x_execd_access_token_header():
    # NOT Authorization: Bearer — upstream defines an apiKey header.
def test_delete_sandbox_treats_404_as_success():
def test_non_2xx_raises_with_status_and_upstream_code():
def test_list_sandboxes_follows_pagination_until_short_page():
    # pageSize defaults to 20 upstream; a single request would silently miss
    # every sandbox past the first page.
def test_list_sandboxes_stops_at_max_pages_guard_and_warns():
def test_execd_endpoint_off_allowlist_is_refused():
    # The endpoint URL is SERVER-supplied; we POST the whole workspace to it.
def test_execd_requests_disable_redirects():
def test_server_supplied_endpoint_headers_are_filtered_by_key_allowlist():
def test_sse_parser_yields_typed_events_and_skips_ping_and_comments():
def test_sse_parser_drops_truncated_trailing_event_without_raising():
def test_upload_file_builds_metadata_part_before_file_part():
def test_upload_file_sets_owner_group_and_mode():
```

- [ ] **Step 2: Run to verify failure**

- [ ] **Step 3: Implement**

`OpenSandboxApi` Protocol + `HttpOpenSandboxApi` per spec §6's call table, plus
`create_pty_session`, `pty_status`, `delete_pty_session`, `pty_ws_url`.
`iter_sse_events(response)` parses `text/event-stream`.

Single `_request` helper carrying the mandated comment:

```python
# 直接调用原因：OpenSandbox 服务端是集群内地址（*.svc.cluster.local / 私网 IP），
# app.utils.outbound_url_guard.safe_request 按设计拒绝私网目标，无法用于本调用。
# lifecycle base_url 仅来自运维配置，永不来自用户输入；execd URL 来自服务端响应，
# 因此单独经 _validate_execd_url 白名单校验 + allow_redirects=False（见 spec §3.1）。
# proxies=None 关闭代理查找，避免 gevent 环境下的 RecursionError（CLAUDE.md #2237）。
```

- [ ] **Step 4: Run tests** → PASS
- [ ] **Step 5: Commit** — `feat(#2023): OpenSandbox REST client with execd endpoint guard`

---

### Task 3: Policy translation, capabilities, refusals

**Files:** Create `opensandbox/policy.py`; Test `tests/unit/test_opensandbox_policy.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_default_egress_blocks_metadata_private_cidr_and_unknown_domain():
    policy = build_create_request(_spec(), cfg, endpoint, generation=1)["networkPolicy"]
    assert policy["defaultAction"] == "deny"
    targets = {r["target"] for r in policy["egress"]}
    assert targets == {"api.anthropic.com"}
    assert all(r["action"] == "allow" for r in policy["egress"])

def test_allow_cidrs_fail_closed_because_upstream_egress_has_no_ip_rules():
def test_unrestricted_egress_mode_refused():
def test_host_backed_volume_refused():
def test_image_outside_allowlist_or_tag_only_refused():
def test_missing_secure_access_attestation_refused():
def test_spec_with_all_none_fields_is_synthesised_from_tier_before_refusals():
    # agent_runner builds SandboxSpec with runtime/network_egress/volumes all
    # None. Without synthesis the refusals are dead code and a tier that cannot
    # enforce egress would pass the capability gate.
def test_tier_without_egress_attestation_fails_closed_even_when_spec_asks_nothing():

def test_capabilities_track_attestations_not_a_constant():
def test_pids_capability_requires_pod_pids_limit_attestation():
def test_storage_inode_quota_off_by_default_and_inode_limit_spec_fails_closed():
    # ulimit -f caps ONE file; k8s ephemeral-storage is eviction-polled with no
    # inode dimension. Declaring the capability would write
    # "enforced": {"inode": true} to the workflow row — a lie in the DB.
def test_every_declared_capability_maps_to_an_attestation_or_probe():

def test_memory_bytes_and_cgroup_cpu_max_convert_to_k8s_quantities():
    assert build_resource_limits(AgentTaskPolicy(cpu_max="200000 100000"), ...)["cpu"] == "2000m"
def test_policy_wins_over_resource_defaults_and_defaults_fill_only_zeros():
def test_pids_max_above_attested_pod_limit_is_refused():
def test_wall_clock_maps_to_sandbox_ttl_and_command_timeout_ms():
def test_ttl_never_below_upstream_minimum_60():

def test_env_built_from_allowlist_never_inherits_process_env(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_x"); monkeypatch.setenv("GH_TOKEN", "ghp_x")
    env = build_env(_spec(), cfg, endpoint, proxy_token="t")
    assert "GITHUB_TOKEN" not in env and "GH_TOKEN" not in env and "GH_CONFIG_DIR" not in env

def test_argv_is_shlex_quoted_into_the_shell_string():
    # RunCommandRequest.command is a string run via `bash -c`; a branch name or
    # path with shell metacharacters would otherwise be injection.
    body = build_command_request(["echo", "a; rm -rf /"], ...)
    assert "'a; rm -rf /'" in body["command"]

def test_sandbox_state_mapping_is_total_and_unknown_maps_to_error():
def test_metadata_values_are_all_strings_including_generation():
def test_secure_access_true_is_always_set():
```

- [ ] **Step 2: Run to verify failure**
- [ ] **Step 3: Implement** per spec §5, §6.1, §6.2 — signatures:

```python
def derive_capabilities(endpoint: EndpointConfig, *, probes_passed: bool) -> frozenset[SandboxCapability]: ...
def synthesise_spec_fields(spec, cfg, endpoint) -> SandboxSpec: ...
def validate_spec_for_endpoint(spec, cfg, endpoint) -> None: ...
    # NOTE: distinct from provider.validate_spec_capabilities (the shared #2022
    # gate), which this calls last. Do not collapse the two names.
def build_network_policy(spec, endpoint) -> dict: ...
def build_resource_limits(policy, cfg, endpoint) -> dict[str, str]: ...
def build_env(spec, cfg, endpoint, *, proxy_token="", extra=None) -> dict[str, str]: ...
def build_create_request(spec, cfg, endpoint, *, generation, tenant=None) -> dict: ...
def build_command_request(command: list[str], *, cwd, envs, timeout_ms) -> dict: ...
def map_state(state: str) -> SandboxStatus: ...
```

- [ ] **Step 4: Run tests** → PASS
- [ ] **Step 5: Commit** — `feat(#2023): attestation-derived capabilities and fail-closed policy translation`

---

### Task 4: Workspace snapshot and ChangeSet

**Files:** Create `opensandbox/workspace.py`; Test `tests/unit/test_opensandbox_workspace.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_snapshot_excludes_git_credentials_ssh_and_env(tmp_path):
    # (part of test_sandbox_cannot_read_host_or_peer_workspace; the peer half
    #  lives in test_opensandbox_provider.py where secureAccess is asserted)
    paths = {e.path for e in build_snapshot(str(tmp_path))}
    assert paths == {"src/main.py"}

def test_snapshot_refuses_symlink_pointing_outside_worktree(tmp_path):
def test_snapshot_sets_file_0644_dir_0755_and_runtime_owner():
    # execd may run as root; root-owned restrictive files would leave the agent
    # unable to edit its own workspace.

def test_changeset_rejects_absolute_path_symlink_escape_and_oversize_file(tmp_path):
    reasons = {r.path: r.reason for r in validate_changeset(entries, root=..., limits=...)}
    assert reasons["/etc/passwd"] == "absolute_path"
    assert reasons["../../outside.txt"] == "path_escape"
    assert reasons["link"] == "symlink_escape"
    assert reasons["big.bin"] == "file_too_large"
    assert reasons["setuid.sh"] == "unsafe_mode"
    assert "ok.py" not in reasons

def test_validate_returns_all_rejections_not_first_fail():
def test_deleted_entries_get_the_same_path_checks():
def test_apply_is_additive_plus_explicit_deletes_never_a_full_sync(tmp_path):
    # A full sync would delete the trusted repo's .git, since .git can never
    # appear in a manifest.
    assert (tmp_path / ".git").exists()
    assert (tmp_path / "untouched.py").exists()
def test_apply_writes_nothing_when_any_entry_is_rejected(tmp_path):
def test_changeset_rejects_over_file_count_and_total_size():
def test_changeset_rejects_secret_bearing_paths():
```

- [ ] **Step 2: Run to verify failure**
- [ ] **Step 3: Implement** per spec §7.1–§7.2. Manifest shape
`{"files": [...], "deleted": [...]}`; `_SECRET_GLOBS` is a concrete tuple in this
module. `apply_changeset` validates fully, raises before the first write, writes
via temp dir + move.

- [ ] **Step 4: Run tests** → PASS
- [ ] **Step 5: Commit** — `feat(#2023): credential-free snapshot and all-or-nothing ChangeSet apply`

---

### Task 5: Fake OpenSandbox server

**Files:** Create `opensandbox/fake_server.py`

- [ ] **Step 1: Implement `FakeOpenSandboxApi`**

Models upstream's **real** behaviour, not convenient behaviour:

- create → `status.state: "Running"` (synchronous, per spec §2.1)
- delete → `Stopping`, then `Terminated` on the next read
- `list_sandboxes` paginates with `pageSize` 20
- foreground `/command` → `init`/`stdout`/`stderr` events, and on non-zero exit
  an `error` event with numeric `evalue` and **no** `execution_complete`
- pod-OOM mode: execd calls raise connection errors **and** the sandbox reads
  back `Failed` with an OOM reason — not a convenient exit 137
- `secureAccess` enforcement: a peer request without the endpoint token is rejected
- fake sidecar `/policy` returning a configurable `enforcementMode`
- fake `/proc/version` output for the runtime probe
- `FakePtyTransport` producing `0x01`/`0x02` frames and a JSON `exit` frame

Inspection attributes: `created_bodies`, `command_bodies`, `uploaded`,
`deleted`, `sandboxes`, `pty_sessions`.

- [ ] **Step 2: Commit** — `test(#2023): in-memory OpenSandbox API double`

---

### Task 6: `AgentTransport` seam — local first

**Files:** Create `sandbox/transport.py`; Modify `sandbox/legacy_posix.py`; Test `tests/unit/test_agent_transport.py`

This task lands the seam with **only** the local implementation, so equivalence
is proven before any container code depends on it.

- [ ] **Step 1: Write the failing tests**

```python
def test_local_transport_round_trips_stdin_and_stdout():
def test_local_transport_readline_returns_empty_bytes_at_eof():
def test_local_transport_poll_and_wait_match_the_wrapped_popen():
def test_local_transport_exposes_the_real_pid():
def test_legacy_provider_get_transport_wraps_its_own_popen():
def test_local_transport_close_stdin_is_idempotent():
```

- [ ] **Step 2: Run to verify failure**

- [ ] **Step 3: Implement**

```python
class AgentTransport(Protocol):
    def write_stdin(self, data: bytes) -> None: ...
    def close_stdin(self) -> None: ...
    def readline_stdout(self) -> bytes: ...
    def readline_stderr(self) -> bytes: ...
    def poll(self) -> int | None: ...
    def wait(self, timeout: float | None = None) -> int | None: ...
    @property
    def pid(self) -> int | None: ...

class LocalProcessTransport:
    def __init__(self, process: subprocess.Popen) -> None: ...
```

`LocalProcessTransport` delegates one-to-one to the wrapped `Popen`; it adds no
behaviour. `LegacyPosixProvider.get_transport(exec_handle)` returns one wrapping
the `Popen` `get_process` already returns. `get_process` stays for the callers
that genuinely need the raw object.

- [ ] **Step 4: Run tests** → PASS
- [ ] **Step 5: Commit** — `feat(#2023): AgentTransport seam with a pass-through local implementation`

---

### Task 7: PTY WebSocket transport

**Files:** Create `opensandbox/transport.py`; Test `tests/unit/test_opensandbox_transport.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_stdin_is_framed_with_0x00_prefix():
def test_stdout_and_stderr_frames_demultiplex_into_separate_streams():
    # 0x01 -> stdout, 0x02 -> stderr (pipe mode only)
def test_partial_frames_accumulate_into_whole_lines():
def test_exit_frame_resolves_poll_and_wait_with_its_exit_code():
def test_pid_is_none_for_a_non_local_backend():
def test_reconnect_replays_from_output_offset_without_losing_output():
def test_signal_is_sent_as_a_json_text_frame():
def test_wait_times_out_and_returns_none_when_the_shell_never_exits():
```

- [ ] **Step 2: Run to verify failure**
- [ ] **Step 3: Implement `PtyWebSocketTransport`** per spec §2.4/§6.5, using
`websockets.sync.client.connect` (already used in
`app/modules/workspace/vscode_ws_bridge.py`). Reader thread demultiplexes into
two line-buffered queues; `?since=<output_offset>` on reconnect.

- [ ] **Step 4: Run tests** → PASS
- [ ] **Step 5: Commit** — `feat(#2023): execd PTY pipe-mode transport`

---

### Task 8: `OpenSandboxProvider`

**Files:** Create `opensandbox/provider.py`; Modify `sandbox/__init__.py`; Test `tests/unit/test_opensandbox_provider.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_provider_satisfies_the_frozen_protocol_surface():
def test_create_rejects_a_capability_the_endpoint_does_not_attest():
def test_stale_generation_handle_is_refused():

# Status overlay — these three would otherwise fail against upstream's real timing
def test_inspect_returns_created_until_first_exec():
def test_stop_transitions_to_stopped_even_though_upstream_stays_running():
def test_destroy_marks_destroyed_immediately_and_is_idempotent():

def test_stream_emits_the_canonical_lifecycle_sequence():
    assert kinds[0] == SandboxEventKind.PROCESS_STARTED
    assert SandboxEventKind.COMMAND_STARTED in kinds
    assert kinds[-1] == SandboxEventKind.PROCESS_EXITED
    assert all(e.sandbox_id for e in events)
def test_sse_error_with_numeric_evalue_is_a_normal_nonzero_exit_not_sandbox_error():
    # Upstream emits `error` (not execution_complete) for any non-zero exit.
    # Mapping it to SANDBOX_ERROR would report every failing pytest run as
    # infrastructure failure.
def test_sse_error_with_non_numeric_evalue_is_a_sandbox_error():
def test_stream_never_reports_completed_for_a_non_completion():

def test_resource_limits_return_structured_terminal_reason():
    # child killed under the cgroup
    assert ev(exit_code=137).terminal_reason == TerminalReason.SIGNAL.value
    # pod-level OOM: execd unreachable AND the sandbox reads back Failed
    assert ev(pod_oom=True).terminal_reason == TerminalReason.SIGNAL.value
    assert ev(timed_out=True).terminal_reason == TerminalReason.TIMEOUT.value

def test_execution_evidence_matches_provider_contract():
    row = provider.collect_execution_evidence(handle)[0]
    assert row.sandbox_id == handle.sandbox_id
    assert row.sandbox_generation == handle.generation
    assert (row.exit_code, row.signal) == (0, None)
    assert row.terminal_reason == TerminalReason.COMPLETED.value
    assert row.started_at and row.completed_at
def test_terminal_reason_comes_from_derive_terminal_reason_with_decoded_signal():
    # 128+n must be decoded into signal= BEFORE calling the canonical mapper,
    # or derive_terminal_reason(exit_code=137) returns COMPLETED.

def test_warm_pool_does_not_reuse_tenant_state():
def test_warm_pool_refused_unless_egress_recycle_and_image_all_attested():

def test_runtime_probe_rejects_a_gvisor_kernel_on_a_kata_endpoint():
def test_egress_probe_fails_closed_when_sidecar_reports_dns_instead_of_dns_nft():

def test_sandbox_cannot_read_host_or_peer_workspace():
    assert provider_created_body["secureAccess"] is True
    assert not any(v.get("host") for v in provider_created_body.get("volumes", []))
    with pytest.raises(SandboxError):
        api.peer_request(other_sandbox_id, token=None)

def test_refusals_emit_an_audit_event_and_carry_a_reason_code():
```

- [ ] **Step 2: Run to verify failure**
- [ ] **Step 3: Implement** per spec §6. Constructor:

```python
class OpenSandboxProvider:
    def __init__(self, config, *, api_factory=HttpOpenSandboxApi, tenant=None,
                 project_path=None, event_sink=None) -> None: ...
    def get_transport(self, exec_handle) -> AgentTransport: ...
    def reconcile_orphans(self, live_sandbox_ids) -> list[str]: ...
```

Per-sandbox state (status overlay, exit codes, terminal kinds, evidence) lives in
dicts keyed by `sandbox_id` and is **popped** on destroy.

- [ ] **Step 4: Run tests** → PASS
- [ ] **Step 5: Commit** — `feat(#2023): OpenSandboxProvider over the #2022 contract`

---

### Task 9: Registry and isolation-tier gate

**Files:** Create `sandbox/isolation_tier.py`; Modify `sandbox/registry.py`; Test `tests/unit/test_opensandbox_reconcile.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_registry_resolves_opensandbox_name():
def test_registry_raises_when_backend_config_absent():
def test_registry_still_rejects_unknown_provider_names():
def test_provider_for_accepts_an_optional_event_sink_without_breaking_callers():
def test_reconcile_orphans_paginates_past_the_first_page():
def test_reconcile_orphans_never_raises_on_a_bad_row():
```

- [ ] **Step 2: Run to verify failure**
- [ ] **Step 3: Implement.** `registry.provider_for` gains an `"opensandbox"`
branch (loads config, raises when absent) and a keyword-only optional
`event_sink`; the final unknown-name raise is untouched.
`isolation_tier.select_provider(*, tenant, project_path, config, requires_production_isolation)`
returns OpenSandbox or raises; it returns Legacy only when production isolation
is not required.

- [ ] **Step 4: Run tests** → PASS
- [ ] **Step 5: Commit** — `feat(#2023): registry resolution and production-isolation gate`

---

### Task 10: Scheduler reconciliation wiring

**Files:** Modify `app/services/autonomous_scheduler.py`; Test `tests/unit/test_opensandbox_wiring.py`

- [ ] **Step 1: Write the failing test**

```python
def test_node_and_control_plane_restart_reconcile_sandbox():
    # Asserted at the SCHEDULER layer. The provider method alone would pass green
    # while production leaks: _destroy_orphan_sandbox returns early for every
    # provider except remote_machine, and _reconcile_orphan_sandboxes then marks
    # the row destroyed regardless.
    wf = {"workflow_id": "w1", "sandbox_provider": "opensandbox",
          "sandbox_id": "sb-1", "sandbox_remote_session_id": None}
    _destroy_orphan_sandbox(wf, remote_session_manager=None)
    assert "sb-1" in api.deleted

def test_remote_machine_reconcile_path_is_unchanged():
def test_legacy_row_still_no_ops():
def test_reconcile_sweep_survives_a_provider_failure_on_one_row():
```

- [ ] **Step 2: Run to verify failure** — the opensandbox assertion fails: `api.deleted` is empty.

- [ ] **Step 3: Implement.** Replace the early return at
`autonomous_scheduler.py:1717`:

```python
# Providers that own an external resource surviving a control-plane restart.
# remote_machine keys off its session id; opensandbox keys off sandbox_id.
# Legacy has nothing to stop (the proc died with the server).
if provider_name == "remote_machine":
    external_id_present = bool(remote_sid)
elif provider_name == "opensandbox":
    external_id_present = bool(sandbox_id)
else:
    external_id_present = False
if not external_id_present:
    return
```

Update the two docstrings that currently assert "legacy/gVisor rows … have
nothing to stop" — that assumption is what this task overturns.

- [ ] **Step 4: Run tests** → PASS
- [ ] **Step 5: Commit** — `fix(#2023): reconcile OpenSandbox orphans after a control-plane restart`

---

### Task 11: Agent-runner wiring

**Files:** Modify `app/modules/workspace/autonomous/agent_runner.py`; Test `tests/unit/test_opensandbox_wiring.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_required_production_policy_cannot_fallback_to_legacy():
    # Asserted through the documented single branch point, not a standalone helper.
    runner = AgentRunner(...)
    with pytest.raises(SandboxError):
        runner._select_sandbox_provider("local", tenant="acme")   # required, unconfigured
    assert isinstance(runner._select_sandbox_provider("local", tenant="dev"), LegacyPosixProvider)

def test_local_path_is_byte_identical_when_no_backend_config_present(monkeypatch):
    # The rollout guarantee: absent config == today's behaviour.

def test_run_local_uses_get_transport_not_get_process():
def test_pid_registration_is_skipped_when_transport_pid_is_none():
def test_cancellation_still_routes_through_the_provider_for_a_pidless_transport():
```

- [ ] **Step 2: Run to verify failure**

- [ ] **Step 3: Implement.** In `_run_local`, replace
`process = self._sandbox_provider.get_process(exec_handle)` with
`transport = provider.get_transport(exec_handle)`, store it on `_LocalSession`,
and update the seven `session.process.*` call sites
(`:4148`, `:4181`, `:4371`, `:4534`, `:4545`, `:4600`, `:4628`) to the transport
methods. Guard `_on_pid_registered` on `transport.pid is not None`. Route
`_select_sandbox_provider` through `isolation_tier.select_provider`, preserving
the existing remote branch.

Delete the now-stale `NOTE (#2023)` comment at `:2669` describing the seam as
deferred, and replace it with one naming `AgentTransport` as the seam.

- [ ] **Step 4: Run the full sandbox + runner suites**

Run: `python -m pytest tests/unit -k "sandbox or agent_runner or opensandbox" -q`
Expected: all pass — especially the pre-existing local-path tests, which must be
untouched by the transport swap.

- [ ] **Step 5: Commit** — `feat(#2023): route the agent through AgentTransport and the isolation gate`

---

### Task 12: Kubernetes manifests

**Files:** Create `k8s/extras/opensandbox/{runtimeclasses,server-gvisor,server-kata,configmap-gvisor,configmap-kata,sandbox-podtemplate,networkpolicy,rbac,image-policy,kustomization}.yaml`, `README.md`

- [ ] **Step 1: Write the manifests** per spec §11. The sandbox pod template must
carry exactly the properties §4's attestations promise (`runAsNonRoot`,
`readOnlyRootFilesystem`, dropped capabilities, `allowPrivilegeEscalation: false`,
seccomp `RuntimeDefault`, dedicated `ServiceAccount`), and the README must state
that each attestation in `sandbox-backends.json` is only true if the
corresponding manifest is applied.

- [ ] **Step 2: Validate**

Run: `python -c "import yaml,glob; [list(yaml.safe_load_all(open(f))) for f in glob.glob('k8s/extras/opensandbox/*.yaml')]; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit** — `feat(#2023): Kubernetes manifests for gVisor and Kata tiers`

---

### Task 13: Operator guide and comparison report

**Files:** Create `docs/sandbox-backends.md`

- [ ] **Step 1: Write it** per spec §11 — including the attestation checklist,
the fail-closed reason-code catalogue, the Legacy/gVisor/Kata comparison table
with **every number labelled by provenance**, and the security note that inside
its own sandbox the agent can reach execd as root.

- [ ] **Step 2: Commit** — `docs(#2023): OpenSandbox operator guide and backend comparison`

---

### Task 14: Full verification

- [ ] **Step 1:** `python -m pytest tests/unit/test_opensandbox_*.py tests/unit/test_agent_transport.py -q` → all pass
- [ ] **Step 2:** `python -m pytest tests/unit -k "sandbox or agent_runner or scheduler" -q` → no regressions
- [ ] **Step 3:** `pre-commit run --files $(git diff --name-only origin/main...HEAD | tr '\n' ' ')` → clean (bare black/ruff is NOT CI-equivalent; the hooks are pinned)
- [ ] **Step 4:** push via `scripts/push.sh` (never bare `git push`), open the PR stating the §9 scope boundaries and the §5.1 security note.
