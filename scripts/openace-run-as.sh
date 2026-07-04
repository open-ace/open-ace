#!/bin/bash
# openace-run-as — cross-user agent launcher for AI autonomous development.
#
# Problem (Issue #1395): the open-ace service runs as the `openace` user, but
# autonomous-development workflows drive a repo owned by another user
# (system_account), often under a 0700 home directory. Agent CLIs
# (claude-code/qwen-code/openclaw) infer the project root from cwd and have
# no --cwd/--project flag, so they MUST be launched with cwd = project_path.
# But subprocess.Popen(cwd=...) chdir's as the service user and hits
# [Errno 13] Permission denied under a private home.
#
# This wrapper closes that gap: invoked as root (via a sudoers rule that
# targets ONLY this script), it chdir's into the project dir as root (which
# can traverse any directory), then drops to the target user via `runuser`
# before exec'ing the CLI. The CLI thus runs with the repo owner's identity,
# in the project cwd, with inherited env (API keys arrive via sudo env_keep).
#
# Usage: openace-run-as <user> <dir> <cmd> [args...]
#
# Exit codes: 64 = usage error; 65 = chdir failed; otherwise passes through.
#
# Security: sudoers authorizes `openace -> root: /usr/local/bin/openace-run-as *`
# ONLY. runuser (not sudo) performs the user drop, so no further sudoers rules
# are needed for the CLI itself. The script is single-purpose and takes no
# shell; the command and its args are exec'd verbatim (no eval).

set -euo pipefail

if [ "$#" -lt 3 ]; then
    echo "Usage: $0 <user> <dir> <cmd> [args...]" >&2
    exit 64
fi

target_user="$1"
project_dir="$2"
shift 2

# Root can traverse any directory; chdir here so the CLI inherits the project
# root as its cwd without the service user ever needing access to the path.
cd "$project_dir" || {
    echo "openace-run-as: cannot chdir to '$project_dir'" >&2
    exit 65
}

# Drop privileges to the repo owner and exec the CLI. runuser inherits the
# caller's environment (API keys preserved through sudo env_keep), so no -E
# or explicit env passthrough is required.
exec runuser -u "$target_user" -- "$@"
