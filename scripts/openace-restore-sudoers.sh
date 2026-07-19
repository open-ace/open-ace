#!/bin/bash
# openace-restore-sudoers — Rollback script for sudoers configuration.
#
# This script backs up the current sudoers configuration and restores
# the previous version. Used for emergency rollback when sudoers
# changes cause issues.
#
# Usage: openace-restore-sudoers [--latest | --file <backup_file>]
#
# Options:
#   --latest          Restore the most recent backup (default)
#   --file <path>     Restore from a specific backup file
#   --list            List available backups
#   --dry-run         Show what would be restored without making changes
#
# Exit codes:
#   0 - Success
#   1 - Invalid arguments
#   2 - No backups found
#   3 - Restore failed

set -euo pipefail

# Constants
SUDOERS_FILE="/etc/sudoers.d/open-ace-webui"
BACKUP_DIR="/app/logs"
AUDIT_LOG="/app/logs/sudoers-audit.log"

log_audit() {
    local msg="$1"
    local timestamp
    timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    local log_entry="[$timestamp] [openace-restore-sudoers] $msg"

    # Try to write to audit log, fallback to stderr
    if ! echo "$log_entry" >> "$AUDIT_LOG" 2>/dev/null; then
        echo "[AUDIT_FALLBACK] $log_entry" >&2
    fi
}

usage() {
    echo "Usage: $0 [--latest | --file <backup_file> | --list | --dry-run]" >&2
    echo "" >&2
    echo "Options:" >&2
    echo "  --latest          Restore the most recent backup (default)" >&2
    echo "  --file <path>     Restore from a specific backup file" >&2
    echo "  --list            List available backups" >&2
    echo "  --dry-run         Show what would be restored without making changes" >&2
    exit 1
}

list_backups() {
    echo "Available sudoers backups in $BACKUP_DIR:"
    echo ""
    local found=0
    for f in $(ls -t "$BACKUP_DIR"/sudoers-*.bak 2>/dev/null || true); do
        if [ -f "$f" ]; then
            found=1
            local size
            size=$(stat -c%s "$f" 2>/dev/null || echo "unknown")
            local mtime
            mtime=$(stat -c%y "$f" 2>/dev/null | cut -d'.' -f1 || echo "unknown")
            echo "  $f (size: $size bytes, modified: $mtime)"
        fi
    done

    if [ "$found" -eq 0 ]; then
        echo "  No backups found."
    fi
}

# Parse arguments
MODE="latest"
DRY_RUN=false
RESTORE_FILE=""

while [ "$#" -gt 0 ]; do
    case "$1" in
        --latest)
            MODE="latest"
            shift
            ;;
        --file)
            MODE="file"
            shift
            if [ "$#" -eq 0 ]; then
                echo "ERROR: --file requires a path argument" >&2
                usage
            fi
            RESTORE_FILE="$1"
            shift
            ;;
        --list)
            list_backups
            exit 0
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        -h|--help)
            usage
            ;;
        *)
            echo "ERROR: Unknown option: $1" >&2
            usage
            ;;
    esac
done

# Check if running as root
if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: This script must be run as root" >&2
    exit 1
fi

# Find the backup file to restore
if [ "$MODE" = "file" ]; then
    if [ ! -f "$RESTORE_FILE" ]; then
        echo "ERROR: Backup file not found: $RESTORE_FILE" >&2
        exit 2
    fi
else
    # Find the most recent backup
    RESTORE_FILE=$(ls -t "$BACKUP_DIR"/sudoers-*.bak 2>/dev/null | head -n1 || true)
    if [ -z "$RESTORE_FILE" ]; then
        echo "ERROR: No backup files found in $BACKUP_DIR" >&2
        exit 2
    fi
fi

echo "Restore source: $RESTORE_FILE"

# Validate the backup file syntax
if ! visudo -c -f "$RESTORE_FILE" &>/dev/null; then
    echo "ERROR: Backup file has invalid sudoers syntax: $RESTORE_FILE" >&2
    exit 3
fi

if [ "$DRY_RUN" = true ]; then
    echo "Dry run mode - would restore:"
    echo "  Source: $RESTORE_FILE"
    echo "  Target: $SUDOERS_FILE"
    echo ""
    echo "Content preview:"
    echo "---"
    head -20 "$RESTORE_FILE"
    echo "---"
    exit 0
fi

# Backup current sudoers before restore
if [ -f "$SUDOERS_FILE" ]; then
    CURRENT_BACKUP="$BACKUP_DIR/sudoers-pre-restore-$(date +%Y%m%d%H%M%S).bak"
    cp "$SUDOERS_FILE" "$CURRENT_BACKUP"
    echo "Current sudoers backed up to: $CURRENT_BACKUP"
fi

# Restore the backup
cp "$RESTORE_FILE" "$SUDOERS_FILE"
chmod 440 "$SUDOERS_FILE"

# Validate restored sudoers
if ! visudo -c &>/dev/null; then
    echo "ERROR: Restored sudoers has invalid syntax, rolling back..." >&2
    if [ -f "$CURRENT_BACKUP" ]; then
        cp "$CURRENT_BACKUP" "$SUDOERS_FILE"
        chmod 440 "$SUDOERS_FILE"
    fi
    log_audit "caller=$(whoami) restore_from=$RESTORE_FILE result=fail_syntax"
    exit 3
fi

log_audit "caller=$(whoami) restore_from=$RESTORE_FILE result=success"
echo "Successfully restored sudoers from: $RESTORE_FILE"
exit 0
