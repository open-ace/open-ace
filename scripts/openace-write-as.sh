#!/bin/bash
# openace-write-as — Secure cross-user file write wrapper for multi-user mode.
#
# Problem (Issue #1916): in Package non-root multi-user mode the openace
# service account cannot write to /home/<system_account>/... (0700 home).
# Upload must write as the target user. cp/tee/mv/install are NOT in the
# sudoers OPENACE_UTILS whitelist, so `sudo -u <user> cp ...` is denied.
# This wrapper is invoked as root via a dedicated sudoers rule, validates
# user + path, then drops to the target user via runuser and writes stdin
# to the target path. No sudoers rule for cp/tee/mv is required.
#
# Usage: openace-write-as <user> <path>
#   File content arrives on stdin; the file is created/truncated at <path>.
#
# Exit codes:
#   0 - Success
#   1 - Invalid arguments
#   2 - Path validation failed
#   3 - User validation failed
#   4 - Write failed
#   5 - Target is a symbolic link
#   6 - Target is a directory
#
# Capability marker (Issue #3410). app/routes/fs.py greps the INSTALLED wrapper
# for this exact line and refuses uploads fail-closed when it is absent, so a
# host still running a pre-#3410 wrapper cannot silently keep the escalation
# while the capability contract reports filesystem_api: enforced. Keep it in
# sync with _WRITE_AS_CAPABILITY_SENTINEL (a test asserts both files agree).
# openace-write-as-capability: symlink-refusal=1

set -euo pipefail

AUDIT_LOG="/app/logs/sudoers-audit.log"
MIN_UID=1000
ALLOWED_PREFIXES=("/workspace/" "/home/")

log_audit() {
    local msg="$1"
    local timestamp
    timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    local log_entry="[$timestamp] [openace-write-as] $msg"
    if ! echo "$log_entry" >> "$AUDIT_LOG" 2>/dev/null; then
        echo "[AUDIT_FALLBACK] $log_entry" >&2
    fi
}

usage() {
    echo "Usage: $0 <user> <path>" >&2
    echo "  File content must be piped on stdin." >&2
    echo "  Path must be under /workspace/* or /home/*" >&2
    exit 1
}

if [ "$#" -ne 2 ]; then
    usage
fi

TARGET_USER="$1"
TARGET_PATH="$2"

# Validate user exists
if ! id "$TARGET_USER" &>/dev/null; then
    echo "ERROR: User '$TARGET_USER' does not exist" >&2
    log_audit "caller=$(whoami) target_user=${TARGET_USER} path=${TARGET_PATH} result=reject_user_not_found"
    exit 3
fi

# Validate UID >= MIN_UID (no system users)
TARGET_UID=$(id -u "$TARGET_USER")
if [ "$TARGET_UID" -lt "$MIN_UID" ]; then
    echo "ERROR: UID $TARGET_UID is below minimum $MIN_UID (system users not allowed)" >&2
    log_audit "caller=$(whoami) target_user=${TARGET_USER} path=${TARGET_PATH} result=reject_uid_low"
    exit 3
fi

# Issue #3410 (review round 1): every filesystem probe below runs AS the target
# user, never as root. As root, the probes followed paths the target user may
# not even traverse, and because they ran before the prefix check they also
# told the caller what kind of entry an arbitrary path was (symlink -> 5,
# directory -> 6, anything else -> 2). stdin is /dev/null so nothing in the PAM
# chain can consume bytes of the upload body.
as_target() {
    runuser -u "$TARGET_USER" -- "$@" </dev/null
}

path_allowed() {
    local prefix
    for prefix in "${ALLOWED_PREFIXES[@]}"; do
        if [[ "$1" == "$prefix"* ]]; then
            return 0
        fi
    done
    return 1
}

reject_path() {
    echo "ERROR: Path '$1' is outside allowed directories (/workspace/*, /home/*)" >&2
    log_audit "caller=$(whoami) target_user=${TARGET_USER} path=${TARGET_PATH} resolved=$1 result=reject_path"
    exit 2
}

# Prefix check on the path AS GIVEN, before touching the filesystem at all.
path_allowed "$TARGET_PATH" || reject_path "$TARGET_PATH"

