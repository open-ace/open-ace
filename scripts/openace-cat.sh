#!/bin/bash
# openace-cat — Secure cat wrapper for cross-user file reading in multi-user mode.
#
# Security constraints:
#   - User must be a valid system user
#   - Path must be under /workspace/*, /home/*, or /tmp/*
#   - Path must not be in sensitive file blacklist
#   - Audit logging with fallback to stderr
#
# Usage: openace-cat <user> <path>
#
# Exit codes:
#   0 - Success
#   1 - Invalid arguments
#   2 - Path validation failed
#   3 - User validation failed
#   4 - Permission denied or file not found
#   5 - Sensitive file access denied

set -euo pipefail

# Constants
AUDIT_LOG="/app/logs/sudoers-audit.log"

# Allowed path prefixes
ALLOWED_PREFIXES=("/workspace/" "/home/" "/tmp/")

# Sensitive file patterns (blacklist)
SENSITIVE_PATTERNS=(
    "/etc/shadow"
    "/etc/passwd"
    "/etc/gshadow"
    "/etc/group"
    "/etc/sudoers"
    "/etc/sudoers.d/"
    "/root/.ssh/"
    "/.ssh/"
)

# Sensitive file regex patterns (private keys, etc.)
SENSITIVE_REGEX=(
    "^/.*/\.ssh/id_[a-z0-9]+$"      # Private keys: id_rsa, id_ed25519, etc.
    "^/.*/\.ssh/id_[a-z0-9]+\.pub$" # Public keys (less sensitive but still restricted
    "^/etc/shadow$"
    "^/etc/passwd$"
    "^/etc/gshadow$"
    "^/etc/group$"
)

log_audit() {
    local msg="$1"
    local timestamp
    timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    local log_entry="[$timestamp] [openace-cat] $msg"

    # Try to write to audit log, fallback to stderr
    if ! echo "$log_entry" >> "$AUDIT_LOG" 2>/dev/null; then
        echo "[AUDIT_FALLBACK] $log_entry" >&2
    fi
}

usage() {
    echo "Usage: $0 <user> <path>" >&2
    echo "  Path must be under /workspace/*, /home/*, or /tmp/*" >&2
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

# Check against sensitive file patterns (blacklist)
for pattern in "${SENSITIVE_PATTERNS[@]}"; do
    if [[ "$RESOLVED_PATH" == "$pattern"* ]]; then
        echo "ERROR: Access to sensitive file denied: $RESOLVED_PATH" >&2
        log_audit "caller=$(whoami) target_user=${TARGET_USER} path=${TARGET_PATH} resolved=${RESOLVED_PATH} result=reject_sensitive"
        exit 5
    fi
done

# Check against sensitive regex patterns
for regex in "${SENSITIVE_REGEX[@]}"; do
    if [[ "$RESOLVED_PATH" =~ $regex ]]; then
        echo "ERROR: Access to sensitive file denied: $RESOLVED_PATH" >&2
        log_audit "caller=$(whoami) target_user=${TARGET_USER} path=${TARGET_PATH} resolved=${RESOLVED_PATH} result=reject_sensitive"
        exit 5
    fi
done

# Validate path prefix
PATH_VALID=false
for prefix in "${ALLOWED_PREFIXES[@]}"; do
    if [[ "$RESOLVED_PATH" == "$prefix"* ]]; then
        PATH_VALID=true
        break
    fi
done

if [ "$PATH_VALID" = false ]; then
    echo "ERROR: Path '$RESOLVED_PATH' is outside allowed directories (/workspace/*, /home/*, /tmp/*)" >&2
    log_audit "caller=$(whoami) target_user=${TARGET_USER} path=${TARGET_PATH} resolved=${RESOLVED_PATH} result=reject_path"
    exit 2
fi

# Execute cat as target user
log_audit "caller=$(whoami) target_user=${TARGET_USER} path=${RESOLVED_PATH} result=attempt"

if sudo -u "$TARGET_USER" cat "$RESOLVED_PATH" 2>/dev/null; then
    log_audit "caller=$(whoami) target_user=${TARGET_USER} path=${RESOLVED_PATH} result=success"
    exit 0
else
    EXIT_CODE=$?
    log_audit "caller=$(whoami) target_user=${TARGET_USER} path=${RESOLVED_PATH} result=fail"
    exit 4
fi