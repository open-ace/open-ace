#!/bin/bash
# upgrade-sudoers-security — Sudoers security upgrade script for Issue #2181.
#
# This script upgrades sudoers configuration from legacy rules (rm *, OPENACE_CLI)
# to the new security model (openace-rm wrapper, openace-run-as --isolated).
#
# Usage: upgrade-sudoers-security [--dry-run | --force | --check]
#
# Options:
#   --dry-run    Show what would be changed without making changes
#   --force      Force upgrade even if already upgraded
#   --check      Only check if upgrade is needed (exit 0 = needed, exit 1 = not needed)
#
# Exit codes:
#   0 - Success
#   1 - Invalid arguments
#   2 - No sudoers file found
#   3 - Upgrade failed
#   4 - visudo validation failed
#   5 - Backup failed

set -euo pipefail

# Constants (can be overridden via environment variables)
SUDOERS_FILE="${SUDOERS_FILE:-/etc/sudoers.d/open-ace-webui}"
BACKUP_DIR="${BACKUP_DIR:-/app/logs}"
AUDIT_LOG="${AUDIT_LOG:-/app/logs/sudoers-audit.log}"
WRAPPER_DIR="${WRAPPER_DIR:-/usr/local/bin}"

# Wrappers that should have sudoers rules
SECURITY_WRAPPERS="openace-chown openace-useradd openace-cat openace-mkdir openace-write-as openace-rm"

# Deprecated rules to remove
DEPRECATED_PATTERNS=(
    "/usr/bin/rm *"
    "/usr/bin/cat *"
    "/usr/bin/chown *"
    "/usr/bin/useradd *"
    "Cmnd_Alias OPENACE_CLI"
)

# Sensitive variables that should not be in env_keep
SENSITIVE_VARS="OPENAI_API_KEY|ANTHROPIC_API_KEY|GEMINI_API_KEY|OPENCLAW_TOKEN|GH_TOKEN"

log_audit() {
    local msg="$1"
    local timestamp
    timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    local log_entry="[$timestamp] [upgrade-sudoers-security] $msg"

    # Try to write to audit log, fallback to stderr
    if ! echo "$log_entry" >> "$AUDIT_LOG" 2>/dev/null; then
        echo "[AUDIT_FALLBACK] $log_entry" >&2
    fi
}

usage() {
    echo "Usage: $0 [--dry-run | --force | --check]" >&2
    echo "" >&2
    echo "Options:" >&2
    echo "  --dry-run    Show what would be changed without making changes" >&2
    echo "  --force      Force upgrade even if already upgraded" >&2
    echo "  --check      Only check if upgrade is needed" >&2
    exit 1
}

# Check if sudoers file exists
check_sudoers_exists() {
    if [ ! -f "$SUDOERS_FILE" ]; then
        echo "ERROR: sudoers file not found: $SUDOERS_FILE" >&2
        return 1
    fi
    return 0
}

# Check if upgrade is needed
check_upgrade_needed() {
    local needs_upgrade=0

    # Check for deprecated patterns
    for pattern in "${DEPRECATED_PATTERNS[@]}"; do
        if grep -qF "$pattern" "$SUDOERS_FILE" 2>/dev/null; then
            echo "Found deprecated pattern: $pattern"
            needs_upgrade=1
        fi
    done

    # Check for sensitive variables in env_keep
    if grep -E "env_keep.*(${SENSITIVE_VARS})" "$SUDOERS_FILE" 2>/dev/null | grep -q "."; then
        echo "Found sensitive variables in env_keep"
        needs_upgrade=1
    fi

    # Check if openace-rm wrapper rule exists
    if ! grep -q "openace-rm" "$SUDOERS_FILE" 2>/dev/null; then
        echo "Missing openace-rm wrapper rule"
        needs_upgrade=1
    fi

    return $needs_upgrade
}

# Backup current sudoers file
backup_sudoers() {
    local backup_file="${SUDOERS_FILE}.bak.$(date +%s)"

    if ! cp "$SUDOERS_FILE" "$backup_file"; then
        echo "ERROR: Failed to backup sudoers file" >&2
        return 1
    fi

    chmod 440 "$backup_file"
    echo "Backed up sudoers to: $backup_file"
    log_audit "Backed up sudoers to $backup_file"
    return 0
}

