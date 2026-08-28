#!/bin/sh
# Sandbox container entrypoint (Issue #2023).
#
# Deliberately does NOT synthesise the git repository. The lifecycle order is
# create -> upload_workspace -> exec, and this script runs during create — so a
# `git init && git add -A && git commit` here would execute against an empty
# /workspace, commit nothing, and leave the snapshot landing afterwards as
# entirely untracked files: `git diff` empty, `git status` showing the whole
# tree as new, and pre-commit staging against a HEAD that does not exist.
#
# The provider runs the synthesis as an explicit foreground command after the
# upload instead (see OpenSandboxProvider.upload_workspace). This script only
# prepares the writable tree the agent needs and then idles, letting execd own
# the process lifecycle.
set -eu

HOME_DIR="${HOME:-/workspace/home}"
mkdir -p "$HOME_DIR" "$HOME_DIR/tmp" "$HOME_DIR/.cache" "$HOME_DIR/.config" \
         "$HOME_DIR/.local/share" /workspace

# Idle as PID 1. execd drives every command and PTY session from here.
exec tail -f /dev/null
