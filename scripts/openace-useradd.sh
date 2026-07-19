#!/bin/bash
# openace-useradd — Secure useradd wrapper for multi-user workspace mode.
#
# Security constraints:
#   - Username must match Linux format: ^[a-z_][a-z0-9_-]{0,31}$
#   - Reserved usernames are forbidden (root, nobody, systemd-*, etc.)
#   - UID must be >= 1000 if specified
#   - Uses flock to prevent race conditions
#   - Audit logging with fallback to stderr
#
# Usage: openace-useradd <username> [-u <uid>]
#
# Exit codes:
#   0 - Success (or user already exists)
#   1 - Invalid arguments
#   2 - Username validation failed
#   3 - UID validation failed
#   4 - Lock acquisition failed
#   5 - useradd execution failed

set -euo pipefail

# Constants
LOCK_FILE="/var/lock/openace-useradd.lock"
LOCK_TIMEOUT=10
AUDIT_LOG="/app/logs/sudoers-audit.log"
MIN_UID=1000

# Reserved usernames (lowercase)
RESERVED_USERNAMES=(
    "root"
    "nobody"
    "daemon"
    "bin"
    "sys"
    "sync"
    "games"
    "man"
    "lp"
    "mail"
    "news"
    "uucp"
    "proxy"
    "www-data"
    "backup"
    "list"
    "irc"
    "gnats"
    "sshd"
    "mysql"
    "postgres"
    "open-ace"
    "openace"
)

# Reserved username prefixes
RESERVED_PREFIXES=("systemd-" "nobody" "polkitd" "geoclue" "colord" "saned" "pulse" "avahi" "cups")

# Ensure lock directory exists
mkdir -p "$(dirname "$LOCK_FILE")" 2>/dev/null || true

log_audit() {
    local msg="$1"
    local timestamp
    timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    local log_entry="[$timestamp] [openace-useradd] $msg"

    # Try to write to audit log, fallback to stderr
    if ! echo "$log_entry" >> "$AUDIT_LOG" 2>/dev/null; then
        echo "[AUDIT_FALLBACK] $log_entry" >&2
    fi
}

usage() {
    echo "Usage: $0 <username> [-u <uid>]" >&2
    echo "  Username must start with lowercase letter or underscore" >&2
    echo "  UID must be >= $MIN_UID if specified" >&2
    exit 1
}

# Parse arguments
USERNAME=""
UID_OPT=""

while [ "$#" -gt 0 ]; do
    case "$1" in
        -u)
            shift
            if [ "$#" -eq 0 ]; then
                echo "ERROR: -u requires an argument" >&2
                usage
            fi
            UID_OPT="$1"
            shift
            ;;
        -*)
            echo "ERROR: Unknown option: $1" >&2
            usage
            ;;
        *)
            if [ -z "$USERNAME" ]; then
                USERNAME="$1"
            else
                echo "ERROR: Unexpected argument: $1" >&2
                usage
            fi
            shift
            ;;
    esac
done

# Validate username is provided
if [ -z "$USERNAME" ]; then
    usage
fi

# Validate username format
if [[ ! "$USERNAME" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]]; then
    echo "ERROR: Invalid username format '$USERNAME'. Must match: ^[a-z_][a-z0-9_-]{0,31}$" >&2
    log_audit "caller=$(whoami) username=${USERNAME} result=reject_format"
    exit 2
fi

# Check against reserved usernames
for reserved in "${RESERVED_USERNAMES[@]}"; do
    if [ "$USERNAME" = "$reserved" ]; then
        echo "ERROR: Username '$USERNAME' is reserved" >&2
        log_audit "caller=$(whoami) username=${USERNAME} result=reject_reserved"
        exit 2
    fi
done

# Check against reserved prefixes
for prefix in "${RESERVED_PREFIXES[@]}"; do
    if [[ "$USERNAME" == "$prefix"* ]]; then
        echo "ERROR: Username '$USERNAME' matches reserved prefix '$prefix'" >&2
        log_audit "caller=$(whoami) username=${USERNAME} result=reject_reserved_prefix"
        exit 2
    fi
done

# Validate UID if specified
if [ -n "$UID_OPT" ]; then
    if [[ ! "$UID_OPT" =~ ^[0-9]+$ ]]; then
        echo "ERROR: UID must be a number" >&2
        usage
    fi

    if [ "$UID_OPT" -lt "$MIN_UID" ]; then
        echo "ERROR: UID $UID_OPT is below minimum $MIN_UID (system UIDs not allowed)" >&2
        log_audit "caller=$(whoami) username=${USERNAME} uid=${UID_OPT} result=reject_uid_low"
        exit 3
    fi
fi

# Check if user already exists
if id "$USERNAME" &>/dev/null; then
    echo "INFO: User '$USERNAME' already exists" >&2
    log_audit "caller=$(whoami) username=${USERNAME} uid=${UID_OPT:-auto} result=already_exists"
    exit 0
fi

# Acquire lock and execute
exec 200>"$LOCK_FILE"
if ! flock -w "$LOCK_TIMEOUT" 200; then
    echo "ERROR: Failed to acquire lock within ${LOCK_TIMEOUT}s" >&2
    log_audit "caller=$(whoami) username=${USERNAME} uid=${UID_OPT:-auto} result=lock_timeout"
    exit 4
fi

# Build useradd command
USERADD_CMD=("useradd" "-m" "-s" "/bin/bash")
if [ -n "$UID_OPT" ]; then
    USERADD_CMD+=("-u" "$UID_OPT")
fi
USERADD_CMD+=("$USERNAME")

# Execute useradd
if "${USERADD_CMD[@]}"; then
    log_audit "caller=$(whoami) username=${USERNAME} uid=${UID_OPT:-auto} result=success"
    exit 0
else
    log_audit "caller=$(whoami) username=${USERNAME} uid=${UID_OPT:-auto} result=fail"
    exit 5
fi
