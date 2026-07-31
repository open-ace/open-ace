#!/bin/bash
# Script to remove .worktrees/ submodules from git index
# This script should be run by the orchestrator

set -e

echo "Removing .worktrees/ submodules from git index..."

# Remove each worktree entry from the git index
git rm --cached -r .worktrees/082fbaf2-d1b4-4075-917f-1d628c44b357 2>/dev/null || true
git rm --cached -r .worktrees/63f63269-4e12-42d6-86f3-c3c41b7eea42 2>/dev/null || true
git rm --cached -r .worktrees/f566fa56-38a3-4868-aaa2-f79e9655b2c4 2>/dev/null || true

echo "Done. Please commit and push these changes."