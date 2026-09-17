"""Versioned capability contract for local interactive workspace multi-user
isolation (Issue #3374).

The contract reports what this deployment actually enforces, derived from
runtime state only (webui manager config, environment, euid, platform).
It deliberately never upgrades the reported isolation level: ``os_user``
means per-user OS accounts on a shared host kernel, not strong runtime
isolation (no namespaces, no egress policy). Reason messages stay free of
deployment specifics; see docs/WORKSPACE_ISOLATION_CAPABILITIES.md for
deployment requirements. Bump POLICY_REVISION whenever derivation semantics
or the entry-point matrix change.

Issue #3378 adds the ``sandboxed`` level (WebUI pods on an OpenSandbox
backend). Its kernel/egress enforcement is only verifiable per-pod via boot
probes, so the sandboxed snapshot reports those dimensions as unsupported
with an explicit ``sandbox_runtime_unverified`` reason until the first
successful pod probe memoizes the upgrade (in-process; resets on
control-plane restart, when a probe on the tier fails, and after a 1h TTL).
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from typing import Any

# Revision 5 (2026-09-16.1, Issue #3410): entry-point matrix RECALIBRATED and
# made machine-readable.
#  - filesystem_api: partial -> enforced. The /fs per-file paths no longer act
#    on a realpath()'d path (the #3410 cross-user symlink overwrite: a user
#    planted <own home>/x -> <other user>/x and the root branch wrote through
#    it). A root process now reaches upload/download/delete-file/search targets
#    by descending from the home root with O_NOFOLLOW directory fds, and the
#    package non-root wrappers (openace-write-as, openace-rm) probe, write and
#    delete AS the target account, gated on their capability markers.
#    create-directory gained the home lock it never had, the per-file paths use
#    the same per-base home roots browse does, and <base>/<account> is now 0700
#    like /home/<account>.
#  - terminal / vscode: partial -> remote_machine_scope. Their old "partial"
#    reasons (terminal tokens not bound to the session owner; VS Code owner =
#    machine creator) were FIXED by #3376 / PR #3380 and the matrix was never
#    updated. The honest statement is not "enforced" either: both entries
#    execute on a REMOTE machine under that machine's agent account, so the
#    local os_user level does not extend to them; access is governed by the
#    machine assignment ACL + session ownership + tenant gate.
#  - New sibling map ENTRY_POINT_DETAILS (emitted as entry_point_details):
#    scope, coverage, per-operation roots and symlink policy, access-control
#    mechanisms, structured limitations (gaps that DO gate admission) and
#    residuals (accepted properties that do NOT), so an integrator can tell
#    "covered by the declared level" from "governed by a different mechanism"
#    from "real gap" without parsing prose.
#
# Revision 4 (2026-09-12.2, review round 1 T-K): the entry-point matrix is
# per-level — sandboxed snapshots report terminal/vscode/filesystem_api as
# ``sandboxed_entry_not_wired`` (those executors still live on the control
# plane and are not wired to the user's pod); sandboxed snapshots on a cold
# worker (no manager yet) append ``sandbox_launch_unverified`` (the
# launch_path_unverified precedent, #3375).
#
# Revision 3 (2026-09-12.1, Issue #3378): new sandboxed level with a zero-pod
# fail-closed probe; new ``kernel`` dimension (os_user reports it unsupported
# — shared host kernel); sandboxed snapshots report
# enforced=(identity, filesystem, environment, process, resources) with
# kernel/network_egress unverified-until-probed; evaluate_isolation_requirement
# gates sandboxed requests on the probe reasons instead of the OS-account
# chain.
POLICY_REVISION = "2026-09-16.1"

ISOLATION_LEVEL_NONE = "none"
ISOLATION_LEVEL_OS_USER = "os_user"
ISOLATION_LEVEL_SANDBOXED = "sandboxed"

_LEVEL_ORDER = {
    ISOLATION_LEVEL_NONE: 0,
    ISOLATION_LEVEL_OS_USER: 1,
    ISOLATION_LEVEL_SANDBOXED: 2,
}
SUPPORTED_ISOLATION_LEVELS = tuple(_LEVEL_ORDER)

DIMENSION_IDENTITY = "identity"
DIMENSION_FILESYSTEM = "filesystem"
DIMENSION_ENVIRONMENT = "environment"
DIMENSION_PROCESS = "process"
DIMENSION_RESOURCES = "resources"
DIMENSION_NETWORK_EGRESS = "network_egress"
DIMENSION_KERNEL = "kernel"

ALL_DIMENSIONS = (
    DIMENSION_IDENTITY,
    DIMENSION_FILESYSTEM,
    DIMENSION_ENVIRONMENT,
    DIMENSION_PROCESS,
    DIMENSION_RESOURCES,
    DIMENSION_NETWORK_EGRESS,
    DIMENSION_KERNEL,
)

_OS_USER_ENFORCED = (
    DIMENSION_IDENTITY,
    DIMENSION_FILESYSTEM,
    DIMENSION_ENVIRONMENT,
    DIMENSION_PROCESS,
)
# Issue #3378: os_user shares the host kernel by definition (module docstring),
# so the kernel dimension is honestly reported as unsupported.
_OS_USER_UNSUPPORTED = (DIMENSION_RESOURCES, DIMENSION_NETWORK_EGRESS, DIMENSION_KERNEL)

# Config-derived facts for sandboxed WebUI pods: one pod per instance with a
# per-instance token secret, an image_allowlisted digest-pinned image, and
# resource limits that build_create_request always attaches (defaults are
# bounds too). Kernel and egress enforcement need a live pod probe (D3).
_SANDBOXED_ENFORCED = (
    DIMENSION_IDENTITY,
    DIMENSION_FILESYSTEM,
    DIMENSION_ENVIRONMENT,
    DIMENSION_PROCESS,
    DIMENSION_RESOURCES,
)
_SANDBOXED_UNSUPPORTED = (DIMENSION_KERNEL, DIMENSION_NETWORK_EGRESS)

BACKEND_PER_USER = "qwen-code-webui-per-user"
BACKEND_SHARED = "qwen-code-webui-shared"
BACKEND_OPENSANDBOX = "opensandbox"

# Reason codes produced by the sandboxed readiness probe; the user-url gate
# surfaces one of these when a sandboxed request outruns the snapshot level.
# Review round 1 (T-F): sandbox_proxy_token_ttl_too_short is GONE — a short
# effective webui proxy-token TTL is not a deployment defect of the sandboxed
# level: the launcher clamps the pod TTL to min(webui token TTL, proxy token
# TTL) at create time and re-clamps at every renew, so the pod never outlives
# its baked-in credential regardless of the TTL configuration. Forcing the
# old gate's ≥1440min requirement meant every sandboxed declaration also
# lengthened the LOCAL webui credentials' floor — a security regression.
SANDBOX_PROBE_REASON_CODES = (
    "sandbox_backend_unconfigured",
    "sandbox_tier_missing",
    "webui_image_missing",
    "webui_image_not_pinned",
    "webui_image_not_allowed",
    "sandbox_api_key_missing",
    "sandbox_proxy_unreachable",
    "sandbox_multi_process_unsupported",
)

# Issue #3378 (D3): the boot-probe upgrade memo, keyed per tier. Kernel and
# egress enforcement are only verifiable per-pod, so the sandboxed snapshot
# reports them unverified until the WebUI launcher's first successful pod probe
# on THAT tier registers the upgrade here. Per-tier keying (conformance review
# Q1): in a multi-tier deployment (gVisor + Kata) a later Kata pod must not
# overwrite a gVisor tier's earlier positive kernel upgrade (and vice versa) —
# each snapshot consults only its own tier's memo. A restart reverts every
# tier to the static (unverified) view, which is the documented honesty
# contract.
#
# Review round 1 (T-L): the memo is write-only NO MORE — (a) a FAILED pod
# probe on a tier revokes that tier's entry (a tier whose last launch could
# not verify itself must not keep riding a stale upgrade), and (b) entries
# carry a timestamp and expire after SANDBOX_RUNTIME_MEMO_TTL_SECONDS, so a
# long-lived control plane cannot lean on hours-old probe evidence.
_SANDBOX_RUNTIME_MEMO: dict[str, dict[str, Any]] = {}
SANDBOX_RUNTIME_MEMO_TTL_SECONDS = 3600.0


def register_sandbox_runtime_verified(
    *, tier: str, kernel_enforced: bool, egress_enforced: bool = False
) -> None:
    """Record that a sandbox pod's boot probes passed on ``tier`` (launcher hook).

    Called by ``SandboxedWebuiLauncher`` after the runtime-class and egress
    probes confirm the pod on the tier the pod was launched against.
    ``kernel_enforced`` distinguishes the two probe directions (provider
    ``_run_probes``): gVisor identifies itself positively in the kernel probe,
    while a Kata result is negative-only — it rules out gVisor but cannot
    distinguish Kata from an unisolated runc container, so the kernel dimension
    must NOT upgrade on it (reason ``sandbox_runtime_kata_negative_only``).
    ``egress_enforced`` (T-M) upgrades network_egress ONLY for a sidecar-
    attestation tier whose /policy the probe actually read: on gVisor/CNI
    tiers the cluster-egress check is a negative control (deny-path only),
    which never proves an allow flows — those stay unverified with
    ``sandbox_runtime_egress_negative_only``.
    """
    _SANDBOX_RUNTIME_MEMO[str(tier)] = {
        "verified": True,
        "kernel_enforced": bool(kernel_enforced),
        "egress_enforced": bool(egress_enforced),
        "ts": time.time(),
    }


def revoke_sandbox_runtime_verification(tier: str) -> None:
    """Drop *tier*'s memo entry (launcher boot-probe failure hook, T-L).

    The launcher calls this when a pod's boot probes fail and the pod is
    destroyed: whatever the tier's memo claimed, the most recent evidence is
    that the tier's runtime cannot be verified right now. Deleting (not
    downgrading) means the snapshot falls back to the honest static view
    until a fresh successful probe re-registers.
    """
    _SANDBOX_RUNTIME_MEMO.pop(str(tier), None)


def sandbox_runtime_verification(tier: str) -> tuple[bool, bool, bool]:
    """Return ``(verified, kernel_enforced, egress_enforced)`` for *tier*.

    An entry older than SANDBOX_RUNTIME_MEMO_TTL_SECONDS reads as absent
    (T-L): the upgrade evidence is per-pod and per-boot, never permanent.
    """
    state = _SANDBOX_RUNTIME_MEMO.get(str(tier))
    if state is None:
        return (False, False, False)
    try:
        age = time.time() - float(state.get("ts") or 0)
    except (TypeError, ValueError):
        return (False, False, False)
    if age > SANDBOX_RUNTIME_MEMO_TTL_SECONDS:
        return (False, False, False)
    return (
        bool(state["verified"]),
        bool(state["kernel_enforced"]),
        bool(state.get("egress_enforced", False)),
    )


def _reset_sandbox_runtime_verification() -> None:
    """Reset the boot-probe memo for every tier (test isolation only)."""
    _SANDBOX_RUNTIME_MEMO.clear()


# --- Entry-point matrix (design doc §2.4, recalibrated by Issue #3410) ------
# Status vocabulary. An integrator MUST fail closed on an unknown status.
ENTRY_POINT_STATUS_ENFORCED = "enforced"
ENTRY_POINT_STATUS_PARTIAL = "partial"
ENTRY_POINT_STATUS_REMOTE_SCOPE = "remote_machine_scope"
ENTRY_POINT_STATUS_SEPARATE = "separate_contract"
ENTRY_POINT_STATUS_SANDBOX_UNWIRED = "sandboxed_entry_not_wired"
# RESERVED, and deliberately never emitted by any snapshot today: Open ACE has
# no server-side entry kill switch (docs §8). The token is defined so an
# integrator can write its admission check once; a deployment that gains the
# switch will emit it without a contract-shape change. A unit test pins that
# nothing emits it.
ENTRY_POINT_STATUS_DISABLED = "disabled"

ENTRY_POINT_SCOPE_LOCAL = "local_workspace"
ENTRY_POINT_SCOPE_REMOTE = "remote_machine"
ENTRY_POINT_SCOPE_SEPARATE = "separate_contract"
ENTRY_POINT_SCOPES = (
    ENTRY_POINT_SCOPE_LOCAL,
    ENTRY_POINT_SCOPE_REMOTE,
    ENTRY_POINT_SCOPE_SEPARATE,
)

# Symlink policy tokens for a single operation.
# The requested path is resolved and rejected if it lands outside the declared
# roots, AND below the home root no symlink is ever followed by a process with
# more privilege than the target account (#3410 review): a root process
# descends through O_NOFOLLOW directory fds; every other branch runs as that
# account, which can only reach what its own shell could.
_SYMLINK_NO_ELEVATED_FOLLOW = "never_followed_with_elevated_privilege"
# The requested path is resolved and rejected if it lands outside the roots.
_SYMLINK_RESOLVED_THEN_CHECKED = "resolved_then_rejected_if_outside"
_SYMLINK_NA = "not_applicable"

# Root tokens an operation admits.
_ROOTS_HOME = ("home",)
_ROOTS_HOME_SHARED = ("home", "shared_projects")
_ROOTS_CREATABLE = (
    "home",
    "shared_projects",
    "workspace_root",
    "workspace_root_first_level_non_home",
)

_REMOTE_SCOPE_LIMITATION = {
    "code": "remote_execution_not_isolated_by_this_level",
    "message": (
        "The local isolation level says nothing about a remote machine's own "
        "user separation; a deployment that needs it must not grant machine "
        "assignments."
    ),
}

ENTRY_POINT_DETAILS: dict[str, dict[str, Any]] = {
    "webui": {
        "status": ENTRY_POINT_STATUS_ENFORCED,
        "scope": ENTRY_POINT_SCOPE_LOCAL,
        "covered_by_isolation_level": True,
        "operations": [
            {"name": "user-url", "roots": list(_ROOTS_HOME), "symlink_policy": _SYMLINK_NA},
            {"name": "prestart", "roots": list(_ROOTS_HOME), "symlink_policy": _SYMLINK_NA},
            {"name": "stop", "roots": [], "symlink_policy": _SYMLINK_NA},
            {"name": "token-validate", "roots": [], "symlink_policy": _SYMLINK_NA},
        ],
        "access_control": ["per_user_instance", "per_instance_token", "os_account_uid"],
        "boundary": (
            "One qwen-code-webui instance per user, launched under that user's OS "
            "account and port; stop and token revocation are keyed by user_id."
        ),
        "limitations": [],
        "residuals": [],
    },
    "filesystem_api": {
        "status": ENTRY_POINT_STATUS_ENFORCED,
        "scope": ENTRY_POINT_SCOPE_LOCAL,
        "covered_by_isolation_level": True,
        # Per-operation, because the admissible root set is NOT uniform: browse
        # and check-path reach shared projects, check-path/create-directory also
        # admit the workspace root and its non-home first-level children, and
        # the per-file paths are home-only. One prose sentence could not be
        # true of all eight (Issue #3410 review).
        "operations": [
            {
                "name": "browse",
                "roots": list(_ROOTS_HOME_SHARED),
                "symlink_policy": _SYMLINK_RESOLVED_THEN_CHECKED,
            },
            {
                "name": "check-path",
                "roots": list(_ROOTS_CREATABLE),
                "symlink_policy": _SYMLINK_RESOLVED_THEN_CHECKED,
            },
            {
                "name": "create-directory",
                "roots": list(_ROOTS_CREATABLE),
                "symlink_policy": _SYMLINK_RESOLVED_THEN_CHECKED,
            },
            {"name": "home", "roots": list(_ROOTS_HOME), "symlink_policy": _SYMLINK_NA},
        ]
        + [
            {
                "name": name,
                "roots": list(_ROOTS_HOME),
                "symlink_policy": _SYMLINK_NO_ELEVATED_FOLLOW,
            }
            for name in ("upload", "download", "delete-file", "search")
        ],
        "access_control": [
            "home_subtree_lock",
            "shared_project_acl",
            "os_account_dac",
            "nofollow_fd_descent",
        ],
        "boundary": (
            "Every /api/fs operation is locked to the roots its row declares: the "
            "caller's own per-base home roots (<workspace base>/<account>), plus "
            "the tenant's explicitly shared project roots for browse/check-path, "
            "plus the workspace root itself and its first-level children that are "
            "not another user's home root for check-path/create-directory. Every "
            "requested path is resolved and rejected if it lands outside that set. "
            "upload, download, delete-file and search then never follow a symlink "
            "with more privilege than the target account (Issue #3410): a root "
            "process reaches the target through directory fds opened with "
            "O_NOFOLLOW from the home root, whose owner it asserts; package non-root "
            "deployments delegate to wrappers that probe, write and delete as that "
            "account. browse, check-path and create-directory run as the target "
            "account for every user with a system_account."
        ),
        "limitations": [],
        "residuals": [
            {
                "code": "shared_project_roots_are_cross_user_by_design",
                "message": (
                    "browse/check-path also admit the tenant's explicitly shared "
                    "project roots; those are cross-user on purpose and fenced by "
                    "a per-tenant OS group, not by the home lock."
                ),
            },
            {
                "code": "account_scoped_wrappers_required_for_package_non_root",
                "message": (
                    "On package non-root multi-user deployments uploads and deletes "
                    "go through the openace-write-as and openace-rm wrappers, which "
                    "probe, write and delete as the target account. This is a "
                    "residual rather than a limitation because the route checks each "
                    "installed wrapper's capability marker and REFUSES the operation "
                    "(500, fail closed) when it predates the hardening, so the entry "
                    "never silently degrades."
                ),
            },
            {
                "code": "unmapped_users_act_as_the_web_process",
                "message": (
                    "A user without a system_account works inside the "
                    "username-derived <base>/<username> as the web process: uploads "
                    "are not chowned, and on a root process browse, check-path and "
                    "create-directory act on that path by name. Such a user cannot "
                    "start a local workspace (the launcher answers "
                    "identity_mapping_missing), so it has no workspace shell to plant "
                    "symlinks with; the per-file operations still never follow one; "
                    "and a username that is another user's system_account gets no "
                    "home at all. Map every user to a system_account."
                ),
            },
        ],
    },
    "session_history": {
        "status": ENTRY_POINT_STATUS_ENFORCED,
        "scope": ENTRY_POINT_SCOPE_LOCAL,
        "covered_by_isolation_level": True,
        "operations": [
            {"name": "list", "roots": [], "symlink_policy": _SYMLINK_NA},
            {"name": "get", "roots": [], "symlink_policy": _SYMLINK_NA},
            {"name": "restore", "roots": [], "symlink_policy": _SYMLINK_NA},
        ],
        "access_control": ["session_owner", "tenant_fail_closed"],
        "boundary": "Application-layer ownership gate plus a fail-closed tenant check.",
        "limitations": [],
        "residuals": [],
    },
    "terminal": {
        "status": ENTRY_POINT_STATUS_REMOTE_SCOPE,
        "scope": ENTRY_POINT_SCOPE_REMOTE,
        "covered_by_isolation_level": False,
        "operations": [
            {"name": name, "roots": [], "symlink_policy": _SYMLINK_NA}
            for name in ("start", "attach", "status", "stop", "ws")
        ],
        "access_control": [
            "machine_assignment_acl",
            "session_owner",
            "tenant_fail_closed",
            "platform_admin",
        ],
        "boundary": (
            "This entry allocates no local workspace path, account or token: it "
            "attaches to a shell on a REMOTE machine registered with Open ACE, "
            "running under that machine's agent account. Access is the machine "
            "assignment ACL plus session ownership (#3376); whether a user may "
            "reach a remote machine at all is an ACL decision, not an isolation "
            "level decision."
        ),
        "limitations": [_REMOTE_SCOPE_LIMITATION],
        "residuals": [],
    },
    "vscode": {
        "status": ENTRY_POINT_STATUS_REMOTE_SCOPE,
        "scope": ENTRY_POINT_SCOPE_REMOTE,
        "covered_by_isolation_level": False,
        "operations": [
            {"name": name, "roots": [], "symlink_policy": _SYMLINK_NA}
            for name in ("start", "attach", "status", "stop", "proxy", "ws")
        ],
        "access_control": [
            "machine_assignment_acl",
            "session_owner",
            "tenant_fail_closed",
            "machine_admin",
            "platform_admin",
        ],
        "boundary": (
            "code-server on a REMOTE machine; ownership is the requester "
            "(#3376 VSCodeOwnerStore), and the proxy/WS paths share the same "
            "gate. Same remote-scope caveat as `terminal`."
        ),
        "limitations": [_REMOTE_SCOPE_LIMITATION],
        "residuals": [],
    },
    "autonomous": {
        "status": ENTRY_POINT_STATUS_SEPARATE,
        "scope": ENTRY_POINT_SCOPE_SEPARATE,
        "covered_by_isolation_level": False,
        "operations": [{"name": "task-execution", "roots": [], "symlink_policy": _SYMLINK_NA}],
        "access_control": ["sandbox_effective_policy"],
        "boundary": "Governed by the #2022 sandbox contract; see docs/SANDBOX_BACKENDS.md.",
        "limitations": [
            {
                "code": "separate_contract",
                "message": (
                    "Autonomous task isolation is declared by sandbox_effective_policy, "
                    "not by this contract."
                ),
            }
        ],
        "residuals": [],
    },
}

ENTRY_POINT_STATUSES = {name: d["status"] for name, d in ENTRY_POINT_DETAILS.items()}

# T-K (review round 1): the matrix is PER-LEVEL. On a sandboxed snapshot only
# the webui entry rides the pod — terminal/vscode/filesystem_api executors
# still run on the control-plane host and are NOT wired to the user's sandbox
# instance, so reporting the os_user status would claim coverage that does not
# exist. session_history is enforced via the per-pod snapshot store;
# autonomous stays a separate contract.
_SANDBOX_UNWIRED_LIMITATION = {
    "code": "sandboxed_entry_not_wired",
    "message": (
        "The executor for this entry still runs on the control-plane host and is "
        "not wired to the user's sandbox pod; the user's files live inside the pod."
    ),
}
_SANDBOX_UNWIRED_ENTRIES = ("filesystem_api", "terminal", "vscode")
# The os_user boundary/residuals of filesystem_api describe the HOST /fs tree,
# which a sandboxed user's files are not in (#3410 review); terminal/vscode keep
# their own text, which is about the remote machine and still true.
_SANDBOXED_ENTRY_OVERRIDES: dict[str, dict[str, Any]] = {
    "filesystem_api": {
        "boundary": (
            "The /api/fs executor runs on the control-plane host and is not wired "
            "to the user's sandbox pod; a sandboxed user's files live inside the "
            "pod and are browsed through the WebUI's own file view."
        ),
        "residuals": [],
    },
}

ENTRY_POINT_DETAILS_SANDBOXED: dict[str, dict[str, Any]] = {
    name: (
        detail
        if name not in _SANDBOX_UNWIRED_ENTRIES
        else {
            **detail,
            "status": ENTRY_POINT_STATUS_SANDBOX_UNWIRED,
            "covered_by_isolation_level": False,
            # Keep the entry's own gaps (terminal/vscode: remote scope) and add
            # the sandbox one; replacing them dropped the remote-scope caveat.
            "limitations": [*detail["limitations"], _SANDBOX_UNWIRED_LIMITATION],
            **_SANDBOXED_ENTRY_OVERRIDES.get(name, {}),
        }
    )
    for name, detail in ENTRY_POINT_DETAILS.items()
}

ENTRY_POINTS_SANDBOXED = {name: d["status"] for name, d in ENTRY_POINT_DETAILS_SANDBOXED.items()}


def _public_entry_detail(detail: dict[str, Any]) -> dict[str, Any]:
    """Deep-ish copy of one entry detail for the public payload."""
    return {
        "status": detail["status"],
        "scope": detail["scope"],
        "covered_by_isolation_level": detail["covered_by_isolation_level"],
        "operations": [{**op, "roots": list(op["roots"])} for op in detail["operations"]],
        "access_control": list(detail["access_control"]),
        "boundary": detail["boundary"],
        "limitations": [dict(x) for x in detail["limitations"]],
        "residuals": [dict(x) for x in detail["residuals"]],
    }


def _current_platform() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    return sys.platform


def is_valid_isolation_level(value: str) -> bool:
    return value in _LEVEL_ORDER


def isolation_level_at_least(have: str, want: str) -> bool:
    return _LEVEL_ORDER[have] >= _LEVEL_ORDER[want]


@dataclass(frozen=True)
class IsolationReason:
    """A machine-readable cause code with a human-readable explanation."""

    code: str
    message: str

    def public_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True)
class IsolationCapabilitySnapshot:
    """Immutable view of what this deployment enforces for local workspaces."""

    supported: bool
    backend: str
    isolation_level: str
    enforced: tuple[str, ...]
    unsupported: tuple[str, ...]
    reasons: tuple[IsolationReason, ...]
    policy_revision: str = POLICY_REVISION

    def public_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "local_workspace_multi_user": "supported" if self.supported else "unsupported",
            "backend": self.backend,
            "isolation_level": self.isolation_level,
            "enforced": list(self.enforced),
            "unsupported": list(self.unsupported),
            "reasons": [r.public_dict() for r in self.reasons],
            "policy_revision": self.policy_revision,
        }
        # Issue #3374 review #11: the entry-point matrix describes multi-user
        # enforcement; emitting it on an unsupported snapshot would claim
        # "webui: enforced" next to "no isolation at all". T-K: the matrix is
        # level-aware — a sandboxed snapshot reports its own wiring truth.
        if self.supported:
            sandboxed = self.isolation_level == ISOLATION_LEVEL_SANDBOXED
            details = ENTRY_POINT_DETAILS_SANDBOXED if sandboxed else ENTRY_POINT_DETAILS
            data["entry_points"] = {name: d["status"] for name, d in details.items()}
            # Issue #3410: the string map alone could not tell an integrator
            # "covered by the declared level" from "governed by a different
            # mechanism" from "real gap". The detail map is the machine-readable
            # answer; the string map stays for existing consumers. `limitations`
            # gate admission, `residuals` are informational and must not.
            data["entry_point_details"] = {
                name: _public_entry_detail(d) for name, d in details.items()
            }
        return data


def _unsupported(reason_code: str, message: str) -> IsolationCapabilitySnapshot:
    return IsolationCapabilitySnapshot(
        supported=False,
        backend=BACKEND_SHARED,
        isolation_level=ISOLATION_LEVEL_NONE,
        enforced=(),
        unsupported=ALL_DIMENSIONS,
        reasons=(IsolationReason(reason_code, message),),
    )


def _peer_web_processes_live() -> bool:
    """Whether another live web process holds a fresh heartbeat.

    Conservative in both directions that matter: an unreadable/errored
    heartbeat root counts as "peers present" (we cannot prove this process
    is alone, and per-instance secrets cannot span replicas anyway), while
    a readable root with no fresh peer heartbeats counts as single-process.
    Late-imported to keep the contract importable without the sandbox
    module (and to avoid its gevent dependencies on cold paths).
    """
    try:
        from app.services.webui_sandbox import fresh_peer_heartbeats

        return bool(fresh_peer_heartbeats())
    except Exception:  # noqa: BLE001 - cannot prove aloneness -> refuse
        return True


def _sandboxed_readiness(config: Any) -> tuple[bool, str, IsolationReason | None]:
    """Zero-pod fail-closed probe for the sandboxed level (Issue #3378).

    Verifies the configuration plane only — backend config parses, a tier
    exists, a digest-pinned allowlisted webui image is set, and the control
    plane's LLM-proxy URL would be reachable under the tier's egress policy.
    Never creates a pod; kernel/egress enforcement stays
    ``sandbox_runtime_unverified`` until the first successful pod boot probe
    upgrades it (launcher-side, in-process memo).

    Review round 1 (T-F): the proxy-token TTL check is deliberately ABSENT.
    The launch path clamps the pod TTL to min(webui token TTL, effective
    proxy token TTL) at create and re-clamps at every renew, so a short TTL
    never strands a pod past its LLM credential; gating here would only have
    forced deployments to raise OPENACE_PROXY_TOKEN_TTL_WEBUI_MINUTES — which
    lengthens the LOCAL webui credentials too (the TTL is process-global), a
    security regression — and made this read-only capability probe construct
    the database-backed APIKeyProxyService on every cold GET.

    Returns ``(ok, tier, reason)``; on failure ``reason`` carries one of
    :data:`SANDBOX_PROBE_REASON_CODES`.
    """
    from app.modules.workspace.autonomous.sandbox.opensandbox.config import load_backend_config

    try:
        backend_cfg = load_backend_config()
    except Exception as exc:  # noqa: BLE001 - fail closed on any config error
        return (
            False,
            "",
            IsolationReason(
                "sandbox_backend_unconfigured",
                f"The sandbox backend config cannot be loaded ({exc}); the "
                "sandboxed isolation level is unavailable until it is repaired.",
            ),
        )
    if backend_cfg is None:
        return (
            False,
            "",
            IsolationReason(
                "sandbox_backend_unconfigured",
                "No OpenSandbox backend is configured on this deployment; the "
                "sandboxed isolation level is unavailable.",
            ),
        )

    tier = (getattr(config, "sandbox_tier", "") or "").strip() or backend_cfg.default_tier
    endpoint = backend_cfg.endpoints.get(tier)
    if endpoint is None:
        return (
            False,
            tier,
            IsolationReason(
                "sandbox_tier_missing",
                f"Sandbox endpoint tier {tier!r} does not exist in the backend "
                "config; the sandboxed isolation level is unavailable.",
            ),
        )

    image = (endpoint.webui_image or "").strip()
    if not image:
        return (
            False,
            tier,
            IsolationReason(
                "webui_image_missing",
                f"Sandbox endpoint tier {tier!r} has no webui_image configured; "
                "the sandboxed isolation level needs an image containing "
                "qwen-code-webui.",
            ),
        )
    from app.modules.workspace.autonomous.sandbox.opensandbox.config import SandboxConfigError

    try:
        # Same digest-pinning discipline as default_image, but validated here
        # so a bad value degrades only the sandboxed level, not the shared
        # backend config autonomous tasks depend on.
        from app.modules.workspace.autonomous.sandbox.opensandbox.config import (
            _require_digest_pinned,
        )

        _require_digest_pinned(image, f"endpoint {tier!r} webui_image")
    except SandboxConfigError as exc:
        return False, tier, IsolationReason("webui_image_not_pinned", str(exc))
    if backend_cfg.image_allowlist and image not in backend_cfg.image_allowlist:
        return (
            False,
            tier,
            IsolationReason(
                "webui_image_not_allowed",
                f"webui_image for tier {tier!r} is not in image_allowlist; "
                "refusing to launch interactive pods from an unlisted image.",
            ),
        )

    # T-J (review round 1): a tier whose API key env var is empty in THIS
    # process cannot create pods — the launcher's first API call fails
    # upstream. The contract and the gate must not declare sandboxed on the
    # same host where the launch path cannot succeed (#3375's "no
    # contradictory verdicts for one host" principle).
    api_key_env = getattr(endpoint, "api_key_env", "") or ""
    if not os.environ.get(api_key_env, "").strip():
        return (
            False,
            tier,
            IsolationReason(
                "sandbox_api_key_missing",
                f"The tier's API key environment variable {api_key_env!r} is "
                "not set in this process; sandboxed WebUI pods cannot be "
                "created (the autonomous backend config names it via "
                "api_key_env).",
            ),
        )

    # Review follow-up: per-instance token secrets are process-memory state.
    # With another live web process (the shipped k8s manifest runs 3
    # replicas), a pod's secret is invisible to the other replicas and
    # roughly two thirds of token validations would 401. The contract must
    # not declare a level whose auth is unreliable — same fail-closed class
    # as api_key_env. fresh_peer_heartbeats() is the liveness answer this PR
    # already built for the reconcile mutex.
    if _peer_web_processes_live():
        return (
            False,
            tier,
            IsolationReason(
                "sandbox_multi_process_unsupported",
                "Another live web process is present (per its heartbeat); "
                "sandboxed token validation and instance management are "
                "per-process in-memory state and cannot span replicas — the "
                "sandboxed level is only declarable on a single web process.",
            ),
        )

    # Proxy reachability: the pod must reach the control plane's LLM proxy.
    # The static probe needs a URL without a request in hand, so it uses the
    # configured webui_callback_url — deployments wanting the sandboxed level
    # must set it (the per-request host is only known at launch time).
    callback_url = (getattr(config, "webui_callback_url", "") or "").strip()
    if not callback_url:
        return (
            False,
            tier,
            IsolationReason(
                "sandbox_proxy_unreachable",
                "workspace.webui_callback_url is not set; the control-plane URL "
                "sandbox pods must reach back for the LLM proxy cannot be "
                "verified without it.",
            ),
        )
    from app.modules.workspace.autonomous.sandbox.opensandbox import policy as sandbox_policy

    try:
        sandbox_policy.assert_proxy_reachable({"OPENACE_PROXY_URL": callback_url}, endpoint)
    except Exception as exc:  # noqa: BLE001 - fail closed on any reachability error
        return (
            False,
            tier,
            IsolationReason(
                "sandbox_proxy_unreachable",
                f"The sandbox egress policy would block the control-plane LLM proxy ({exc}).",
            ),
        )
    return True, tier, None


def build_workspace_isolation_snapshot(
    manager: Any = None,
) -> IsolationCapabilitySnapshot:
    """Derive the isolation capability snapshot from live runtime state.

    When a manager is available the snapshot also consults the launch-path
    readiness probe, so the contract and the /user-url gate can never
    disagree about the same host (Issue #3374 review #4). Without a manager
    (read-only capability GET before any workspace activity) the snapshot is
    derived from disk config only and cannot verify the launch path — that
    limitation is documented in the capability docs.

    Issue #3378: the sandboxed probe runs first and independently — it does
    not check platform or multi_user_mode (the pods live on a remote
    cluster; single-user + sandboxed is a legitimate hardening). When it
    passes the snapshot reports the sandboxed level with kernel/egress
    unverified-until-probed. When it fails the os_user chain below runs
    exactly as before, with the sandbox failure reason attached whenever a
    sandbox backend is actually configured (an unconfigured backend adds no
    noise — that is every default deployment).
    """
    readiness_probe = None
    config: Any = None
    if manager is None:
        from app.services.webui_manager import peek_webui_manager

        manager = peek_webui_manager()
    if manager is None:
        from app.services.webui_manager import read_workspace_config

        config = read_workspace_config()
    else:
        config = getattr(manager, "config", None)
        readiness_probe = getattr(manager, "per_user_launch_readiness", None)

    if config is None or not getattr(config, "enabled", False):
        return _unsupported(
            "webui_disabled",
            "WebUI manager is disabled; no interactive workspace runtime is available.",
        )

    sandbox_ok, sandbox_tier, sandbox_reason = _sandboxed_readiness(config)
    if sandbox_ok:
        # T-K (review round 1): a cold worker (no manager singleton yet) has
        # not exercised the sandbox launch path — mark the level provisional
        # exactly like the os_user chain's launch_path_unverified (#3375
        # precedent): the manager appearing (first workspace activity)
        # removes the marker.
        cold_reasons: tuple[IsolationReason, ...] = ()
        if readiness_probe is None:
            cold_reasons = (
                IsolationReason(
                    "sandbox_launch_unverified",
                    "The sandboxed launch path has not been exercised on this "
                    "worker yet; treat this level as provisional until the "
                    "WebUI manager is initialized.",
                ),
            )
        # D3 two-state mapping: the static view reports kernel/egress
        # unverified; once the launcher's first pod boot probe succeeded the
        # in-process memo upgrades them. Kernel only upgrades on a positive
        # gVisor identification (T-M: Kata is negative-only); network_egress
        # only on a sidecar-attestation tier whose /policy was read live
        # (T-M: a gVisor/CNI tier's cluster-egress probe is a negative
        # control — deny-path evidence, never proof an allow flows — same
        # treatment as the kata kernel direction). The memo is keyed per tier
        # (review Q1): a pod verified on another tier never upgrades THIS
        # tier's snapshot.
        verified, kernel_enforced, egress_enforced = sandbox_runtime_verification(sandbox_tier)
        if verified:
            enforced: tuple[str, ...] = _SANDBOXED_ENFORCED
            reasons = list(cold_reasons)
            if egress_enforced:
                enforced = enforced + (DIMENSION_NETWORK_EGRESS,)
            else:
                reasons.append(
                    IsolationReason(
                        "sandbox_runtime_egress_negative_only",
                        "The tier's egress enforcement was verified in the "
                        "negative direction only (the cluster deny-default "
                        "control proves a deny path exists, not that an allow "
                        "actually flows); only an egress sidecar whose /policy "
                        "was read live upgrades network_egress, so the "
                        "dimension stays unverified on gVisor/CNI tiers.",
                    )
                )
            if kernel_enforced:
                enforced = enforced + (DIMENSION_KERNEL,)
            else:
                reasons.append(
                    IsolationReason(
                        "sandbox_runtime_kata_negative_only",
                        "The runtime boot probe passed in the negative "
                        "direction only (Kata rules out gVisor but no "
                        "hypervisor signal is observable), so the kernel "
                        "dimension stays unverified.",
                    )
                )
            return IsolationCapabilitySnapshot(
                supported=True,
                backend=f"{BACKEND_OPENSANDBOX}:{sandbox_tier}",
                isolation_level=ISOLATION_LEVEL_SANDBOXED,
                enforced=enforced,
                unsupported=tuple(d for d in ALL_DIMENSIONS if d not in enforced),
                reasons=tuple(reasons),
            )
        return IsolationCapabilitySnapshot(
            supported=True,
            backend=f"{BACKEND_OPENSANDBOX}:{sandbox_tier}",
            isolation_level=ISOLATION_LEVEL_SANDBOXED,
            enforced=_SANDBOXED_ENFORCED,
            unsupported=_SANDBOXED_UNSUPPORTED,
            reasons=cold_reasons
            + (
                IsolationReason(
                    "sandbox_runtime_unverified",
                    "The sandboxed level is verified on the configuration "
                    "plane only; kernel and egress enforcement are confirmed "
                    "per-pod by boot probes after the first launch (the memo "
                    "resets on control-plane restart, when a probe on the "
                    "tier fails, or after its 1h TTL).",
                ),
            ),
        )
    # A configured-but-failing sandbox backend is observable on the os_user
    # snapshot so the gate can surface the exact reason for sandboxed
    # requests; an absent backend (every default deployment) stays silent.
    sandbox_reasons: tuple[IsolationReason, ...] = ()
    if sandbox_reason is not None and sandbox_reason.code != "sandbox_backend_unconfigured":
        sandbox_reasons = (sandbox_reason,)

    # Linux only: macOS skips system-user creation (utils/workspace.py), so
    # Open ACE cannot establish or verify the identity mapping there; Windows
    # forces a single shared instance.
    if _current_platform() != "linux":
        return _with_extra_reasons(
            _unsupported(
                "platform_unsupported",
                "Per-user workspace isolation is supported on Linux deployments "
                "only; this platform runs a single shared WebUI instance.",
            ),
            sandbox_reasons,
        )

    if not getattr(config, "multi_user_mode", False):
        return _with_extra_reasons(
            _unsupported(
                "multi_user_mode_disabled",
                "Multi-user workspace mode is disabled; the interactive workspace "
                "intentionally runs one shared instance.",
            ),
            sandbox_reasons,
        )

    # The launch-path readiness probe is the verification: it checks the
    # actual per-user launch mechanism (WebUI resolution, dev-directory mode,
    # launch wrapper, sudo) regardless of Docker vs package installation —
    # a package-method host that really runs `sudo -u` reports os_user.
    # Without a manager (read-only capability GET on a cold worker) the
    # probe cannot run; the snapshot is reported as provisional via an
    # explicit reason instead of silently claiming a verified level.
    if readiness_probe is not None:
        degradation = readiness_probe()
        if degradation:
            return _with_extra_reasons(
                _unsupported(
                    "launch_path_degraded",
                    "This deployment's WebUI launch path cannot host per-user "
                    f"instances ({degradation}); see the workspace isolation "
                    "documentation.",
                ),
                sandbox_reasons,
            )
        return IsolationCapabilitySnapshot(
            supported=True,
            backend=BACKEND_PER_USER,
            isolation_level=ISOLATION_LEVEL_OS_USER,
            enforced=_OS_USER_ENFORCED,
            unsupported=_OS_USER_UNSUPPORTED,
            reasons=sandbox_reasons,
        )

    return IsolationCapabilitySnapshot(
        supported=True,
        backend=BACKEND_PER_USER,
        isolation_level=ISOLATION_LEVEL_OS_USER,
        enforced=_OS_USER_ENFORCED,
        unsupported=_OS_USER_UNSUPPORTED,
        reasons=(
            IsolationReason(
                "launch_path_unverified",
                "The WebUI launch path has not been probed on this worker "
                "yet; treat this level as provisional until the manager is "
                "initialized.",
            ),
        )
        + sandbox_reasons,
    )


def _with_extra_reasons(
    snapshot: IsolationCapabilitySnapshot, extra: tuple[IsolationReason, ...]
) -> IsolationCapabilitySnapshot:
    if not extra:
        return snapshot
    return IsolationCapabilitySnapshot(
        supported=snapshot.supported,
        backend=snapshot.backend,
        isolation_level=snapshot.isolation_level,
        enforced=snapshot.enforced,
        unsupported=snapshot.unsupported,
        reasons=snapshot.reasons + extra,
    )


def resolve_required_floor(config: Any, snapshot: IsolationCapabilitySnapshot) -> str:
    """Resolve the server-side isolation floor (Issue #3374 PR review round 2).

    An explicitly configured ``workspace.required_isolation_level`` wins when
    valid; unset — or a hand-edited invalid value (warned) — derives from what
    the deployment actually verifies (``snapshot.isolation_level``), NOT from
    a flat mode default: a package-method or launch-degraded host must not
    have every default workspace request rejected by an unreachable floor.
    The request parameter can only raise above this floor, never lower it.
    """
    explicit = (getattr(config, "required_isolation_level", "") or "").strip()
    degraded = snapshot.isolation_level == ISOLATION_LEVEL_NONE and _reason_is_degraded(snapshot)
    if degraded:
        # The floor follows verified capability — it can only mirror a
        # degradation downward, never detect one. Make BOTH directions
        # audible (PR review round 5): a pinned floor above a degraded
        # probe rejects every launch and must not fail silently, while a
        # derived multi-user floor keeps serving on the shared account.
        # Late import avoids a logging dependency at module import time.
        import logging

        # PR review round 6: only a VALID pin above the degraded probe
        # actually rejects; an invalid value falls through to the derived
        # floor below and must not claim rejections that are not happening.
        if explicit and is_valid_isolation_level(explicit) and explicit != ISOLATION_LEVEL_NONE:
            logging.getLogger(__name__).warning(
                "Workspace launch path is degraded (%s); the pinned "
                "required_isolation_level '%s' is above what this host can "
                "verify and will REJECT all launches until the launch path "
                "is repaired",
                _first_degradation(snapshot),
                explicit,
            )
        elif getattr(config, "multi_user_mode", False):
            logging.getLogger(__name__).warning(
                "Multi-user workspace isolation floor derived as 'none' "
                "(launch path degraded: %s); default launches will NOT be "
                "per-user isolated — set workspace.required_isolation_level "
                "to pin a hard floor",
                _first_degradation(snapshot),
            )
    if explicit:
        if is_valid_isolation_level(explicit):
            return explicit
        import logging

        logging.getLogger(__name__).warning(
            "Invalid workspace.required_isolation_level %r; using derived default",
            explicit,
        )
    return snapshot.isolation_level


def _reason_is_degraded(snapshot: IsolationCapabilitySnapshot) -> bool:
    return any(r.code in ("launch_path_degraded",) for r in snapshot.reasons)


def _first_degradation(snapshot: IsolationCapabilitySnapshot) -> str:
    for r in snapshot.reasons:
        if r.code == "launch_path_degraded":
            return r.message
    return "unknown"


def evaluate_isolation_requirement(
    required_level: str,
    *,
    snapshot: IsolationCapabilitySnapshot,
    system_account: str | None,
    manager: Any,
) -> IsolationReason | None:
    """Gate an explicit isolation requirement before launching a workspace.

    Returns None when the requirement is satisfiable, else the structured
    rejection reason. ``system_account`` must be the explicit DB mapping
    (no username fallback) — a missing mapping is a rejection, not a
    silent convention-derived account (Issue #3374).
    """
    if not is_valid_isolation_level(required_level):
        return IsolationReason(
            "invalid_isolation_level",
            f"Unknown isolation level '{required_level}'; expected one of "
            f"{', '.join(SUPPORTED_ISOLATION_LEVELS)}.",
        )
    if required_level == ISOLATION_LEVEL_NONE:
        return None
    if isolation_level_at_least(snapshot.isolation_level, ISOLATION_LEVEL_SANDBOXED):
        # Review round 1 (T-B, pin=floor): a sandboxed-capable deployment
        # satisfies every requirement at or below sandboxed with the pod
        # form — the strongest VERIFIED form, which is what launches. The
        # snapshot level IS sandboxed, so the probe already passed, and the
        # OS-account chain below (identity mapping, sudo launch) does not
        # apply: a pod's identity is its per-instance token. Without this
        # branch, an entrypoint-pinned `os_user` floor made every default
        # request from a mapping-less user a silent 400 even though the
        # launch itself would have been a sandboxed pod.
        return None
    if required_level == ISOLATION_LEVEL_SANDBOXED:
        # Issue #3378: identity for sandboxed pods is the per-instance webui
        # token, not an OS account — the identity_mapping / per-user-launch
        # chain below is os_user-specific and does not apply. The gate is the
        # capability snapshot itself: when the level is unmet, surface the
        # sandbox probe's exact reason instead of the generic level message.
        for reason in snapshot.reasons:
            if reason.code in SANDBOX_PROBE_REASON_CODES:
                return reason
        return IsolationReason(
            "isolation_level_unsupported",
            f"Requested isolation level 'sandboxed' exceeds what this "
            f"deployment enforces ('{snapshot.isolation_level}'); refusing to "
            "silently launch with weaker isolation.",
        )
    if not isolation_level_at_least(snapshot.isolation_level, required_level):
        return IsolationReason(
            "isolation_level_unsupported",
            f"Requested isolation level '{required_level}' exceeds what this "
            f"deployment enforces ('{snapshot.isolation_level}'); refusing to "
            "silently launch with weaker isolation.",
        )
    if not system_account:
        return IsolationReason(
            "identity_mapping_missing",
            "User has no system_account mapping; refusing to fall back to a "
            "convention-derived or shared account under an explicit isolation "
            "requirement.",
        )
    launch_ok, launch_reason = manager.supports_per_user_launch(system_account)
    if not launch_ok:
        return IsolationReason(
            "per_user_launch_unavailable",
            f"Cannot launch the WebUI under system account '{system_account}' ({launch_reason}).",
        )
    return None
