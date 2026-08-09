"""Tests for system_account fallback logic in autonomous API (Issue #1530).

This module tests the system_account resolution mechanism that backs sudo
execution in multi-user workspace mode.

The runtime fallback (exercised at the route layer):
1. First tries workflow.system_account (persisted at creation)
2. If empty, queries user.system_account via UserRepository
3. Passes system_account to GitHubOps for sudo -u execution

The route-layer fallback needs a Flask/DB harness, so these unit tests instead
pin the underlying pure functions that implement the per-user decision:
``_get_effective_system_account`` (same-user short-circuit, NoNewPrivileges
safety) and ``GitHubOps._needs_sudo`` (cross-user detection). These are the
semantics the route fallback relies on, and they are unit-testable without
the Flask client.
"""

import os
import pwd
from unittest.mock import patch

from app.modules.workspace.autonomous.github_ops import GitHubOps
from app.routes.autonomous import _get_effective_system_account


class TestGetEffectiveSystemAccount:
    """Test _get_effective_system_account same-user short-circuit."""

    def test_empty_system_account_returns_none(self):
        """An empty system_account resolves to None (no sudo target)."""
        assert _get_effective_system_account("") is None

    def test_none_system_account_returns_none(self):
        """A None system_account resolves to None (no sudo target)."""
        assert _get_effective_system_account(None) is None

    def test_current_user_matches_returns_none(self):
        """When the process already runs as the target user, no sudo is needed."""
        current_user = pwd.getpwuid(os.getuid()).pw_name
        assert _get_effective_system_account(current_user) is None

    def test_different_user_returns_system_account(self):
        """A different target user is returned unchanged for sudo execution."""
        assert _get_effective_system_account("__not_a_user__") == "__not_a_user__"


class TestGitHubOpsSystemAccount:
    """Test GitHubOps stores and decides on system_account."""

    def test_init_stores_system_account(self):
        """GitHubOps.__init__ persists system_account for later sudo decisions."""
        ops = GitHubOps("/tmp/repo", system_account="someuser")
        assert ops.system_account == "someuser"

    def test_init_defaults_system_account_to_none(self):
        """GitHubOps.__init__ defaults system_account to None when omitted."""
        ops = GitHubOps("/tmp/repo")
        assert ops.system_account is None

    def test_needs_sudo_false_without_system_account(self):
        """Without a system_account, _needs_sudo is False (no sudo wrapper)."""
        ops = GitHubOps("/tmp/repo")
        assert ops._needs_sudo() is False

    def test_needs_sudo_false_for_same_user(self):
        """When the service already runs as system_account, sudo is skipped."""
        current_user = pwd.getpwuid(os.getuid()).pw_name
        ops = GitHubOps("/tmp/repo", system_account=current_user)
        assert ops._needs_sudo() is False

    def test_needs_sudo_true_for_different_user(self):
        """A different system_account requires a sudo -u wrapper."""
        ops = GitHubOps("/tmp/repo", system_account="__other_user__")
        assert ops._needs_sudo() is True
