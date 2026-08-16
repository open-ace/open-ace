#!/bin/bash
# CI-parity push gate: run the exact CI lint first, fold formatter autofixes
# into the commit being pushed, then push.
#
# Why this exists: on 2026-08-15 the same failure hit #2712, #2718 and #2719
# (#2205 before them) — black/isort autofixes landed in the worktree but not
# in the pushed commit, CI lint went red, and a local `pre-commit run` still
# passed because it checks the worktree, not the commit. Running the CI
# command at push time closes that gap.
#
# Usage:
#   scripts/push.sh [git-push args]    e.g. scripts/push.sh origin HEAD
#
# Semantics:
#   - Runs `SKIP=bandit-check,no-commit-to-branch pre-commit run --all-files`
#     — the exact CI lint command, so tool versions match the pinned config.
#   - Auto-fixable problems are fixed, then ALL tracked worktree modifications
#     are folded into HEAD: amended if HEAD is not yet on the remote, otherwise
#     a "style: apply pre-commit autofixes" commit. Commit or stash unrelated
#     work-in-progress before using this script.
#   - Failures autofix cannot resolve (mypy, pydocstyle, ...) abort the push;
#     any applied fixes are kept in HEAD.
#   - Escape hatch: ACE_PUSH_SKIP_CHECK=1 scripts/push.sh ...

set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

if [ "${ACE_PUSH_SKIP_CHECK:-0}" = "1" ]; then
    echo "scripts/push.sh: ACE_PUSH_SKIP_CHECK=1 — skipping checks"
    exec git push "$@"
fi

if ! command -v pre-commit >/dev/null 2>&1; then
    echo "scripts/push.sh: pre-commit not found on PATH." >&2
    echo "Install it (pip install pre-commit), or bypass with ACE_PUSH_SKIP_CHECK=1." >&2
    exit 1
fi

echo "==> Running CI-parity lint (pre-commit run --all-files)"
set +e
SKIP=bandit-check,no-commit-to-branch pre-commit run --all-files
LINT_RC=$?
set -e

if git diff --quiet; then
    CHANGED=0
else
    CHANGED=1
fi

if [ "$CHANGED" -eq 1 ]; then
    git add -u
    HEAD_SHA="$(git rev-parse HEAD)"
    UPSTREAM_SHA="$(git rev-parse '@{u}' 2>/dev/null || true)"
    if [ -n "$UPSTREAM_SHA" ] && [ "$HEAD_SHA" = "$UPSTREAM_SHA" ]; then
        # HEAD is already on the remote; appending is the only non-force option.
        git commit -m "style: apply pre-commit autofixes"
    else
        git commit --amend --no-edit
    fi
    echo "scripts/push.sh: folded autofixes into $(git rev-parse --short HEAD)"
fi

if [ "$LINT_RC" -ne 0 ]; then
    echo >&2
    echo "scripts/push.sh: pre-commit exit $LINT_RC — failures above are not autofixable." >&2
    echo "scripts/push.sh: applied fixes were kept in $(git rev-parse --short HEAD);" >&2
    echo "scripts/push.sh: resolve the failures, then re-run this script to push." >&2
    exit "$LINT_RC"
fi

exec git push "$@"
