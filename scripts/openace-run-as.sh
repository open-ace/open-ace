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
    # Single source for the lock directory so the #2403 preserve reaper derives
    # peer lock paths exactly the way this run derives its own. Deliberately a
    # constant, not an environment override: redirecting it would silently break
    # mutual exclusion between concurrent attempts.
    _lock_dir="/run/lock"
    exec 9>"${_lock_dir}/openace-agent-${isolation_key}.lock"
    flock -x 9

    # Per-attempt HOME/TMP/XDG runtime tree (#2020). Ephemeral parent under
    # /run; each leaf chowned to the agent and locked 0700 so sibling attempts
    # cannot traverse. Wiped first so a retried task_id starts clean.
    task_base=""
    task_home="" ; task_tmp="" ; task_cache="" ; task_config="" ; task_data=""
    # Issue #2403: must be initialised unconditionally. It is only assigned
    # inside the `[ -n "$task_id" ]` block below, and reclaim_task_tree() reads
    # it from an EXIT trap that also fires on the legacy `uid-*` path — under
    # `set -u` an unset name would kill the wrapper there.
    preserve_claude_dir=""
    # Same reason as above: only assigned inside the task_id block, but read by
    # the preserve reaper.
    task_root=""
    # Age cutoff for reaping abandoned .claude-preserve dirs (#2403 F1c) is read
    # from the launcher conf at the call site below — _conf_value() is not
    # defined yet at this point in the script.
    preserve_max_age_days=30

    # Issue #2403: reclaim the per-task runtime tree on the way out. Without
    # this the wrapper only ever wiped on START, so every run left one directory
    # behind forever; 46 of them filled the 3.1G /run tmpfs and every autonomous
    # workflow began failing with a misleading "Failed to access project dir".
    #
    # Defined BEFORE the tree is built, and before the first exit that can
    # follow it (the cgroup fail-closed `exit 66` further down), because bash
    # resolves the function name when the EXIT trap FIRES, not when it is
    # installed. Defining it later would silently make that trap a no-op.
    #
    # Deliberately NOT part of cleanup_isolated(): that one also runs pre-flight,
    # AFTER this tree has been built and .claude restored into it, so folding
    # the reclaim in there would delete the freshly restored session history
    # before the agent even starts. Keeping it separate also lets tests extract
    # and run this function without cgroup/ACL/registry privileges.
    # Issue #2442: move $1 (.claude) to $2 (preserve path). If the prior
    # preserve dir cannot be removed, a following `mv` would nest source into
    # the survivor and the next restore would hand Claude a mis-shaped tree,
    # silently breaking --resume. Fail closed: rm failure returns non-zero so
    # the caller decides (startup aborts via exit 70; reclaim logs). chmod 700
    # is applied per #2403.
    _move_to_preserve() {
        local src="$1" dest="$2"
        if [ -e "$dest" ] || [ -L "$dest" ]; then
            rm -rf -- "$dest" 2>/dev/null || return 1
        fi
        mv -- "$src" "$dest" 2>/dev/null || return 1
        chmod 700 "$dest" 2>/dev/null || true
    }

    reclaim_task_tree() {
        [ -n "$task_base" ] || return 0
        [ -n "$preserve_claude_dir" ] || return 0
        # Same three-line shape as the startup preserve-move below. The `rm -rf`
        # MUST stay inside the `-d` guard: a second execution (a signal landing
        # between the success-path call and `trap -`) then becomes a no-op
        # instead of deleting the history it just saved.
        #
        # Both branches do real work, depending on where a signal lands:
        #   - guard TRUE  (.claude still in task_home): rescue it to the preserve
        #     path, then drop the tree. This is the common case.
        #   - guard FALSE (.claude already moved out, or task_home gone): leave
        #     the preserve dir alone and just drop the tree.
        # Do not "simplify" this to the FALSE branch — the TRUE branch is what
        # keeps #2035 --resume working.
        if [ -d "$task_home/.claude" ]; then
            # _move_to_preserve removes any prior preserve dir, moves .claude
            # into it, and chmod 700 (the SIBLING-of-$task_base exposure noted
            # in #2403). It fails closed on rm failure so mv cannot nest .claude
            # into a survivor (#2442); reclaim only logs because errexit is live
            # in this EXIT trap and an exit here would rewrite the status.
            _move_to_preserve "$task_home/.claude" "$preserve_claude_dir" \
                || log_audit "result=preserve_rescue_failed task=${task_id:-}"
        fi
        # Log rather than swallow: a partial rm on a full tmpfs is exactly the
        # condition this issue is about, and silence is why it went unnoticed
        # for so long.
        rm -rf -- "$task_base" 2>/dev/null || log_audit "result=reclaim_failed task=${task_id:-}"
    }

    # Issue #2403 F1c: reclaiming the tree on exit converts a fast directory
    # leak into a slow .claude-preserve leak (one per task_id, forever), so bound
    # that too. Scope is deliberately narrow: ONLY *.claude-preserve siblings,
    # never task directories — reaping those needs a liveness test this script
    # cannot make (the Python launch path in task_isolation.py creates task trees
    # without ever taking a lock; it never creates .claude-preserve, which is why
    # this sweep is safe and a task-dir sweep is not).
    #
    # Named rather than inlined so tests can extract and run it directly.
    reap_stale_preserve_dirs() {
        for _pdir in "$task_root"/*.claude-preserve; do
            [ -d "$_pdir" ] || continue   # unmatched glob stays literal
            # Test the whole TREE, not the directory inode. The CLI writes to
            # .claude/projects/<encoded>/*.jsonl, so the top directory's mtime
            # does not move as a session grows, and rename(2) does not touch the
            # renamed inode's mtime either. Keying on the directory mtime would
            # delete the most active long-lived sessions first.
            #
            # Check find's exit status too: swallowing it would turn any find
            # failure into empty output, i.e. "stale", i.e. delete. Skip on
            # error — the safe direction for a destructive test.
            _fresh="$(find "$_pdir" -newermt "-${preserve_max_age_days} days" \
                      -print -quit 2>/dev/null)" || continue
            [ -z "$_fresh" ] || continue
            _pid="${_pdir##*/}"; _pid="${_pid%.claude-preserve}"
            _plock="${_lock_dir}/openace-agent-task-${_pid}.lock"
            # `8<` — read only, NO O_CREAT. flock(2) does not require write
            # access, and this must never manufacture a lock file: lock files
            # cannot be reclaimed safely (unlink then same-name open yields a
            # different inode, so two holders stop excluding each other). A live
            # run creates its lock before touching anything, so an absent lock
            # file means no live run.
            if [ ! -e "$_plock" ]; then
                rm -rf -- "$_pdir" 2>/dev/null || true
                continue
            fi
            # The subshell scopes fd 8 so it is closed on every exit path and a
            # large task_root cannot leak descriptors. Never reuse fd 9 — that is
            # this run's own lock; re-locking or closing it would drop our mutex.
            ( flock -n 8 || exit 0; rm -rf -- "$_pdir" 2>/dev/null || true ) \
                8<"$_plock" || true
        done
    }
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
        # Issue #2403: arm reclaim HERE, not after the tree is built. Everything
        # from the preserve-move below through the restore is a window in which
        # an exit or a signal would otherwise strand a task tree — including the
        # cgroup fail-closed `exit 66` and the repo-integrity `exit 68` further
        # down, both of which run before the full on_exit trap is installed.
        #
        # The signal traps are pure `exit N` and are hoisted here for the same
        # reason: until they exist, a SIGTERM kills bash outright and the EXIT
        # trap never runs at all. They are re-registered (not duplicated) below.
        # `|| true` for the same reason every step of on_exit has it: errexit
        # stays live inside a trap handler, and a failure there both truncates
        # the handler before `rm -rf "$task_base"` and rewrites the exit status
        # to 1 — which would make _classify_isolated_exit_code misread the
        # fail-closed `exit 66` / `exit 68` this trap exists to cover.
        trap 'reclaim_task_tree || true' EXIT
        trap 'exit 129' HUP
        trap 'exit 130' INT
        trap 'exit 143' TERM
        if [ -d "$task_home/.claude" ]; then
            if ! _move_to_preserve "$task_home/.claude" "$preserve_claude_dir"; then
                echo "openace-run-as: cannot clear prior .claude-preserve dir; aborting to avoid nesting (Issue #2442)" >&2
                exit 70
            fi
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
            # Issue #2020: delegate controllers from the parent cgroup to its
            # children so task subgroups get memory.max, pids.max, cpu.max files.
            # Without this, mkdir creates the subgroup but it has no resource
            # limit files, and the writes below silently fail (|| true).
            # Idempotent: re-writing the same controllers is a no-op.
            echo "+memory +pids +cpu" > "$cgroup_root/cgroup.subtree_control" 2>/dev/null || true
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
    # Issue #2403 F1c: reap abandoned .claude-preserve dirs. Runs here rather
    # than beside the task-tree setup for two reasons: _conf_value() only exists
    # from this point on, and by now this run's own preserve dir has already been
    # restored into $task_home/.claude, so it cannot be a candidate.
    # Guarded on task_id because $task_root is only assigned in that block.
    if [ -n "$task_id" ]; then
        preserve_max_age_days="$(_conf_value agent_task_preserve_max_age_days)"
        # 0 passes a naive numeric guard but means "everything is stale": it
        # would delete every session history on every run. Treat it, and any
        # non-numeric value, as unset.
        case "$preserve_max_age_days" in
            0|''|*[!0-9]*) preserve_max_age_days=30 ;;
        esac
        reap_stale_preserve_dirs
    fi
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

    # Named for everything it does, not just the kill: a function called
    # "_kill_task_processes" invites a future early-return guard that would
    # silently stop reclaiming signature_tmp.
    _cleanup_task_runtime() {
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
        rm -f "$signature_tmp"
    }

    _revoke_task_acls() {
        if [ -f "$acl_registry" ]; then
            while IFS= read -r protected_path; do
                revoke_agent_access "$protected_path"
            done < "$acl_registry"
        fi
        # Issue #2403: remove rather than truncate. The registry's only reader
        # guards on `[ -f ]`, and the writer creates it, so absent and
        # present-but-empty are equivalent to every consumer — but removing it
        # also stops a second execution from re-creating a stray empty file.
        # This deletion must stay HERE, at the point of consumption: doing it in
        # reclaim_task_tree() would, on the early `exit 66` path (where this
        # revocation has not run yet), destroy an unconsumed record of still-live
        # grants and orphan the agent's write ACLs permanently.
        rm -f "$acl_registry"
    }

    cleanup_isolated() { _cleanup_task_runtime; _revoke_task_acls; }

    # Issue #2403: the EXIT handler once the agent can be running.
    #
    # Order matters and is NOT the obvious one. The orchestrator escalates
    # SIGTERM to SIGKILL after 5 seconds, so everything here shares one 5-second
    # budget — and the two halves have opposite recoverability: an ACL revocation
    # missed here is replayed from $acl_registry by the next invocation, while a
    # task tree left behind is never reclaimed by anything. So the tree goes
    # first and the (self-healing) ACL sweep goes last.
    #
    # Each step is `|| true` because errexit stays active inside trap handlers:
    # a non-zero return from the first would otherwise skip the rest, and the
    # most likely cause of that is a full /run — exactly the condition this
    # issue is about.
    on_exit() {
        _cleanup_task_runtime || true
        reclaim_task_tree     || true
        _revoke_task_acls     || true
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
    # Issue #2403: upgrade the EXIT handler now that an agent can be running —
    # from the bare tree reclaim armed at task-setup time to the full sequence.
    # Bash traps REPLACE rather than stack, so this expression must keep naming
    # reclaim_task_tree (via on_exit); reverting it to `cleanup_isolated` alone
    # would silently disarm reclamation for the whole rest of the run.
    trap on_exit EXIT
    # Bash services traps promptly while waiting for a background job. With a
    # foreground external command it may defer the trap until that command
    # exits, stranding this wrapper and its ACL lock after the parent sudo
    # process is terminated.
    # (These are re-registrations: the same handlers were armed at task setup so
    # that the earlier window is covered too.)
    trap 'exit 129' HUP
    trap 'exit 130' INT
    trap 'exit 143' TERM

    # Record every path before the first ACL grant. If this wrapper is
    # interrupted at any later instruction, the next serialized invocation
    # can revoke the exact abandoned grants before starting another agent.
    git_dir="$(git -C "$project_dir" rev-parse --absolute-git-dir 2>/dev/null || true)"
    common_dir="$(git -C "$project_dir" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)"
    # Issue #2403: the registry is now removed (not truncated) after use, so it
    # is created fresh here every run. Create it under a tight umask in the same
    # step as the write — a separate chmod would leave a world-readable window,
    # and a separate truncate would leave a 0-byte registry if killed between.
    ( umask 077; printf '%s\n' "$project_dir" "$git_dir" "$common_dir" > "$acl_registry" )
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
    # Issue #2403: on_exit, not cleanup_isolated — the trap is disarmed on the
    # next line, so this is the success path's only chance to reclaim the tree.
    on_exit
    trap - EXIT HUP INT TERM
    if ! verify_and_restore_git_entry "$project_dir" "$git_entry_before" "$git_acl_before"; then
        echo "OPENACE_REPO_INTEGRITY_VIOLATION: .git entry changed during agent execution" >&2
        exit 68
    fi
    rm -f "$signature_registry"
    # Reap the task cgroup now that the child has exited and cleanup killed
    # any stragglers.
    #
    # Issue #2403: this used to claim orphan runtime dirs were "reconciled by
    # the scheduler on restart (#2020)". No such reconciler was ever written —
    # nothing outside this script has ever removed a task directory — and that
    # comment is why one directory per run accumulated until /run was full.
    # on_exit now reclaims the tree on the normal path, on the early exits, and
    # on caught signals. A SIGKILL still strands one, which happens when this
    # cleanup overruns the orchestrator's 5-second SIGTERM grace rather than
    # from any external "crash"; the tmpfs self-heals on reboot, and a proper
    # sweep for that residue is tracked separately.
    if [ -n "$task_cgroup" ] && [ -d "$task_cgroup" ]; then
        rmdir "$task_cgroup" 2>/dev/null || true
    fi
    exit "$child_status"
fi

exec /usr/sbin/runuser -u "$target_user" -- "$@"
