#!/bin/bash
# openace-mkdir — Secure mkdir wrapper for cross-user directory creation in multi-user mode.
#
# Security constraints:
#   - User must be a valid system user
#   - Path must be under /workspace/* or /home/*
#   - Audit logging with fallback to stderr
#
# Usage: openace-mkdir <user> <path>
#
# Exit codes:
#   0 - Success
#   1 - Invalid arguments
#   2 - Path validation failed
#   3 - User validation failed
#   4 - mkdir execution failed

set -euo pipefail

# Constants
AUDIT_LOG="/app/logs/sudoers-audit.log"

# Allowed path prefixes
ALLOWED_PREFIXES=("/workspace/" "/home/")

log_audit() {
    local msg="$1"
    local timestamp
    timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    local log_entry="[$timestamp] [openace-mkdir] $msg"

    # Try to write to audit log, fallback to stderr
    if ! echo "$log_entry" >> "$AUDIT_LOG" 2>/dev/null; then
        echo "[AUDIT_FALLBACK] $log_entry" >&2
    fi
}

usage() {
    echo "Usage: $0 <user> <path>" >&2
    echo "  Path must be under /workspace/* or /home/*" >&2
    exit 1
}

# Validate arguments
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

# Resolve path (handle symlinks and ..)
RESOLVED_PATH=""
PARENT_DIR=$(dirname "$TARGET_PATH")
if [ -d "$PARENT_DIR" ]; then
    RESOLVED_PARENT=$(readlink -f "$PARENT_DIR" 2>/dev/null || echo "$PARENT_DIR")
    RESOLVED_PATH="${RESOLVED_PARENT}/$(basename "$TARGET_PATH")"
else
    RESOLVED_PATH="$TARGET_PATH"
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

# Execute mkdir as target user
log_audit "caller=$(whoami) target_user=${TARGET_USER} path=${RESOLVED_PATH} result=attempt"

if sudo -u "$TARGET_USER" mkdir -p "$RESOLVED_PATH" 2>/dev/null; then
    log_audit "caller=$(whoami) target_user=${TARGET_USER} path=${RESOLVED_PATH} result=success"
    exit 0
else
    EXIT_CODE=$?
    log_audit "caller=$(whoami) target_user=${TARGET_USER} path=${RESOLVED_PATH} result=fail"
    exit 4
fi