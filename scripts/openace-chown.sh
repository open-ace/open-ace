#!/bin/bash
# openace-chown — Secure chown wrapper for multi-user workspace mode.
#
# Security constraints:
#   - Path must be under /workspace/* or /home/*
#   - UID/GID must be >= 1000 (no system users)
#   - Uses flock to prevent race conditions
#   - Audit logging with fallback to stderr
#
# Usage: openace-chown <uid>:<gid> <path>
#
# Exit codes:
#   0 - Success
#   1 - Invalid arguments
#   2 - Path validation failed
#   3 - UID/GID validation failed
#   4 - Lock acquisition failed
#   5 - chown execution failed

set -euo pipefail

# Constants
LOCK_FILE="/var/lock/openace-chown.lock"
LOCK_TIMEOUT=10
AUDIT_LOG="/app/logs/sudoers-audit.log"
MIN_UID=1000

# Ensure lock directory exists
mkdir -p "$(dirname "$LOCK_FILE")" 2>/dev/null || true

log_audit() {
    local msg="$1"
    local timestamp
    timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    local log_entry="[$timestamp] [openace-chown] $msg"

    # Try to write to audit log, fallback to stderr
    if ! echo "$log_entry" >> "$AUDIT_LOG" 2>/dev/null; then
        echo "[AUDIT_FALLBACK] $log_entry" >&2
    fi
}

usage() {
    echo "Usage: $0 <uid>:<gid> <path>" >&2
    echo "  UID/GID must be >= $MIN_UID" >&2
    echo "  Path must be under /workspace/* or /home/*" >&2
    exit 1
}

# Validate arguments
if [ "$#" -ne 2 ]; then
    usage
fi

OWNERSHIP="$1"
TARGET_PATH="$2"

# Parse UID:GID
if [[ ! "$OWNERSHIP" =~ ^([0-9]+):([0-9]+)$ ]]; then
    echo "ERROR: Invalid ownership format. Expected <uid>:<gid>" >&2
    exit 1
fi

UID_NUM="${BASH_REMATCH[1]}"
GID_NUM="${BASH_REMATCH[2]}"

# Validate UID/GID range
if [ "$UID_NUM" -lt "$MIN_UID" ]; then
    echo "ERROR: UID $UID_NUM is below minimum $MIN_UID (system users not allowed)" >&2
    log_audit "caller=$(whoami) target=${OWNERSHIP} path=${TARGET_PATH} result=reject_uid_low"
    exit 3
fi

if [ "$GID_NUM" -lt "$MIN_UID" ]; then
    echo "ERROR: GID $GID_NUM is below minimum $MIN_UID (system groups not allowed)" >&2
    log_audit "caller=$(whoami) target=${OWNERSHIP} path=${TARGET_PATH} result=reject_gid_low"
    exit 3
fi

# Resolve path (handle symlinks and ..)
RESOLVED_PATH=""
if [ -e "$TARGET_PATH" ]; then
    RESOLVED_PATH=$(readlink -f "$TARGET_PATH" 2>/dev/null || echo "$TARGET_PATH")
else
    # For non-existent paths, resolve the parent directory
    PARENT_DIR=$(dirname "$TARGET_PATH")
    if [ -d "$PARENT_DIR" ]; then
        RESOLVED_PARENT=$(readlink -f "$PARENT_DIR" 2>/dev/null || echo "$PARENT_DIR")
        RESOLVED_PATH="${RESOLVED_PARENT}/$(basename "$TARGET_PATH")"
    else
        RESOLVED_PATH="$TARGET_PATH"
    fi
fi

# Validate path prefix
ALLOWED_PREFIXES=("/workspace/" "/home/")
PATH_VALID=false
for prefix in "${ALLOWED_PREFIXES[@]}"; do
    if [[ "$RESOLVED_PATH" == "$prefix"* ]]; then
        PATH_VALID=true
        break
    fi
done

if [ "$PATH_VALID" = false ]; then
    echo "ERROR: Path '$RESOLVED_PATH' is outside allowed directories (/workspace/*, /home/*)" >&2
    log_audit "caller=$(whoami) target=${OWNERSHIP} path=${TARGET_PATH} resolved=${RESOLVED_PATH} result=reject_path"
    exit 2
fi

# Acquire lock and execute
exec 200>"$LOCK_FILE"
if ! flock -w "$LOCK_TIMEOUT" 200; then
    echo "ERROR: Failed to acquire lock within ${LOCK_TIMEOUT}s" >&2
    log_audit "caller=$(whoami) target=${OWNERSHIP} path=${TARGET_PATH} result=lock_timeout"
    exit 4
fi

# Execute chown
if chown "$OWNERSHIP" "$TARGET_PATH"; then
    log_audit "caller=$(whoami) target=${OWNERSHIP} path=${TARGET_PATH} result=success"
    exit 0
else
    log_audit "caller=$(whoami) target=${OWNERSHIP} path=${TARGET_PATH} result=fail"
    exit 5
fi