# Resolve the PARENT directory only and re-append the basename: resolving the
# target itself would follow a symlink at the final component (the #3410
# escape). The resolved path must pass the prefix check too.
RESOLVED_PARENT=""
PARENT_DIR=$(dirname "$TARGET_PATH")
if as_target test -d "$PARENT_DIR"; then
    RESOLVED_PARENT=$(as_target readlink -f "$PARENT_DIR" 2>/dev/null || echo "$PARENT_DIR")
    RESOLVED_PATH="${RESOLVED_PARENT}/$(basename "$TARGET_PATH")"
else
    RESOLVED_PATH="$TARGET_PATH"
fi
path_allowed "$RESOLVED_PATH" || reject_path "$RESOLVED_PATH"

# Issue #3410: never write THROUGH a symlink. The caller cannot do this check
# reliably — in this deployment shape the web process is a service account that
# cannot traverse the target user's 0700 home, so its os.path.islink() probe
# returns False on EACCES. This wrapper is the enforcement point.
if as_target test -L "$RESOLVED_PATH"; then
    echo "ERROR: Target '$TARGET_PATH' is a symbolic link; refusing to write through it" >&2
    log_audit "caller=$(whoami) target_user=${TARGET_USER} path=${TARGET_PATH} result=reject_symlink"
    exit 5
fi

# Issue #3410: `mv src dir` moves src INTO dir and exits 0, so a directory
# named like the upload would silently swallow the temp file while the API
# answered 200 with the directory's path. `tee` used to fail here ("Is a
# directory"); keep that a refusal, with its own code. This test MUST come
# after the -L check: test -d follows symlinks, so a symlink-to-a-directory has
# to be reported as a symlink, not as a directory.
if as_target test -d "$RESOLVED_PATH"; then
    echo "ERROR: Target '$TARGET_PATH' is a directory" >&2
    log_audit "caller=$(whoami) target_user=${TARGET_USER} path=${TARGET_PATH} result=reject_directory"
    exit 6
fi

log_audit "caller=$(whoami) target_user=${TARGET_USER} path=${RESOLVED_PATH} result=attempt"

# Drop to target user via runuser and write stdin to a TEMP file in the target
# directory, then rename it into place. runuser (not sudo) performs the user
# drop, so no extra sudoers rule for cp/tee/mv is needed; the wrapper is
# already root via its own sudoers rule.
#
# Issue #3410: the write is a temp + rename rather than `tee "$RESOLVED_PATH"`
# because the [ -L ] check above and the write are two syscalls apart — the
# target user could swap the name in between, and tee follows a symlink at the
# final component. `mv` uses rename(2), which replaces the DIRECTORY ENTRY and
# never follows a symlink there. `-T` (--no-target-directory) forces rename
# semantics so the directory case fails even if the [ -d ] pre-check raced.
# The traps remove the temp file on every error path and on SIGINT/SIGTERM; a
# leaked dotfile would be invisible to /fs browse and search and effectively
# undeletable by the user. (SIGKILL is untrappable — the caller sends SIGTERM
# first for exactly this reason.) The signal traps EXIT after cleaning up: a
# trap that only cleaned up let the script run on into `mv` (#3410 review).
# tee is the only sub-call that reads stdin: it IS the upload body.
TMP_PATH=$(as_target mktemp "${RESOLVED_PARENT:-$(dirname "$RESOLVED_PATH")}/.openace-write-as.XXXXXX") || {
    log_audit "caller=$(whoami) target_user=${TARGET_USER} path=${RESOLVED_PATH} result=fail_mktemp"
    exit 4
}
cleanup_tmp() {
    # Empty after a successful rename: the EXIT trap then costs no runuser
    # fork, and a file the user later recreates at the temp name cannot be
    # deleted by it either.
    if [ -n "$TMP_PATH" ]; then
        as_target rm -f "$TMP_PATH" 2>/dev/null || true
    fi
}
trap cleanup_tmp EXIT
trap 'cleanup_tmp; exit 130' INT
trap 'cleanup_tmp; exit 143' TERM

if runuser -u "$TARGET_USER" -- tee "$TMP_PATH" > /dev/null \
   && as_target mv -fT "$TMP_PATH" "$RESOLVED_PATH"; then
    TMP_PATH=""
    log_audit "caller=$(whoami) target_user=${TARGET_USER} path=${RESOLVED_PATH} result=success"
    exit 0
else
    EXIT_CODE=$?
    log_audit "caller=$(whoami) target_user=${TARGET_USER} path=${RESOLVED_PATH} result=fail code=${EXIT_CODE}"
    exit 4
fi
