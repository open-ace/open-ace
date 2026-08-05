#!/bin/bash
# openace-rm — Secure rm wrapper for multi-user workspace mode.
#
# Security constraints:
#   - Target user must exist and have UID >= 1000
#   - Path must be under /workspace/* or /home/*
#   - Symlink escape detection
#   - Owner must match target user
#   - Dangerous options are rejected
#   - TOCTOU protection via flock
#   - Audit logging
#
# Usage: openace-rm <target_user> <path> [rm_options...]
#
# Exit codes:
#   0 - Success
#   1 - Invalid arguments
#   2 - Path validation failed
#   3 - User validation failed
#   4 - Lock acquisition failed
#   5 - rm execution failed
#   6 - TOCTOU attack detected
#   7 - Symlink escape detected

set -euo pipefail

# Constants
LOCK_DIR="/var/lock"
AUDIT_LOG="/app/logs/sudoers-audit.log"
MIN_UID=1000
LOCK_TIMEOUT=10

# Reserved usernames that cannot be used as target
RESERVED_USERNAMES="root bin daemon sys sync games man lp mail news uucp proxy www-data backup list irc gnats nobody systemd-network systemd-resolve systemd-timesync messagebus syslog uuidd"

# Forbidden path patterns (protected directories)
FORBIDDEN_PATTERNS="/etc /root /var /.ssh /.gnupg /.config/openace"

# Dangerous rm options that must be rejected
DANGEROUS_OPTIONS="--no-preserve-root --one-file-system --xdev"

# Ensure lock directory exists
mkdir -p "$LOCK_DIR" 2>/dev/null || true

log_audit() {
    local msg="$1"
    local timestamp
    timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    local log_entry="[$timestamp] [openace-rm] $msg"

    # Try to write to audit log, fallback to stderr
    if ! echo "$log_entry" >> "$AUDIT_LOG" 2>/dev/null; then
        echo "[AUDIT_FALLBACK] $log_entry" >&2
    fi
}

usage() {
    echo "Usage: $0 <target_user> <path> [rm_options...]" >&2
    echo "  Target user must have UID >= $MIN_UID" >&2
    echo "  Path must be under /workspace/* or /home/*" >&2
    echo "  Dangerous options like --no-preserve-root are rejected" >&2
    exit 1
}

# Validate arguments
if [ "$#" -lt 2 ]; then
    usage
fi

TARGET_USER="$1"
TARGET_PATH="$2"
shift 2
RM_OPTIONS=("$@")

# ========================================
# 1. Parameter validation
# ========================================

# Check target_user format (POSIX username)
if [[ ! "$TARGET_USER" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]]; then
    echo "ERROR: Invalid username format: $TARGET_USER" >&2
    log_audit "caller=$(whoami) target=${TARGET_USER} path=${TARGET_PATH} result=reject_invalid_username"
    exit 1
fi

# Check for shell injection in options
INJECTION_PATTERN='[;|`$]'
for opt in "${RM_OPTIONS[@]}"; do
    if [[ "$opt" =~ $INJECTION_PATTERN ]]; then
        echo "ERROR: Option contains forbidden characters: $opt" >&2
        log_audit "caller=$(whoami) target=${TARGET_USER} path=${TARGET_PATH} result=reject_injection"
        exit 1
    fi
done

# Check for dangerous options
for opt in "${RM_OPTIONS[@]}"; do
    for dangerous in $DANGEROUS_OPTIONS; do
        if [[ "$opt" == "$dangerous" ]] || [[ "$opt" == "${dangerous}="* ]]; then
            echo "ERROR: Dangerous option rejected: $opt" >&2
            log_audit "caller=$(whoami) target=${TARGET_USER} path=${TARGET_PATH} result=reject_dangerous_option option=$opt"
            exit 1
        fi
    done
done

# ========================================
# 2. Target user validation
# ========================================

# Check if user exists
if ! getent passwd "$TARGET_USER" >/dev/null 2>&1; then
    echo "ERROR: User '$TARGET_USER' does not exist" >&2
    log_audit "caller=$(whoami) target=${TARGET_USER} path=${TARGET_PATH} result=reject_user_not_found"
    exit 3
fi

