#!/bin/bash
# openace-run-as — cross-user agent launcher for AI autonomous development.
#
# Problem (Issue #1395): the open-ace service runs as the `openace` user, but
# autonomous-development workflows drive a repo owned by another user
# (system_account), often under a 0700 home directory. Agent CLIs
# (claude-code/qwen-code/openclaw) infer the project root from cwd and have
# no --cwd/--project flag, so they MUST be launched with cwd = project_path.
# But subprocess.Popen(cwd=...) chdir's as the service user and hits
# [Errno 13] Permission denied under a private home.
#
# This wrapper closes that gap: invoked as root through a narrow sudoers rule,
# it chdir's into the project dir, grants a dedicated credentialless account
# worktree-only ACLs, then drops privileges via `runuser`. Isolated mode starts
# from an explicit empty environment and never grants write access to Git
# metadata.
#
# Usage: openace-run-as <user> <dir> <cmd> [args...]
#
# Exit codes: 64 = usage error; 65 = chdir failed; 66 = ACL unavailable;
# 67 = invalid isolation account; 68 = protected .git entry changed; otherwise
# passes through the child status.
#
# Security: sudoers authorizes only the `--isolated` form for the service user.
# runuser (not sudo) performs the user drop. The script is single-purpose and
# takes no shell; the command and its args are passed verbatim (no eval).

set -euo pipefail

isolated=false
if [ "${1:-}" = "--isolated" ]; then
    isolated=true
    shift
fi

# Issue #2020: optional per-attempt task id. When present the launcher keys
# HOME/TMP/XDG, the ACL/signature registries, the cleanup lock and (where
# available) a task cgroup off the attempt instead of the shared Agent UID,
# so concurrent attempts on one UID no longer serialize or cross-kill.
task_id=""
if [ "$isolated" = true ] && [ "${1:-}" = "--task-id" ]; then
    if [ "$#" -lt 2 ]; then
        echo "Usage: $0 [--isolated] [--task-id <id>] <user> <dir> <cmd> [args...]" >&2
        exit 64
    fi
    task_id="$2"
    shift 2
fi

if [ "$#" -lt 3 ]; then
    echo "Usage: $0 [--isolated] [--task-id <id>] <user> <dir> <cmd> [args...]" >&2
    exit 64
fi

target_user="$1"
project_dir="$2"
shift 2

# Issue #2018: the isolated launcher is authorized by an external helper before
# any root ACL grant. The helper pins the target account to the configured
# credentialless openace-agent and confines the project path to a registered
# workspace root (no symlink components, no '..', no mount crossing).
# Autonomous-dev only; the local/remote workspace path does not call this
# wrapper.
#
# When invoked as root (the real sudo'd production path) the constraint paths
# are HARD-CODED and env overrides are IGNORED, so no future sudoers
# `env_keep += OPENACE_*` drift or SETENV grant can redirect the gate or the
# audit log. The OPENACE_* overrides are honored only for non-root test
# invocation (`bash $WRAPPER`).
_resolve_non_root_launcher_conf() {
    local user_launcher_conf="$HOME/.open-ace/agent-launcher.conf"
    if [ -n "${OPENACE_LAUNCHER_CONF:-}" ]; then
        printf '%s\n' "$OPENACE_LAUNCHER_CONF"
        return
    fi
    if [ -f "/etc/openace/agent-launcher.conf" ]; then
        printf '%s\n' "/etc/openace/agent-launcher.conf"
        return
    fi
    if [ -f "$user_launcher_conf" ]; then
        printf '%s\n' "$user_launcher_conf"
        return
    fi
    printf '%s\n' "/etc/openace/agent-launcher.conf"
}

if [ "$(id -u)" -eq 0 ]; then
    _OPENACE_VALIDATE_LAUNCH="/usr/local/libexec/openace-validate-launch"
    _OPENACE_LAUNCHER_CONF="/etc/openace/agent-launcher.conf"
    AUDIT_LOG="/var/log/openace/run-as-audit.log"
