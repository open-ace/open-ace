#!/usr/bin/env bash
# openace-nm-shim — populate a worktree-local frontend node_modules (#23/#2694).
#
# Invoked by git_workspace._ensure_frontend_node_modules_shim as:
#   sudo -u <owner> /usr/local/bin/openace-nm-shim <wt_frontend> <main_node_modules>
#
# The historical 'sudo -u <owner> bash -c <script>' shape is rejected by
# sudoers BY DESIGN (#2650: multi-step bash-as-owner is a root-RCE surface),
# so this root-owned wrapper is the whitelisted single command (NM_SHIM_SAFE,
# (ALL) runas). It runs AS the target owner — it can only touch what that
# owner can — and strictly validates its two absolute-path arguments.
#
# Semantics MUST stay in sync with _build_node_modules_shim_script in
# app/modules/workspace/autonomous/git_workspace.py (tests lock both):
# symlink every entry from the main clone's node_modules, keep the writable
# vite/vitest cache dirs REAL, atomic tmp-dir + mv, idempotent.

set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "usage: $0 <wt_frontend> <main_node_modules>" >&2
  exit 64
fi
case "$1" in
  /*) ;;
  *) echo "refusing non-absolute wt_frontend: $1" >&2; exit 64 ;;
esac
case "$2" in
  /*) ;;
  *) echo "refusing non-absolute main_node_modules: $2" >&2; exit 64 ;;
esac

WT_FE=$1
MAIN_NM=$2
WT_NM="$WT_FE/node_modules"

# idempotent: leave an already-populated node_modules untouched
if [ -d "$WT_NM" ] && [ -n "$(ls -A "$WT_NM" 2>/dev/null || true)" ]; then
  exit 0
fi
# no main node_modules to reuse → clean no-op
if [ ! -d "$MAIN_NM" ]; then
  exit 0
fi
TMP="$WT_FE/.nm.shim.tmp.$$"
rm -rf "$TMP"
mkdir -p "$TMP"
trap 'rm -rf "$TMP"' EXIT
for entry in "$MAIN_NM"/.* "$MAIN_NM"/*; do
  [ -e "$entry" ] || [ -L "$entry" ] || continue
  name=$(basename "$entry")
  [ "$name" = "." ] && continue
  [ "$name" = ".." ] && continue
  case "$name" in
    .cache|.vite|.vite-temp|.vitest) ;;  # cache dirs are made REAL below
    *) ln -sfn "$entry" "$TMP/$name" ;;
  esac
done
# Ensure ALL cache dirs exist as REAL worktree dirs (agent-writable via the
# per-worktree ACL grant) — symlinking them to the owner's copies is the
# EACCES bug this shim fixes (#23).
for c in .cache .vite .vite-temp .vitest; do mkdir -p "$TMP/$c"; done
rm -rf "$WT_NM"
mv "$TMP" "$WT_NM"