# Remove deprecated rules
remove_deprecated_rules() {
    local tmp_file=$(mktemp)

    # Copy original file
    cat "$SUDOERS_FILE" > "$tmp_file"

    # Remove lines containing deprecated patterns
    for pattern in "${DEPRECATED_PATTERNS[@]}"; do
        if grep -qF "$pattern" "$tmp_file" 2>/dev/null; then
            echo "Removing deprecated rule: $pattern"
            grep -vF "$pattern" "$tmp_file" > "${tmp_file}.tmp" || true
            mv "${tmp_file}.tmp" "$tmp_file"
        fi
    done

    # Remove lines with sensitive variables in env_keep
    if grep -E "env_keep.*(${SENSITIVE_VARS})" "$tmp_file" 2>/dev/null | grep -q "."; then
        echo "Removing sensitive variables from env_keep"
        grep -vE "env_keep.*(${SENSITIVE_VARS})" "$tmp_file" > "${tmp_file}.tmp" || true
        mv "${tmp_file}.tmp" "$tmp_file"
    fi

    cat "$tmp_file"
    rm -f "$tmp_file"
}

# Add security wrapper rules
add_wrapper_rules() {
    local tmp_file=$(mktemp)

    # Copy cleaned content
    cat > "$tmp_file"

    # Add wrapper rules if wrappers exist
    echo "" >> "$tmp_file"
    echo "# 【安全加固 Issue #2181】安全 wrapper 规则（自动添加）" >> "$tmp_file"

    for wrapper in $SECURITY_WRAPPERS; do
        local wrapper_path="${WRAPPER_DIR}/${wrapper}"
        if [ -x "$wrapper_path" ]; then
            # Check if rule already exists
            if ! grep -qF "$wrapper_path" "$tmp_file" 2>/dev/null; then
                echo "Adding wrapper rule: $wrapper"
                echo "open-ace ALL=(root) NOPASSWD: ${wrapper_path} *" >> "$tmp_file"
                echo "openace ALL=(root) NOPASSWD: ${wrapper_path} *" >> "$tmp_file"
            fi
        fi
    done

    cat "$tmp_file"
    rm -f "$tmp_file"
}

# Validate sudoers syntax
validate_sudoers() {
    local file="$1"

    if ! visudo -c -f "$file" >/dev/null 2>&1; then
        echo "ERROR: sudoers syntax validation failed" >&2
        visudo -c -f "$file" 2>&1 || true
        return 1
    fi

    echo "Sudoers syntax validation passed"
    return 0
}

# Main upgrade function
upgrade_sudoers() {
    local dry_run="${1:-false}"
    local force="${2:-false}"

    echo "========================================="
    echo "Sudoers Security Upgrade (Issue #2181)"
    echo "========================================="
    echo ""

    # Check if sudoers exists
    if ! check_sudoers_exists; then
        echo "No sudoers file to upgrade"
        return 0
    fi

    # Check if upgrade is needed
    if [ "$force" != "true" ]; then
        if ! check_upgrade_needed; then
            echo "Sudoers already upgraded, no changes needed"
            return 0
        fi
    fi

    echo "Upgrade needed: yes"
    echo ""

    # Show what will be changed
    echo "Changes to be made:"
    echo "  1. Remove deprecated rules (rm *, cat *, chown *, useradd *, OPENACE_CLI)"
    echo "  2. Remove sensitive variables from env_keep"
    echo "  3. Add security wrapper rules (openace-rm, etc.)"
    echo ""

    if [ "$dry_run" = "true" ]; then
        echo "Dry run mode - showing changes without applying"
        echo ""
        echo "Cleaned sudoers content:"
        remove_deprecated_rules | add_wrapper_rules
        return 0
    fi

    # Backup original
    if ! backup_sudoers; then
        return 5
    fi

    # Apply changes
    local tmp_sudoers=$(mktemp)

    # Generate new sudoers content
    remove_deprecated_rules | add_wrapper_rules > "$tmp_sudoers"

    # Validate new content
    if ! validate_sudoers "$tmp_sudoers"; then
        echo "ERROR: Generated sudoers is invalid, aborting"
        rm -f "$tmp_sudoers"
        return 4
    fi

    # Apply new sudoers
    chmod 440 "$tmp_sudoers"
    if ! mv "$tmp_sudoers" "$SUDOERS_FILE"; then
        echo "ERROR: Failed to apply new sudoers file" >&2
        rm -f "$tmp_sudoers"
        return 3
    fi

    echo ""
    echo "========================================="
    echo "Sudoers upgrade completed successfully"
    echo "========================================="
    log_audit "Sudoers upgrade completed successfully"

    return 0
}

# Parse arguments
DRY_RUN=false
FORCE=false
MODE="upgrade"

while [ "$#" -gt 0 ]; do
    case "$1" in
        --dry-run)
            DRY_RUN=true
            ;;
        --force)
            FORCE=true
            ;;
        --check)
            MODE="check"
            ;;
        -h|--help)
            usage
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage
            ;;
    esac
    shift
done

# Execute
if [ "$MODE" = "check" ]; then
    if check_sudoers_exists && check_upgrade_needed; then
        echo "STATUS: Upgrade needed"
        exit 0
    else
        echo "STATUS: No upgrade needed"
        exit 1
    fi
else
    upgrade_sudoers "$DRY_RUN" "$FORCE"
    exit $?
fi