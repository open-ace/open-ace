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

# Resolve path (handle symlinks and ..). For non-existent paths, resolve the
# parent directory then re-append the basename so path-prefix validation sees
# the real location the file will land in.
RESOLVED_PATH=""
if [ -e "$TARGET_PATH" ]; then
    RESOLVED_PATH=$(readlink -f "$TARGET_PATH" 2>/dev/null || echo "$TARGET_PATH")
else
    PARENT_DIR=$(dirname "$TARGET_PATH")
    if [ -d "$PARENT_DIR" ]; then
        RESOLVED_PARENT=$(readlink -f "$PARENT_DIR" 2>/dev/null || echo "$PARENT_DIR")
        RESOLVED_PATH="${RESOLVED_PARENT}/$(basename "$TARGET_PATH")"
    else
        RESOLVED_PATH="$TARGET_PATH"
    fi
fi

# Validate path prefix
PATH_VALID=false
for prefix in "${ALLOWED_PREFIXES[@]}"; do
    if [[ "$RESOLVED_PATH" == "$prefix"* ]]; then
        PATH_VALID=true
        break
    fi
done

if [ "$PATH_VALID" = false ]; then
    echo "ERROR: Path '$RESOLVED_PATH' is outside allowed directories (/workspace/*, /home/*)" >&2
    log_audit "caller=$(whoami) target_user=${TARGET_USER} path=${TARGET_PATH} resolved=${RESOLVED_PATH} result=reject_path"
    exit 2
fi

log_audit "caller=$(whoami) target_user=${TARGET_USER} path=${RESOLVED_PATH} result=attempt"

# Drop to target user via runuser and write stdin to the target path. runuser
# (not sudo) performs the user drop, so no extra sudoers rule for cp/tee is
# needed; the wrapper is already root via its own sudoers rule. tee truncates
# the file and writes stdin to it; stdout is discarded so it doesn't echo
# back. A temp file + atomic rename would be nicer for crash safety, but tee
# matches the simplicity of the single-user file.save() path.
if runuser -u "$TARGET_USER" -- tee "$RESOLVED_PATH" > /dev/null; then
    log_audit "caller=$(whoami) target_user=${TARGET_USER} path=${RESOLVED_PATH} result=success"
    exit 0
else
    EXIT_CODE=$?
    log_audit "caller=$(whoami) target_user=${TARGET_USER} path=${RESOLVED_PATH} result=fail code=${EXIT_CODE}"
    exit 4
fi
