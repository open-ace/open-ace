#!/bin/bash
# CI-parity push gate for interactive sessions: run the CI lint scoped to the
# branch's changed files, fold formatter autofixes into the commit being
# pushed, then push.
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
#   - Lints ONLY the branch delta (files changed vs the upstream, falling
#     back to origin/main). `--all-files` would reformat untouched files
#     whenever the local toolchain drifts from the pinned config — the
#     scope-exceeded cascade the orchestrator's CI-repair rule #4 forbids —
#     and costs a full hook-env install on every push from a fresh clone.
#   - Refuses to run over unrelated uncommitted work: pre-commit fixes are
#     folded into HEAD, and folding a dirty tree would silently ship
#     unreviewed work-in-progress with them. Commit or stash first.
#   - Fixing hooks are re-run after each fold until the tree settles
#     (a `ruff --fix` can leave code black wants to reformat; same doctrine
#     as orchestrator CI-repair rule #5: repeat the full command to exit 0).
#   - Fixes are amended into HEAD when HEAD is not on the remote; if HEAD is
#     already the remote tip, a "style: apply pre-commit autofixes" commit is
#     appended instead. A HEAD that already sits on any remote branch is
#     refused up front — published commits are never rewritten, and the
#     refusal happens before lint so nothing is left staged.
#   - Failures autofix cannot resolve (mypy, pydocstyle, ...) abort the push;
#     any applied fixes are kept in HEAD.
#   - pre-commit missing on PATH degrades to a loud warning plus the push:
#     this is the mandated push route, so it must always terminate in a push.
#   - Escape hatch: ACE_PUSH_SKIP_CHECK=1 scripts/push.sh ...

set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

if [ "${ACE_PUSH_SKIP_CHECK:-0}" = "1" ]; then
    echo "scripts/push.sh: ACE_PUSH_SKIP_CHECK=1 — skipping checks"
    exec git push "$@"
fi

BRANCH_NAME="$(git rev-parse --abbrev-ref HEAD)"
case "$BRANCH_NAME" in
    main|master)
        echo "scripts/push.sh: refusing to commit on $BRANCH_NAME — push a topic branch instead." >&2
        exit 1
        ;;
esac

if ! command -v pre-commit >/dev/null 2>&1; then
    echo >&2
    echo "scripts/push.sh: WARNING: pre-commit not found on PATH — pushing WITHOUT the CI-parity lint." >&2
    echo "scripts/push.sh: Install it (pip install pre-commit) so the next push is checked." >&2
    exec git push "$@"
fi

# Snapshot the tree BEFORE pre-commit runs. Any dirt at this point is
# unrelated work-in-progress; folding it would ship unreviewed work, so
# abort rather than fold (enforced, not just documented).
if ! git diff --quiet HEAD; then
    echo "scripts/push.sh: worktree has uncommitted changes (staged or unstaged)." >&2
    echo "scripts/push.sh: commit or stash them first — this script folds whatever it finds into the push." >&2
    exit 1
fi

BASE=""
if git rev-parse --quiet --verify '@{u}' >/dev/null 2>&1; then
    BASE='@{u}'
elif git rev-parse --quiet --verify origin/main >/dev/null 2>&1; then
    BASE='origin/main'
elif git rev-parse --quiet --verify origin/master >/dev/null 2>&1; then
    BASE='origin/master'
else
    echo "scripts/push.sh: no upstream and no origin/main to diff against —" >&2
    echo "scripts/push.sh: set one (git push -u origin '$BRANCH_NAME' or git fetch origin) and re-run." >&2
    exit 1
fi

TARGETS=()
while IFS= read -r f; do
    TARGETS+=("$f")
done < <(git diff --name-only "$BASE...HEAD" | sort -u)
if [ "${#TARGETS[@]}" -eq 0 ]; then
    echo "scripts/push.sh: branch has no changes vs $BASE — nothing to lint."
    exec git push "$@"
fi

# Never rewrite a published commit: HEAD already on some remote branch
# (pushed without -u, or landed there via another route) means the fold would
# have to amend it. Refusing HERE — before anything runs — keeps the worktree
# untouched; refusing after `git add -u` would leave the autofixes staged,
# and the dirty-tree guard would then block the re-run this message suggests.
# (A branch with no commits of its own never reaches this point: its empty
# delta exited above.)
HEAD_SHA="$(git rev-parse HEAD)"
REMOTE_CONTAINING="$(git branch -r --contains "$HEAD_SHA")"
if [ -n "$REMOTE_CONTAINING" ] && [ "$HEAD_SHA" != "$(git rev-parse --quiet --verify '@{u}' 2>/dev/null || true)" ]; then
    echo "scripts/push.sh: HEAD is already on: $(echo "$REMOTE_CONTAINING" | tr -d ' ' | paste -sd, -)" >&2
    echo "scripts/push.sh: refusing to amend a published commit —" >&2
    echo "scripts/push.sh: make your own commit on top (or git pull --rebase), then re-run." >&2
    exit 1
fi

fold_changes() {
    git add -u
    local HEAD_SHA UPSTREAM_SHA
    HEAD_SHA="$(git rev-parse HEAD)"
    UPSTREAM_SHA="$(git rev-parse --quiet --verify '@{u}' 2>/dev/null || true)"
    if [ -n "$UPSTREAM_SHA" ] && [ "$HEAD_SHA" = "$UPSTREAM_SHA" ]; then
        # HEAD is already the remote branch tip; appending is the only non-force option.
        git commit -m "style: apply pre-commit autofixes"
    else
        # HEAD exists only locally — the published-commit check above refused
        # everything already on a remote, and the first fold's amend keeps
        # HEAD off the remote by construction.
        git commit --amend --no-edit
    fi
    echo "scripts/push.sh: folded autofixes into $(git rev-parse --short HEAD)"
}

LINT_RC=0
FOLDED=0
SETTLED=0
for pass in 1 2 3; do
    echo "==> CI-parity lint pass $pass: pre-commit run --files (${#TARGETS[@]} file(s) vs $BASE)"
    set +e
    SKIP=bandit-check,no-commit-to-branch pre-commit run --files "${TARGETS[@]}"
    LINT_RC=$?
    set -e
    if git diff --quiet HEAD; then
        SETTLED=1
        break
    fi
    # A hook exiting 1 with modifications is the fix in progress, not a
    # verdict — fold and repeat until the tree stops changing.
    FOLDED=1
    fold_changes
done

if [ "$SETTLED" -ne 1 ]; then
    echo "scripts/push.sh: WARNING: hooks still modified files on pass 3 (fixer oscillation?);" >&2
    echo "scripts/push.sh: the folded commit may not be stable — review it before it reaches CI." >&2
fi

if [ "$LINT_RC" -ne 0 ]; then
    echo >&2
    echo "scripts/push.sh: pre-commit exit $LINT_RC on the settled tree — failures above are not autofixable." >&2
    if [ "$FOLDED" -eq 1 ]; then
        echo "scripts/push.sh: applied fixes were kept in $(git rev-parse --short HEAD);" >&2
    fi
    echo "scripts/push.sh: resolve the failures, then re-run this script to push." >&2
    exit "$LINT_RC"
fi

exec git push "$@"