else
    _OPENACE_VALIDATE_LAUNCH="${OPENACE_VALIDATE_LAUNCH:-/usr/local/libexec/openace-validate-launch}"
    _OPENACE_LAUNCHER_CONF="$(_resolve_non_root_launcher_conf)"
    AUDIT_LOG="${OPENACE_AUDIT_LOG:-/var/log/openace/run-as-audit.log}"
fi

# Security audit trail (mirrors scripts/openace-chown.sh). Logs only caller,
# account, path and outcome — never the command or its args, which may carry
# proxy tokens via env_keep. The default log lives under a root-owned dir so a
# compromised service account cannot swap the file; the target_user guard below
# strips the log-injection vector (a newline in the account could otherwise
# become a cron entry via a swapped audit file).
log_audit() {
    local outcome="$1"
    local log_entry
    log_entry="[$(date '+%Y-%m-%d %H:%M:%S')] caller=$(id -un) target=${target_user} path=${project_dir} ${outcome}"
    mkdir -p "$(dirname "$AUDIT_LOG")" 2>/dev/null || true
    echo "$log_entry" >> "$AUDIT_LOG" 2>/dev/null || true
}

# Autonomous agents use a dedicated credentialless principal.  Unlike the
# legacy repo-owner launch, this account receives write ACLs only on worktree
# files; Git metadata is read-only so it cannot install hooks, rewrite remotes,
# or create refs that a later privileged push would trust.
if [ "$isolated" = true ]; then
    # task_id becomes a path component under the per-task runtime root and a
    # suffix on registry/lock filenames, so it must match the same safe charset
    # the Python side enforces (task_isolation.sanitize_task_id). Reject early,
    # before any filesystem or audit work.
    if [ -n "$task_id" ]; then
        case "$task_id" in
            "" | *[!A-Za-z0-9._-]*)
                echo "openace-run-as: --task-id must be alphanumeric, '.', '_', '-' only" >&2
                exit 64
                ;;
        esac
    fi
    if [[ "$project_dir" != /* || "$project_dir" == *$'\n'* ]]; then
        echo "openace-run-as: isolated project path must be absolute and single-line" >&2
        exit 64
    fi
    # target_user reaches the audit log verbatim, so reject embedded newlines
    # or CR (log injection → root cron via a swapped audit file) and any name
    # outside POSIX user-name shape BEFORE any log_audit call. This mirrors the
    # shape the orchestrator already validates for the agent account.
    if [[ "$target_user" == *$'\n'* || "$target_user" == *$'\r'* ]] || \
       ! [[ "$target_user" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]]; then
        echo "openace-run-as: isolated target account has invalid characters" >&2
        exit 64
    fi
    # Authorize the launch (account pin + path confinement) BEFORE touching the
    # filesystem. Fail closed — including when the helper or conf is absent, so
    # a missing constraint source can never degrade into an unvalidated grant.
    if [ ! -x "$_OPENACE_VALIDATE_LAUNCH" ]; then
        echo "openace-run-as: launch validator unavailable at $_OPENACE_VALIDATE_LAUNCH" >&2
        log_audit "result=reject_missing_validator"
        exit 66
    fi
    set +e
    validate_msg="$("$_OPENACE_VALIDATE_LAUNCH" "$_OPENACE_LAUNCHER_CONF" "$target_user" "$project_dir" 2>&1)"
    validate_rc=$?
    set -e
    if [ "$validate_rc" -ne 0 ]; then
        echo "openace-run-as: isolated launch rejected: ${validate_msg}" >&2
        case "$validate_msg" in
            *reject_account*) log_audit "result=reject_account"; exit 67 ;;
            *reject_conf*)    log_audit "result=reject_conf"; exit 66 ;;
            *reject_path*)    log_audit "result=reject_path"; exit 64 ;;
            *)                log_audit "result=reject_unknown"; exit 67 ;;
        esac
    fi
    log_audit "result=accept"
    # Issue #2020: pkill is no longer used (UID-wide kill removed in favor of
    # task-scoped cgroup/pgid kill); only flock remains required here.
    if ! command -v flock >/dev/null 2>&1; then
        echo "openace-run-as: flock is required for isolated execution" >&2
        exit 66
    fi
    normalize_group_class_signature() {
        local signature="$1"
        local entry_mode
        if [[ "$signature" =~ ^((file|dir):[^:]+:[^:]+:)([0-7]{3,4})(:.*)$ ]]; then
            entry_mode="${BASH_REMATCH[3]}"
            printf '%s%s-%s%s' \
                "${BASH_REMATCH[1]}" "${entry_mode%??}" "${entry_mode: -1}" "${BASH_REMATCH[4]}"
        else
            printf '%s' "$signature"
        fi
    }

    git_entry_signature() {
        local signature_project="$1"
        if [ -L "$signature_project/.git" ]; then
            printf 'link:%s' "$(readlink "$signature_project/.git")"
        elif [ -f "$signature_project/.git" ]; then
            printf 'file:%s:%s' \
                "$(stat -c '%d:%i:%a:%U:%G' "$signature_project/.git")" \
                "$(sha256sum "$signature_project/.git" | cut -d' ' -f1)"
        elif [ -d "$signature_project/.git" ]; then
            printf 'dir:%s' "$(stat -c '%d:%i:%a:%U:%G' "$signature_project/.git")"
        else
            printf 'missing'
        fi
    }

    git_entry_acl_snapshot() {
        local signature_project="$1"
        if [ -f "$signature_project/.git" ] || [ -d "$signature_project/.git" ]; then
            getfacl -cpE "$signature_project/.git" | base64 -w 0
        else
            printf '-'
        fi
    }

    acl_snapshot_without_mask() {
        local acl_snapshot="$1"
        printf '%s' "$acl_snapshot" | base64 --decode | \
            grep -v '^mask::' | base64 -w 0
    }

    acl_snapshot_has_mask() {
        local acl_snapshot="$1"
        printf '%s' "$acl_snapshot" | base64 --decode | grep -q '^mask::'
    }

    verify_and_restore_git_entry() {
        local signature_project="$1"
        local expected_signature="$2"
        local expected_acl_snapshot="${3:-}"
        local actual_signature
        local actual_acl_snapshot
        local restored_signature
        local restored_acl_snapshot

        actual_signature="$(git_entry_signature "$signature_project")"
        actual_acl_snapshot="$(git_entry_acl_snapshot "$signature_project")"
        if [ "$expected_signature" = "$actual_signature" ] && \
            [ -n "$expected_acl_snapshot" ] && \
            [ "$expected_acl_snapshot" = "$actual_acl_snapshot" ]; then
            return 0
        fi

        # Before restoring launcher-owned ACL state, fail closed on every
        # structural/content/ownership change. Only the group-class digit may
        # differ here because POSIX exposes its ACL mask in those mode bits.
        if [ "$(normalize_group_class_signature "$expected_signature")" != \
            "$(normalize_group_class_signature "$actual_signature")" ]; then
            return 1
        fi

        if [ -n "$expected_acl_snapshot" ] && [ "$expected_acl_snapshot" != "-" ]; then
            # The launcher may add or recalculate only mask::. All base and
            # named ACL entries must still exactly match before restoration.
            if [ "$(acl_snapshot_without_mask "$expected_acl_snapshot")" != \
                "$(acl_snapshot_without_mask "$actual_acl_snapshot")" ]; then
                return 1
            fi
            printf '%s' "$expected_acl_snapshot" | base64 --decode | \
                setfacl -n --set-file=- "$signature_project/.git" || return 1
            restored_signature="$(git_entry_signature "$signature_project")"
            restored_acl_snapshot="$(git_entry_acl_snapshot "$signature_project")"
            [ "$expected_signature" = "$restored_signature" ] && \
                [ "$expected_acl_snapshot" = "$restored_acl_snapshot" ]
            return
        fi

        if [ "$expected_acl_snapshot" = "-" ]; then
            [ "$expected_signature" = "$actual_signature" ]
            return
        fi

        # Rolling-upgrade compatibility for a registry written by the previous
        # release, which has no ACL snapshot. This one-time fallback still
        # protects type, device/inode, owner/group, owner/other permissions and
        # content, and is allowed only when the recovered entry actually has
        # the extended ACL mask responsible for the legacy false positive. A
        # successful recovery immediately writes the new exact format.
        acl_snapshot_has_mask "$actual_acl_snapshot"
    }

    if ! command -v setfacl >/dev/null 2>&1 \
        || ! command -v getfacl >/dev/null 2>&1 \
        || ! command -v base64 >/dev/null 2>&1; then
        echo "openace-run-as: setfacl, getfacl and base64 are required for isolated execution" >&2
        exit 66
    fi
    project_owner="$(stat -c '%U' "$project_dir")"
    if [ -z "$project_owner" ] || [ "$project_owner" = "$target_user" ]; then
        echo "openace-run-as: isolated agent must differ from project owner" >&2
        exit 67
    fi

    target_uid="$(id -u "$target_user")"
    if ! [[ "$target_uid" =~ ^[0-9]+$ ]] || [ "$target_uid" -eq 0 ]; then
        echo "openace-run-as: isolated agent must be an unprivileged account" >&2
        exit 67
    fi
    for target_group in $(id -Gn "$target_user"); do
        case "$target_group" in
            root|wheel|sudo|admin)
                echo "openace-run-as: isolated agent must not belong to an admin group" >&2
                exit 67
                ;;
        esac
    done
    # Issue #2020: key the lock + registries off the attempt (task_id) when
    # present, so concurrent attempts on the same UID neither serialize on a
    # UID-level flock nor overwrite each other's ACL/signature registry. The
    # uid-* fallback preserves legacy single-attempt semantics for callers
    # that do not pass --task-id.
    if [ -n "$task_id" ]; then
        isolation_key="task-${task_id}"
    else
        isolation_key="uid-${target_uid}"
    fi
    acl_registry="/run/openace-agent-acl-${isolation_key}"
    signature_registry="/run/openace-agent-git-signature-${isolation_key}"
    signature_tmp="${signature_registry}.next"
    exec 9>"/run/lock/openace-agent-${isolation_key}.lock"
    flock -x 9

    # Per-attempt HOME/TMP/XDG runtime tree (#2020). Ephemeral parent under
    # /run; each leaf chowned to the agent and locked 0700 so sibling attempts
    # cannot traverse. Wiped first so a retried task_id starts clean.
    task_base=""
    task_home="" ; task_tmp="" ; task_cache="" ; task_config="" ; task_data=""
    if [ -n "$task_id" ]; then
        task_root="${OPENACE_AGENT_TASK_ROOT:-/run/openace-agent-tasks}"
        task_base="${task_root}/${task_id}"
        task_home="${task_base}/home"
        task_tmp="${task_base}/tmp"
        task_cache="${task_base}/cache"
        task_config="${task_base}/config"
        task_data="${task_base}/data"
        # Issue #2035: preserve Claude CLI session files across task invocations.
        # The per-task HOME is ephemeral, but Claude CLI stores conversation
        # history under ~/.claude/projects/. These files must survive task_base
        # cleanup so that --resume can find them on the next invocation with the
        # same task_id (session line). Without this, every resume attempt fails
        # with "no conversation found with session id", triggering repeated
        # session_resume_recovery events and wasting agent tokens.
        #
        # The preserve path is fixed (not conditional on .claude existing) so
        # that a crashed previous invocation — which moved .claude to the
        # preserve path but never moved it back — is recovered: the
        # unconditional restore below finds the orphaned preserve dir and
        # restores it even though $task_home/.claude was already moved away.
        preserve_claude_dir="${task_base}.claude-preserve"
        if [ -d "$task_home/.claude" ]; then
            rm -rf -- "$preserve_claude_dir" 2>/dev/null || true
            mv "$task_home/.claude" "$preserve_claude_dir" 2>/dev/null || true
        fi
        rm -rf -- "$task_base" 2>/dev/null || true
        mkdir -p "$task_home" "$task_tmp" "$task_cache" "$task_config" "$task_data"
        chmod 700 "$task_base" "$task_home" "$task_tmp" "$task_cache" "$task_config" "$task_data"
        # Unconditional restore: works even if a previous invocation crashed
        # between the preserve-mv and the restore-mv (the .claude dir was moved
        # out but never moved back). The chown on $task_base below covers .claude.
        if [ -d "$preserve_claude_dir" ]; then
            mv "$preserve_claude_dir" "$task_home/.claude" 2>/dev/null || true
        fi
        chown -R "$target_user":"$(id -g "$target_user")" "$task_base" 2>/dev/null || true
    fi

    # Optional task cgroup (v2 unified) for task-scoped kill (#2020) and, under
    # #2020-B / Layer 3, resource limits. Created only where the host exposes a
    # writable cgroup v2 hierarchy (bare metal or a delegated container). In a
    # stock python:3.11-slim container /sys/fs/cgroup is read-only and mkdir
    # fails, so task_cgroup stays empty and kill falls back to the child pgid.
    task_cgroup=""
    if [ -n "$task_id" ]; then
        cgroup_root="${OPENACE_AGENT_CGROUP_ROOT:-/sys/fs/cgroup/openace-agent}"
        # Issue #2020: cgroup v2 root does not expose cgroup.kill (kernel
        # design — the root cgroup cannot be killed). Test whether
        # cgroup_root exists and a subgroup can be created under it, rather
        # than checking /sys/fs/cgroup/cgroup.kill which is never present on
        # the root cgroup. Also use -f (file exists) instead of -w on
        # cgroup.procs because cgroup2's virtual files do not honour
        # traditional access(2) write checks — test -w returns false even
        # for root on a writable cgroup.procs.
        if [ -d "$cgroup_root" ]; then
            if [ -d "$cgroup_root/$task_id" ]; then
                echo 1 > "$cgroup_root/$task_id/cgroup.kill" 2>/dev/null || true
                rmdir "$cgroup_root/$task_id" 2>/dev/null || true
            fi
            if mkdir -p "$cgroup_root/$task_id" 2>/dev/null \
               && [ -f "$cgroup_root/$task_id/cgroup.procs" ]; then
                task_cgroup="$cgroup_root/$task_id"
            fi
        fi
    fi
    agent_child_pid=""

    # Issue #2020: per-task resource limits read from agent-launcher.conf. The
    # same keys the scheduler/runner parse (task_isolation.read_agent_task_policy)
    # are consumed here so the launcher and control plane agree on the policy.
    _conf_value() {
        local key="$1"
        [ -f "$_OPENACE_LAUNCHER_CONF" ] || return 0
        sed -n "s/^[[:space:]]*${key}[[:space:]]*=[[:space:]]*//p" \
            "$_OPENACE_LAUNCHER_CONF" 2>/dev/null \
            | head -1 | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//"
    }
    task_memory="$(_conf_value agent_task_memory_max_bytes)"
    task_pids="$(_conf_value agent_task_pids_max)"
    task_cpu="$(_conf_value agent_task_cpu_max)"
    # Normalize to lowercase before matching so the case list can't miss a
    # variant (review #2067: the hand-enumerated list had a duplicate TRUE and
    # no mixed-case True).
    task_cg_enabled_lc="$(printf '%s' "$(_conf_value agent_task_cgroup_enabled)" | tr '[:upper:]' '[:lower:]')"
    case "$task_cg_enabled_lc" in
        on|true|yes|1) task_cg_required=true ;;
        *)             task_cg_required=false ;;
    esac

    # Fail-closed (#2020): if cgroup enforcement is forced on but the task
    # cgroup could not be created (read-only cgroupfs / no v2 hierarchy, e.g.
    # a stock container), refuse to launch rather than run the agent without
    # the configured resource boundary.
    if [ "$task_cg_required" = true ] && [ -z "$task_cgroup" ] && [ -n "$task_id" ]; then
        echo "OPENACE_CGROUP_REQUIRED: cgroup enforcement forced (agent_task_cgroup_enabled=on) but task cgroup unavailable" >&2
        log_audit "result=reject_cgroup_required_unavailable"
        exit 66
    fi

    # Apply cgroup v2 limits where the task cgroup exists.
    if [ -n "$task_cgroup" ]; then
        if [ -n "$task_memory" ] && [ "$task_memory" != "0" ]; then
            echo "$task_memory" > "$task_cgroup/memory.max" 2>/dev/null || true
        fi
        if [ -n "$task_pids" ] && [ "$task_pids" != "0" ]; then
            echo "$task_pids" > "$task_cgroup/pids.max" 2>/dev/null || true
        fi
        if [ -n "$task_cpu" ] && [ "$task_cpu" != "0" ]; then
            echo "$task_cpu" > "$task_cgroup/cpu.max" 2>/dev/null || true
        fi
    fi

    revoke_agent_access() {
        local protected_path="$1"
        [ -n "$protected_path" ] || return 0
        if [ -d "$protected_path" ]; then
            find "$protected_path" -type l -prune -o \
                -exec setfacl -x "u:${target_user}" {} + 2>/dev/null || true
            find "$protected_path" -type l -prune -o -type d \
                -exec setfacl -x "d:u:${target_user}" {} + 2>/dev/null || true
        elif [ -e "$protected_path" ]; then
            setfacl -x "u:${target_user}" "$protected_path" 2>/dev/null || true
        fi
        local protected_parent="$protected_path"
        while [ "$protected_parent" != "/" ]; do
            setfacl -x "u:${target_user}" "$protected_parent" 2>/dev/null || true
            protected_parent="$(dirname "$protected_parent")"
        done
    }

    cleanup_isolated() {
        # Issue #2020: kill exactly THIS attempt's processes. Never pkill by
        # UID — that would reap other concurrent attempts sharing the Agent
        # account. Prefer the task cgroup (kills every cgroup member and their
        # descendants); as defense-in-depth also signal the recorded child.
        # The orchestrator's os.killpg(<sudo_pid>) handles the no-cgroup case
        # because the child remains in the launcher's process group.
        if [ -n "$task_cgroup" ] && [ -f "$task_cgroup/cgroup.kill" ]; then
            echo 1 > "$task_cgroup/cgroup.kill" 2>/dev/null || true
        fi
        if [ -n "${agent_child_pid:-}" ] && kill -0 "${agent_child_pid}" 2>/dev/null; then
            kill -KILL "${agent_child_pid}" 2>/dev/null || true
        fi
        if [ -f "$acl_registry" ]; then
            while IFS= read -r protected_path; do
                revoke_agent_access "$protected_path"
            done < "$acl_registry"
        fi
        : > "$acl_registry"
        chmod 600 "$acl_registry" 2>/dev/null || true
        rm -f "$signature_tmp"
    }

    # Recover safely after a previously interrupted wrapper before granting
    # this run access to anything. The registries are root-owned under /run.
    #
    # Capture the prior run's protected .git baseline before cleanup, but only
    # compare it after cleanup has removed the launcher's own temporary ACLs.
    # POSIX ACL mask changes are reflected in stat(2)'s mode bits; comparing a
    # pre-cleanup signature with a post-cleanup signature therefore produced a
    # false integrity violation even for the read-only project-dir probe.
    previous_signature_project=""
    previous_git_signature=""
    previous_git_acl_snapshot=""
    if [ -f "$signature_registry" ]; then
        IFS= read -r previous_signature_project < "$signature_registry" || true
        previous_git_signature="$(sed -n '2p' "$signature_registry")"
        previous_git_acl_snapshot="$(sed -n '3p' "$signature_registry")"
    fi
    cleanup_isolated
    if [ -n "$previous_signature_project" ] && [ -n "$previous_git_signature" ]; then
        # The signature registry is keyed by the shared isolation account
        # (uid), not the project path. Any workflow using this account that is
        # interrupted leaves a registry behind; the next (possibly different)
        # workflow would then compare its own .git against the stale entry and
        # fail with a false integrity violation — most commonly because the
        # interrupted run's worktree (e.g. a throwaway merge-<wf> worktree) was
        # already cleaned up, so its .git is now "missing". Only verify when
        # the previous run operated on the same project as this one; otherwise
        # the registry belongs to a different, already-finalized run and is
        # discarded.
        if [ "$previous_signature_project" = "$project_dir" ] && \
            verify_and_restore_git_entry \
                "$previous_signature_project" \
                "$previous_git_signature" \
                "$previous_git_acl_snapshot"; then
            : # integrity verified for the same project
        elif [ "$previous_signature_project" = "$project_dir" ]; then
            echo "OPENACE_REPO_INTEGRITY_VIOLATION: .git entry changed during interrupted agent execution" >&2
            exit 68
        else
            rm -f "$signature_registry"
        fi
    else
        rm -f "$signature_registry"
    fi

    # Persist the exact signature and ACL baseline before granting this run any
    # access. If interrupted, the next invocation first verifies structural
    # integrity, restores this ACL, and then requires an exact match.
    git_entry_before="$(git_entry_signature "$project_dir")"
    git_acl_before="$(git_entry_acl_snapshot "$project_dir")"
    printf '%s\n%s\n%s\n' \
        "$project_dir" "$git_entry_before" "$git_acl_before" > "$signature_tmp"
    chmod 600 "$signature_tmp"
    mv -f "$signature_tmp" "$signature_registry"
    trap cleanup_isolated EXIT
    # Bash services traps promptly while waiting for a background job. With a
    # foreground external command it may defer the trap until that command
    # exits, stranding this wrapper and its ACL lock after the parent sudo
    # process is terminated.
    trap 'exit 129' HUP
    trap 'exit 130' INT
    trap 'exit 143' TERM

    # Record every path before the first ACL grant. If this wrapper is
    # interrupted at any later instruction, the next serialized invocation
    # can revoke the exact abandoned grants before starting another agent.
    git_dir="$(git -C "$project_dir" rev-parse --absolute-git-dir 2>/dev/null || true)"
    common_dir="$(git -C "$project_dir" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)"
    printf '%s\n' "$project_dir" "$git_dir" "$common_dir" > "$acl_registry"
    chmod 600 "$acl_registry"

    # Allow traversal to the project without exposing sibling home content.
    parent="$project_dir"
    while [ "$parent" != "/" ]; do
        setfacl -m "u:${target_user}:x" "$parent"
        parent="$(dirname "$parent")"
    done

    # Existing and newly-created worktree files stay writable by both the
    # credentialless agent and repository owner.  Never grant write access to
    # .git (directory in a normal clone, pointer file in a linked worktree).
    find "$project_dir" -path "$project_dir/.git" -prune -o -type l -prune -o \
        -exec setfacl -m "u:${target_user}:rwX,u:${project_owner}:rwX" {} +
    find "$project_dir" -path "$project_dir/.git" -prune -o -type l -prune -o -type d \
        -exec setfacl -m "d:u:${target_user}:rwX,d:u:${project_owner}:rwX" {} +

    for metadata_dir in "$git_dir" "$common_dir"; do
        [ -d "$metadata_dir" ] || continue
        parent="$metadata_dir"
        while [ "$parent" != "/" ]; do
            setfacl -m "u:${target_user}:x" "$parent"
            parent="$(dirname "$parent")"
        done
        setfacl -R -m "u:${target_user}:rX" "$metadata_dir"
    done
    # A linked worktree stores .git as a pointer file (read-only is enough),
    # while a normal clone stores it as a directory and needs execute/traverse.
    if [ -f "$project_dir/.git" ]; then
        setfacl -m "u:${target_user}:r" "$project_dir/.git"
    elif [ -d "$project_dir/.git" ]; then
        setfacl -m "u:${target_user}:rX" "$project_dir/.git"
    fi
