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

# Issue #3410: never write THROUGH a symlink. The caller cannot do this check
# reliably — in this deployment shape the web process is a service account that
# cannot traverse the target user's 0700 home, so its os.path.islink() probe
# returns False on EACCES. This wrapper is the enforcement point.
if [ -L "$TARGET_PATH" ]; then
    echo "ERROR: Target '$TARGET_PATH' is a symbolic link; refusing to write through it" >&2
    log_audit "caller=$(whoami) target_user=${TARGET_USER} path=${TARGET_PATH} result=reject_symlink"
    exit 5
fi

# Issue #3410: `mv src dir` moves src INTO dir and exits 0, so a directory
# named like the upload would silently swallow the temp file while the API
# answered 200 with the directory's path. `tee` used to fail here ("Is a
# directory"); keep that a refusal, with its own code. This test MUST come
# after the -L check: [ -d ] follows symlinks, so a symlink-to-a-directory has
# to be reported as a symlink, not as a directory.
if [ -d "$TARGET_PATH" ]; then
    echo "ERROR: Target '$TARGET_PATH' is a directory" >&2
    log_audit "caller=$(whoami) target_user=${TARGET_USER} path=${TARGET_PATH} result=reject_directory"
    exit 6
fi

# Resolve the PARENT directory only and re-append the basename: the target
# itself is known not to be a symlink (checked above), and resolving it would
# reintroduce the #3410 escape for any future caller.
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
# The trap removes the temp file on every error path and on SIGTERM; a leaked
# dotfile would be invisible to /fs browse and search and effectively
# undeletable by the user. (SIGKILL is untrappable — the caller sends SIGTERM
# first for exactly this reason.) The runuser sub-calls read /dev/null so
# nothing in the PAM chain can consume bytes of the upload body.
TMP_PATH=$(runuser -u "$TARGET_USER" -- mktemp "${RESOLVED_PARENT:-$(dirname "$RESOLVED_PATH")}/.openace-write-as.XXXXXX" </dev/null) || {
    log_audit "caller=$(whoami) target_user=${TARGET_USER} path=${RESOLVED_PATH} result=fail_mktemp"
    exit 4
}
trap 'runuser -u "$TARGET_USER" -- rm -f "$TMP_PATH" </dev/null 2>/dev/null || true' EXIT INT TERM

if runuser -u "$TARGET_USER" -- tee "$TMP_PATH" > /dev/null \
   && runuser -u "$TARGET_USER" -- mv -fT "$TMP_PATH" "$RESOLVED_PATH" </dev/null; then
    log_audit "caller=$(whoami) target_user=${TARGET_USER} path=${RESOLVED_PATH} result=success"
    exit 0
else
    EXIT_CODE=$?
    log_audit "caller=$(whoami) target_user=${TARGET_USER} path=${RESOLVED_PATH} result=fail code=${EXIT_CODE}"
    exit 4
fi