# Get UID
TARGET_UID=$(getent passwd "$TARGET_USER" | cut -d: -f3)

# Check UID >= MIN_UID
if [ "$TARGET_UID" -lt "$MIN_UID" ]; then
    echo "ERROR: User '$TARGET_USER' has UID $TARGET_UID, below minimum $MIN_UID (system users not allowed)" >&2
    log_audit "caller=$(whoami) target=${TARGET_USER} path=${TARGET_PATH} result=reject_uid_low uid=$TARGET_UID"
    exit 3
fi

# Check reserved usernames
for reserved in $RESERVED_USERNAMES; do
    if [ "$TARGET_USER" = "$reserved" ]; then
        echo "ERROR: User '$TARGET_USER' is a reserved system user" >&2
        log_audit "caller=$(whoami) target=${TARGET_USER} path=${TARGET_PATH} result=reject_reserved_user"
        exit 3
    fi
done

# ========================================
# 3. Path validation
# ========================================

# Check for empty path
if [ -z "$TARGET_PATH" ]; then
    echo "ERROR: Path cannot be empty" >&2
    log_audit "caller=$(whoami) target=${TARGET_USER} path=empty result=reject_empty_path"
    exit 2
fi

# Check for root directory
if [ "$TARGET_PATH" = "/" ]; then
    echo "ERROR: Root directory '/' is not allowed" >&2
    log_audit "caller=$(whoami) target=${TARGET_USER} path=/ result=reject_root_dir"
    exit 2
fi

# Resolve to canonical path
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

# Check for ".." components (should already be resolved, but double-check)
if [[ "$RESOLVED_PATH" == *"../"* ]] || [[ "$RESOLVED_PATH" == *"/.."* && "$RESOLVED_PATH" != "/.." ]]; then
    echo "ERROR: Path contains '..' components: $RESOLVED_PATH" >&2
    log_audit "caller=$(whoami) target=${TARGET_USER} path=${TARGET_PATH} resolved=${RESOLVED_PATH} result=reject_dotdot"
    exit 2
fi

# Check path prefix whitelist
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
    log_audit "caller=$(whoami) target=${TARGET_USER} path=${TARGET_PATH} resolved=${RESOLVED_PATH} result=reject_path_outside_allowed"
    exit 2
fi

# Check forbidden path patterns
for pattern in $FORBIDDEN_PATTERNS; do
    if [[ "$RESOLVED_PATH" == *"$pattern"* ]] || [[ "$RESOLVED_PATH" == "$pattern"* ]]; then
        echo "ERROR: Path matches forbidden pattern: $pattern" >&2
        log_audit "caller=$(whoami) target=${TARGET_USER} path=${TARGET_PATH} resolved=${RESOLVED_PATH} result=reject_forbidden_pattern pattern=$pattern"
        exit 2
    fi
done

# ========================================
# 4. Symlink escape detection
# ========================================

check_symlink_escape() {
    local path_to_check="$1"
    local current_path=""
    local allowed_prefixes_local=("/workspace" "/home")

    # Walk through each component of the path
    IFS='/' read -ra components <<< "$path_to_check"
    for component in "${components[@]}"; do
        [ -z "$component" ] && continue
        current_path="${current_path}/${component}"

        # Check if this component is a symlink
        if [ -L "$current_path" ]; then
            local link_target
            link_target=$(readlink -f "$current_path" 2>/dev/null || echo "")

            if [ -n "$link_target" ]; then
                # Check if symlink target is still within allowed prefixes
                local target_valid=false
                for prefix in "${allowed_prefixes_local[@]}"; do
                    if [[ "$link_target" == "$prefix"* ]] || [[ "$link_target" == "$prefix/"* ]]; then
                        target_valid=true
                        break
                    fi
                done

                if [ "$target_valid" = false ]; then
                    return 1
                fi
            fi
        fi
    done
    return 0
}

# Only check symlink escape for existing paths
if [ -e "$TARGET_PATH" ]; then
    if ! check_symlink_escape "$TARGET_PATH"; then
        echo "ERROR: Symlink escape detected: path points outside allowed directories" >&2
        log_audit "caller=$(whoami) target=${TARGET_USER} path=${TARGET_PATH} result=reject_symlink_escape"
        exit 7
    fi