fi

# Root can traverse any directory; chdir here so the CLI inherits the project
# root as its cwd without the service user ever needing access to the path.
cd "$project_dir" || {
    echo "openace-run-as: cannot chdir to '$project_dir'" >&2
    exit 65
}

# Drop privileges and run the CLI. Absolute path is used because sudo's
# secure_path may not include /usr/sbin on all distros.
if [ "$isolated" = true ]; then
    # Issue #2020: choose HOME/TMP per attempt. With --task-id the agent gets a
    # private HOME/TMP/XDG tree (and is enrolled in its task cgroup); without it
    # the legacy shared account home + /tmp are used for backward compatibility.
    if [ -n "$task_home" ]; then
        agent_home="$task_home"
        agent_tmp="$task_tmp"
    else
        agent_home="$(getent passwd "$target_user" | cut -d: -f6)"
        agent_tmp="/tmp"
    fi
    env_args=(
        "HOME=$agent_home" "USER=$target_user" "LOGNAME=$target_user"
        "LANG=${LANG:-C.UTF-8}" "LC_ALL=${LC_ALL:-}" "TMPDIR=$agent_tmp"
    )
    if [ -n "$task_cache" ]; then
        env_args+=("XDG_CACHE_HOME=$task_cache" "XDG_CONFIG_HOME=$task_config" "XDG_DATA_HOME=$task_data")
    fi
    env_args+=(
        "GIT_CONFIG_COUNT=1" "GIT_CONFIG_KEY_0=safe.directory"
        "GIT_CONFIG_VALUE_0=$project_dir"
    )
    set +e
    # The child stays in this launcher's process group (= the sudo session the
    # Python side created via start_new_session=True), so the orchestrator's
    # ``os.killpg(<sudo_pid>)`` reaches the whole agent tree directly. A task
    # cgroup (when available) gives the launcher a precise cgroup.kill on top.
    /usr/sbin/runuser -u "$target_user" -- /usr/bin/env -i \
        "${env_args[@]}" "$@" <&0 9>&- &
    agent_child_pid=$!
    if [ -n "$task_cgroup" ]; then
        echo "$agent_child_pid" > "$task_cgroup/cgroup.procs" 2>/dev/null || true
    elif [ -n "$task_id" ] && command -v prlimit >/dev/null 2>&1; then
        # Issue #2020 portable fallback: where no task cgroup exists (stock
        # container with read-only /sys/fs/cgroup), apply POSIX rlimits to the
        # child process tree. RLIMIT_AS bounds virtual address space (a coarse
        # memory ceiling) and RLIMIT_NPROC bounds processes per real UID. CPU
        # is enforced only via cgroup (cpu.max) since RLIMIT_CPU is in seconds.
        prlimit_args=()
        if [ -n "$task_memory" ] && [ "$task_memory" != "0" ]; then
            prlimit_args+=("--as=${task_memory}")
        fi
        if [ -n "$task_pids" ] && [ "$task_pids" != "0" ]; then
            prlimit_args+=("--nproc=${task_pids}")
        fi
        if [ "${#prlimit_args[@]}" -gt 0 ]; then
            prlimit --pid="$agent_child_pid" "${prlimit_args[@]}" 2>/dev/null || true
        fi
    fi
    wait "$agent_child_pid"
    child_status=$?
    set -e
    cleanup_isolated
    trap - EXIT HUP INT TERM
    if ! verify_and_restore_git_entry "$project_dir" "$git_entry_before" "$git_acl_before"; then
        echo "OPENACE_REPO_INTEGRITY_VIOLATION: .git entry changed during agent execution" >&2
        exit 68
    fi
    rm -f "$signature_registry"
    # Reap the task cgroup now that the child has exited and cleanup killed
    # any stragglers. The runtime tree under /run is ephemeral; orphan dirs
    # from killed attempts are reconciled by the scheduler on restart (#2020).
    if [ -n "$task_cgroup" ] && [ -d "$task_cgroup" ]; then
        rmdir "$task_cgroup" 2>/dev/null || true
    fi
    exit "$child_status"
fi

exec /usr/sbin/runuser -u "$target_user" -- "$@"
