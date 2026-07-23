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

if [ "$#" -lt 3 ]; then
    echo "Usage: $0 [--isolated] <user> <dir> <cmd> [args...]" >&2
    exit 64
fi

target_user="$1"
project_dir="$2"
shift 2

# Autonomous agents use a dedicated credentialless principal.  Unlike the
# legacy repo-owner launch, this account receives write ACLs only on worktree
# files; Git metadata is read-only so it cannot install hooks, rewrite remotes,
# or create refs that a later privileged push would trust.
if [ "$isolated" = true ]; then
    if [[ "$project_dir" != /* || "$project_dir" == *$'\n'* ]]; then
        echo "openace-run-as: isolated project path must be absolute and single-line" >&2
        exit 64
    fi
    if ! command -v flock >/dev/null 2>&1 || ! command -v pkill >/dev/null 2>&1; then
        echo "openace-run-as: flock and pkill are required for isolated execution" >&2
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
    acl_registry="/run/openace-agent-acl-${target_uid}"
    signature_registry="/run/openace-agent-git-signature-${target_uid}"
    signature_tmp="${signature_registry}.next"
    exec 9>"/run/lock/openace-agent-${target_uid}.lock"
    flock -x 9

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
        # The account is dedicated to autonomous work and wrappers using it
        # are serialized by fd 9, so this cannot kill an unrelated session.
        pkill -KILL -u "$target_uid" 2>/dev/null || true
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
        fi
        rm -f "$signature_registry"
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
    target_home="$(getent passwd "$target_user" | cut -d: -f6)"
    set +e
    /usr/sbin/runuser -u "$target_user" -- /usr/bin/env -i \
        "HOME=$target_home" "USER=$target_user" "LOGNAME=$target_user" \
        "LANG=${LANG:-C.UTF-8}" "LC_ALL=${LC_ALL:-}" "TMPDIR=/tmp" \
        "GIT_CONFIG_COUNT=1" "GIT_CONFIG_KEY_0=safe.directory" \
        "GIT_CONFIG_VALUE_0=$project_dir" "$@" <&0 9>&- &
    agent_child_pid=$!
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
    exit "$child_status"
fi

exec /usr/sbin/runuser -u "$target_user" -- "$@"
