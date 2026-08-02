#!/bin/bash
# ==============================================================================
# CI Fix Script: Remove .worktrees submodule entries
# ==============================================================================
#
# PROBLEM:
# The .worktrees/ directory was erroneously committed as Git submodules
# (mode 160000). This causes CI to fail with:
#   "fatal: No url found for submodule path '.worktrees/...' in .gitmodules"
#
# WHY submodules: false DOESN'T HELP:
# - actions/checkout's submodules parameter only controls whether to run
#   git submodule init/update
# - It does NOT prevent Git from checking submodule integrity during checkout
# - Git still expects .gitmodules to exist when submodule entries are present
#
# REQUIRED ACTION:
# Execute the commands below to remove the erroneous submodule entries
# ==============================================================================

set -e

echo "=== Removing .worktrees submodule entries from Git index ==="

# Step 1: Remove from index (keep in working tree)
echo "Step 1: Removing from Git index..."
git rm --cached -r .worktrees/ || {
    echo "ERROR: Failed to remove .worktrees from index"
    exit 1
}

# Step 2: Verify removal
echo "Step 2: Verifying removal..."
if git ls-files --stage | grep -q '\.worktrees'; then
    echo "ERROR: .worktrees entries still in index"
    exit 1
else
    echo "SUCCESS: .worktrees entries removed from index"
fi

# Step 3: Check that .worktrees is in .gitignore
echo "Step 3: Checking .gitignore..."
if grep -q '\.worktrees' .gitignore; then
    echo "SUCCESS: .worktrees is in .gitignore"
else
    echo "WARNING: .worktrees not in .gitignore, adding..."
    echo ".worktrees/" >> .gitignore
fi

# Step 4: Commit the fix
echo "Step 4: Committing..."
git commit -m "fix(ci): remove erroneously added .worktrees submodule entries

The .worktrees/ directory contains local git worktrees for development
and should never be committed as submodules.

This fixes the schema-sync CI failure:
'fatal: No url found for submodule path in .gitmodules'

Root cause: commit 4b9dfeeb added .worktrees as submodules
" || {
    echo "Note: Commit may have nothing to commit if already fixed"
}

echo "=== Fix completed successfully ==="