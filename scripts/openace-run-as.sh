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
    git_entry_signature() {
        if [ -L "$project_dir/.git" ]; then
            printf 'link:%s' "$(readlink "$project_dir/.git")"
        elif [ -f "$project_dir/.git" ]; then
            printf 'file:%s:%s' \
                "$(stat -c '%d:%i:%a:%U:%G' "$project_dir/.git")" \
                "$(sha256sum "$project_dir/.git" | cut -d' ' -f1)"
        elif [ -d "$project_dir/.git" ]; then
            printf 'dir:%s' "$(stat -c '%d:%i:%a:%U:%G' "$project_dir/.git")"
        else
            printf 'missing'
        fi
    }
    git_entry_before="$(git_entry_signature)"
    if ! command -v setfacl >/dev/null 2>&1; then
        echo "openace-run-as: setfacl is required for isolated agent execution" >&2
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
    }

    # Recover safely after a previously interrupted wrapper before granting
    # this run access to anything. The registry is root-owned under /run.
    cleanup_isolated
    trap cleanup_isolated EXIT HUP INT TERM

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
        "GIT_CONFIG_VALUE_0=$project_dir" "$@"
    child_status=$?
    set -e
    cleanup_isolated
    trap - EXIT HUP INT TERM
    git_entry_after="$(git_entry_signature)"
    if [ "$git_entry_before" != "$git_entry_after" ]; then
        echo "OPENACE_REPO_INTEGRITY_VIOLATION: .git entry changed during agent execution" >&2
        exit 68
    fi
    exit "$child_status"
fi

exec /usr/sbin/runuser -u "$target_user" -- "$@"