fi

# ========================================
# 5. Owner validation (for existing files)
# ========================================

if [ -e "$TARGET_PATH" ]; then
    FILE_OWNER=$(stat -c '%U' "$TARGET_PATH" 2>/dev/null || echo "")

    if [ -z "$FILE_OWNER" ]; then
        echo "ERROR: Cannot determine owner of: $TARGET_PATH" >&2
        log_audit "caller=$(whoami) target=${TARGET_USER} path=${TARGET_PATH} result=reject_owner_unknown"
        exit 2
    fi

    if [ "$FILE_OWNER" != "$TARGET_USER" ]; then
        echo "ERROR: File owner '$FILE_OWNER' does not match target user '$TARGET_USER'" >&2
        log_audit "caller=$(whoami) target=${TARGET_USER} path=${TARGET_PATH} owner=${FILE_OWNER} result=reject_owner_mismatch"
        exit 2
    fi
fi

# ========================================
# 6. Recursive delete cross-user check
# ========================================

# Check if recursive delete is requested
HAS_RECURSIVE=false
for opt in "${RM_OPTIONS[@]}"; do
    if [[ "$opt" == "-r" ]] || [[ "$opt" == "-R" ]] || [[ "$opt" == "--recursive" ]]; then
        HAS_RECURSIVE=true
        break
    fi
done

# If recursive and target is a directory, check for other users' files
if [ "$HAS_RECURSIVE" = true ] && [ -d "$TARGET_PATH" ]; then
    # Find files not owned by target user
    OTHER_USER_FILES=$(find "$TARGET_PATH" -type f ! -user "$TARGET_USER" 2>/dev/null | head -1 || true)

    if [ -n "$OTHER_USER_FILES" ]; then
        echo "ERROR: Directory contains files owned by other users. Cannot recursively delete." >&2
        log_audit "caller=$(whoami) target=${TARGET_USER} path=${TARGET_PATH} result=reject_cross_user_files"
        exit 2
    fi
fi

# ========================================
# 7. TOCTOU protection
# ========================================

LOCK_FILE="${LOCK_DIR}/openace-rm-${TARGET_USER}.lock"

# Acquire lock
exec 200>"$LOCK_FILE"
if ! flock -w "$LOCK_TIMEOUT" 200; then
    echo "ERROR: Failed to acquire lock within ${LOCK_TIMEOUT}s" >&2
    log_audit "caller=$(whoami) target=${TARGET_USER} path=${TARGET_PATH} result=lock_timeout"
    exit 4
fi

# Re-validate path after acquiring lock (TOCTOU protection)
RESOLVED_PATH_AFTER=""
if [ -e "$TARGET_PATH" ]; then
    RESOLVED_PATH_AFTER=$(readlink -f "$TARGET_PATH" 2>/dev/null || echo "$TARGET_PATH")
else
    PARENT_DIR=$(dirname "$TARGET_PATH")
    if [ -d "$PARENT_DIR" ]; then
        RESOLVED_PARENT=$(readlink -f "$PARENT_DIR" 2>/dev/null || echo "$PARENT_DIR")
        RESOLVED_PATH_AFTER="${RESOLVED_PARENT}/$(basename "$TARGET_PATH")"
    else
        RESOLVED_PATH_AFTER="$TARGET_PATH"
    fi
fi

if [ "$RESOLVED_PATH_AFTER" != "$RESOLVED_PATH" ]; then
    echo "ERROR: Path changed during validation (TOCTOU attack detected)" >&2
    log_audit "caller=$(whoami) target=${TARGET_USER} path=${TARGET_PATH} before=${RESOLVED_PATH} after=${RESOLVED_PATH_AFTER} result=reject_toctou"
    flock -u 200
    exit 6
fi

# ========================================
# 8. Execute rm
# ========================================

if rm "${RM_OPTIONS[@]}" "$TARGET_PATH"; then
    log_audit "caller=$(whoami) target=${TARGET_USER} path=${TARGET_PATH} result=success"
    flock -u 200
    exit 0
else
    log_audit "caller=$(whoami) target=${TARGET_USER} path=${TARGET_PATH} result=fail"
    flock -u 200
    exit 5
fi
