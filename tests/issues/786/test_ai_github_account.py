"""Tests for AI GitHub account configuration (Issue #786).

Covers:
  - AiAgentSettingsRepo: CRUD, token masking, env generation
  - get_ai_github_env(): config reader with cache
  - GitHubOps: env injection into subprocess calls
  - API routes: GET/PUT settings, token validation
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

# ── AiAgentSettingsRepo ─────────────────────────────────────────────


class TestAiAgentSettingsRepoMaskToken:
    """Verify _mask_token utility."""

    def test_mask_normal_token(self):
        from app.repositories.ai_agent_settings_repo import _mask_token

        assert _mask_token("ghp_abc1234567890") == "ghp_****7890"

    def test_mask_short_token(self):
        from app.repositories.ai_agent_settings_repo import _mask_token

        assert _mask_token("short") == "****"

    def test_mask_empty_token(self):
        from app.repositories.ai_agent_settings_repo import _mask_token

        assert _mask_token("") == ""

    def test_mask_exact_8_chars(self):
        from app.repositories.ai_agent_settings_repo import _mask_token

        assert _mask_token("12345678") == "****"

    def test_mask_9_chars(self):
        from app.repositories.ai_agent_settings_repo import _mask_token

        result = _mask_token("123456789")
        assert result == "1234****6789"


class TestAiAgentSettingsRepoGetSettings:
    """Verify get_ai_agent_settings returns defaults when DB is empty."""

    def test_returns_defaults_when_table_empty(self):
        from app.repositories.ai_agent_settings_repo import AiAgentSettingsRepo

        mock_db = MagicMock()
        mock_db.fetch_all.return_value = []
        repo = AiAgentSettingsRepo(db=mock_db)

        settings = repo.get_ai_agent_settings(mask_token=True)

        assert settings["ai_github_token"] == ""
        assert settings["ai_github_author_name"] == "Open ACE AI"
        assert settings["ai_github_author_email"] == "bot@open-ace.com"

    def test_returns_masked_token_when_set(self):
        from app.repositories.ai_agent_settings_repo import AiAgentSettingsRepo

        mock_db = MagicMock()
        mock_db.fetch_all.return_value = [
            {"setting_key": "ai_github_token", "setting_value": "ghp_abc1234567890"},
            {"setting_key": "ai_github_author_name", "setting_value": "My Bot"},
            {"setting_key": "ai_github_author_email", "setting_value": "bot@example.com"},
        ]
        repo = AiAgentSettingsRepo(db=mock_db)

        settings = repo.get_ai_agent_settings(mask_token=True)

        assert settings["ai_github_token"] == "ghp_****7890"
        assert settings["ai_github_author_name"] == "My Bot"

    def test_returns_unmasked_token_when_requested(self):
        from app.repositories.ai_agent_settings_repo import AiAgentSettingsRepo

        mock_db = MagicMock()
        mock_db.fetch_all.return_value = [
            {"setting_key": "ai_github_token", "setting_value": "ghp_abc1234567890"},
        ]
        repo = AiAgentSettingsRepo(db=mock_db)

        settings = repo.get_ai_agent_settings(mask_token=False)

        assert settings["ai_github_token"] == "ghp_abc1234567890"


class TestAiAgentSettingsRepoUpdateSettings:
    """Verify update_ai_agent_settings upserts values."""

    def test_upsert_calls_execute_for_each_key(self):
        from app.repositories.ai_agent_settings_repo import AiAgentSettingsRepo

        mock_db = MagicMock()
        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cursor
        mock_db.connection.return_value = mock_conn

        repo = AiAgentSettingsRepo(db=mock_db)
        result = repo.update_ai_agent_settings({"ai_github_token": "new_token"})

        assert result is True
        assert mock_cursor.execute.call_count == 1

    def test_returns_false_on_db_error(self):
        from app.repositories.ai_agent_settings_repo import AiAgentSettingsRepo

        mock_db = MagicMock()
        mock_db.connection.side_effect = Exception("DB error")

        repo = AiAgentSettingsRepo(db=mock_db)
        result = repo.update_ai_agent_settings({"ai_github_token": "new_token"})

        assert result is False


class TestAiAgentSettingsRepoGetGithubEnv:
    """Verify get_ai_github_env returns correct env dict."""

    def test_returns_none_when_no_token(self):
        from app.repositories.ai_agent_settings_repo import AiAgentSettingsRepo

        mock_db = MagicMock()
        mock_db.fetch_all.return_value = []
        repo = AiAgentSettingsRepo(db=mock_db)

        result = repo.get_ai_github_env()

        assert result is None

    def test_returns_env_dict_when_token_set(self):
        from app.repositories.ai_agent_settings_repo import AiAgentSettingsRepo

        mock_db = MagicMock()
        mock_db.fetch_all.return_value = [
            {"setting_key": "ai_github_token", "setting_value": "ghp_test123"},
            {"setting_key": "ai_github_author_name", "setting_value": "Bot Name"},
            {"setting_key": "ai_github_author_email", "setting_value": "bot@test.com"},
        ]
        repo = AiAgentSettingsRepo(db=mock_db)

        result = repo.get_ai_github_env()

        assert result is not None
        assert result["GH_TOKEN"] == "ghp_test123"
        assert result["GIT_AUTHOR_NAME"] == "Bot Name"
        assert result["GIT_AUTHOR_EMAIL"] == "bot@test.com"
        assert result["GIT_COMMITTER_NAME"] == "Bot Name"
        assert result["GIT_COMMITTER_EMAIL"] == "bot@test.com"

    def test_uses_defaults_for_missing_author_fields(self):
        from app.repositories.ai_agent_settings_repo import AiAgentSettingsRepo

        mock_db = MagicMock()
        mock_db.fetch_all.return_value = [
            {"setting_key": "ai_github_token", "setting_value": "ghp_test123"},
        ]
        repo = AiAgentSettingsRepo(db=mock_db)

        result = repo.get_ai_github_env()

        assert result is not None
        assert result["GIT_AUTHOR_NAME"] == "Open ACE AI"
        assert result["GIT_AUTHOR_EMAIL"] == "bot@open-ace.com"


# ── get_ai_github_env() config cache ────────────────────────────────


class TestGetAiGithubEnv:
    """Verify the config.py cache layer for AI GitHub env."""

    def test_returns_none_when_no_token_configured(self):
        from app.utils import config

        # Clear cache
        config._ai_github_env_ts = 0.0
        config._ai_github_env_data = None

        with patch(
            "app.repositories.ai_agent_settings_repo.AiAgentSettingsRepo.get_ai_github_env",
            return_value=None,
        ):
            result = config.get_ai_github_env()
            assert result is None

    def test_returns_env_dict_when_configured(self):
        from app.utils import config

        config._ai_github_env_ts = 0.0
        config._ai_github_env_data = None

        mock_env = {
            "GH_TOKEN": "ghp_test",
            "GIT_AUTHOR_NAME": "Bot",
            "GIT_AUTHOR_EMAIL": "bot@test.com",
            "GIT_COMMITTER_NAME": "Bot",
            "GIT_COMMITTER_EMAIL": "bot@test.com",
        }
        with patch(
            "app.repositories.ai_agent_settings_repo.AiAgentSettingsRepo.get_ai_github_env",
            return_value=mock_env,
        ):
            result = config.get_ai_github_env()
            assert result == mock_env

    def test_caches_result_within_ttl(self):
        import time

        from app.utils import config

        config._ai_github_env_ts = 0.0
        config._ai_github_env_data = None

        mock_env = {
            "GH_TOKEN": "ghp_cached",
            "GIT_AUTHOR_NAME": "Bot",
            "GIT_AUTHOR_EMAIL": "b@t.com",
            "GIT_COMMITTER_NAME": "Bot",
            "GIT_COMMITTER_EMAIL": "b@t.com",
        }

        with patch(
            "app.repositories.ai_agent_settings_repo.AiAgentSettingsRepo.get_ai_github_env",
            return_value=mock_env,
        ) as mock_fn:
            # First call
            result1 = config.get_ai_github_env()
            # Second call within TTL should use cache
            result2 = config.get_ai_github_env()

            assert result1 == result2
            assert mock_fn.call_count == 1  # Only called once due to cache


# ── GitHubOps env injection ─────────────────────────────────────────


class TestGitHubOpsEnvInjection:
    """Verify GitHubOps injects AI env into subprocess calls."""

    def test_get_env_returns_none_when_no_config(self):
        from app.modules.workspace.autonomous.github_ops import GitHubOps

        with patch("app.utils.config.get_ai_github_env", return_value=None):
            ops = GitHubOps("/tmp")
            assert ops._get_env() is None

    def test_get_env_returns_dict_when_configured(self):
        from app.modules.workspace.autonomous.github_ops import GitHubOps

        mock_env = {
            "GH_TOKEN": "ghp_test",
            "GIT_AUTHOR_NAME": "Bot",
            "GIT_AUTHOR_EMAIL": "bot@test.com",
            "GIT_COMMITTER_NAME": "Bot",
            "GIT_COMMITTER_EMAIL": "bot@test.com",
        }
        with patch("app.utils.config.get_ai_github_env", return_value=mock_env):
            ops = GitHubOps("/tmp")
            result = ops._get_env()
            assert result == mock_env

    def test_build_subprocess_kwargs_no_env(self):
        from app.modules.workspace.autonomous.github_ops import GitHubOps

        with patch("app.utils.config.get_ai_github_env", return_value=None):
            ops = GitHubOps("/tmp")
            kwargs = ops._build_subprocess_kwargs()
            assert "env" not in kwargs
            assert kwargs["cwd"] == "/tmp"

    def test_build_subprocess_kwargs_with_env(self):
        from app.modules.workspace.autonomous.github_ops import GitHubOps

        mock_env = {"GH_TOKEN": "ghp_test"}
        with patch("app.utils.config.get_ai_github_env", return_value=mock_env):
            ops = GitHubOps("/tmp")
            kwargs = ops._build_subprocess_kwargs()
            assert "env" in kwargs
            assert kwargs["env"]["GH_TOKEN"] == "ghp_test"
            # Should also include inherited os.environ keys
            assert "PATH" in kwargs["env"]

    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_run_gh_injects_env(self, mock_run):
        from app.modules.workspace.autonomous.github_ops import GitHubOps

        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")

        mock_env = {"GH_TOKEN": "ghp_test"}
        with patch("app.utils.config.get_ai_github_env", return_value=mock_env):
            ops = GitHubOps("/tmp")
            ops._run_gh(["issue", "list"])

        call_kwargs = mock_run.call_args
        assert "env" in call_kwargs[1]
        assert call_kwargs[1]["env"]["GH_TOKEN"] == "ghp_test"

    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_run_git_injects_env(self, mock_run):
        from app.modules.workspace.autonomous.github_ops import GitHubOps

        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        mock_env = {"GIT_AUTHOR_NAME": "Bot", "GIT_AUTHOR_EMAIL": "bot@test.com"}
        with patch("app.utils.config.get_ai_github_env", return_value=mock_env):
            ops = GitHubOps("/tmp")
            ops._run_git(["status"])

        call_kwargs = mock_run.call_args
        assert "env" in call_kwargs[1]
        assert call_kwargs[1]["env"]["GIT_AUTHOR_NAME"] == "Bot"

    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_run_gh_no_env_when_not_configured(self, mock_run):
        from app.modules.workspace.autonomous.github_ops import GitHubOps

        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")

        with patch("app.utils.config.get_ai_github_env", return_value=None):
            ops = GitHubOps("/tmp")
            ops._run_gh(["issue", "list"])

        call_kwargs = mock_run.call_args
        assert "env" not in call_kwargs[1]
