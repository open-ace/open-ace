"""Unit tests for InsightsService and InsightsReportRepository."""

import json
from unittest.mock import MagicMock, mock_open, patch

import pytest

from app.repositories.insights_repo import InsightsReportRepository
from app.services.insights_service import InsightsService


class TestInsightsServiceInit:
    """Test InsightsService initialization."""

    def test_default_repos(self):
        with (
            patch("app.services.insights_service.UserRepository"),
            patch("app.services.insights_service.MessageRepository"),
            patch("app.services.insights_service.InsightsReportRepository"),
        ):
            svc = InsightsService()
            assert svc.user_repo is not None
            assert svc.message_repo is not None
            assert svc.insights_repo is not None

    def test_custom_repos(self):
        mock_user = MagicMock()
        mock_msg = MagicMock()
        mock_insights = MagicMock()
        svc = InsightsService(
            user_repo=mock_user,
            message_repo=mock_msg,
            insights_repo=mock_insights,
        )
        assert svc.user_repo is mock_user
        assert svc.message_repo is mock_msg
        assert svc.insights_repo is mock_insights


class TestInsightsServiceConfig:
    """Test config loading."""

    def _make_service(self):
        mock_user = MagicMock()
        mock_msg = MagicMock()
        mock_insights = MagicMock()
        return (
            InsightsService(
                user_repo=mock_user,
                message_repo=mock_msg,
                insights_repo=mock_insights,
            ),
            mock_user,
            mock_msg,
            mock_insights,
        )

    def test_load_config_success(self):
        svc, _, _, _ = self._make_service()
        config_data = {"insights": {"model": "glm-5"}}
        with patch("builtins.open", mock_open(read_data=json.dumps(config_data))):
            with patch("os.path.join", return_value="/fake/config.json"):
                result = svc._load_config()
                assert result == config_data

    def test_load_config_file_not_found(self):
        svc, _, _, _ = self._make_service()
        with patch("builtins.open", side_effect=FileNotFoundError("Not found")):
            with patch("os.path.join", return_value="/fake/config.json"):
                result = svc._load_config()
                assert result == {}

    def test_load_config_invalid_json(self):
        svc, _, _, _ = self._make_service()
        with patch("builtins.open", mock_open(read_data="not json")):
            with patch("os.path.join", return_value="/fake/config.json"):
                result = svc._load_config()
                assert result == {}

    def test_get_api_credentials_from_database(self):
        """API key resolved from database with scope='local'."""
        svc, _, _, _ = self._make_service()
        config = {}

        with (
            patch.dict("os.environ", {}, clear=True),
            patch("app.modules.workspace.api_key_proxy.get_api_key_proxy_service") as mock_get,
        ):
            mock_proxy = MagicMock()
            mock_proxy.resolve_api_key_for_scope.return_value = (
                "db-key",
                "https://db.api.com/v1",
                1,
            )
            mock_get.return_value = mock_proxy

            api_key, base_url = svc._get_api_credentials(config)
            assert api_key == "db-key"
            assert base_url == "https://db.api.com/v1"
            mock_proxy.resolve_api_key_for_scope.assert_called_once_with(1, "openai", scope="local")

    def test_get_api_credentials_db_key_default_base_url(self):
        """DB key without base_url uses default dashscope URL."""
        svc, _, _, _ = self._make_service()
        config = {}

        with (
            patch.dict("os.environ", {}, clear=True),
            patch("app.modules.workspace.api_key_proxy.get_api_key_proxy_service") as mock_get,
        ):
            mock_proxy = MagicMock()
            mock_proxy.resolve_api_key_for_scope.return_value = ("db-key", None, 1)
            mock_get.return_value = mock_proxy

            api_key, base_url = svc._get_api_credentials(config)
            assert api_key == "db-key"
            assert base_url == "https://coding.dashscope.aliyuncs.com/v1"

    def test_get_api_credentials_fallback_to_env(self):
        """Falls back to env vars when database has no key."""
        svc, _, _, _ = self._make_service()
        config = {}

        with (
            patch.dict(
                "os.environ",
                {
                    "OPENAI_API_KEY": "env-key",
                    "OPENAI_BASE_URL": "https://env.api.com/v1",
                },
                clear=False,
            ),
            patch("app.modules.workspace.api_key_proxy.get_api_key_proxy_service") as mock_get,
        ):
            mock_proxy = MagicMock()
            mock_proxy.resolve_api_key_for_scope.return_value = None
            mock_get.return_value = mock_proxy

            api_key, base_url = svc._get_api_credentials(config)
            assert api_key == "env-key"
            assert base_url == "https://env.api.com/v1"

    def test_get_api_credentials_env_default_base_url(self):
        """Env fallback with only OPENAI_API_KEY uses default base URL."""
        svc, _, _, _ = self._make_service()
        config = {}

        with (
            patch.dict("os.environ", {"OPENAI_API_KEY": "env-key"}, clear=True),
            patch("app.modules.workspace.api_key_proxy.get_api_key_proxy_service") as mock_get,
        ):
            mock_proxy = MagicMock()
            mock_proxy.resolve_api_key_for_scope.return_value = None
            mock_get.return_value = mock_proxy

            api_key, base_url = svc._get_api_credentials(config)
            assert api_key == "env-key"
            assert base_url == "https://coding.dashscope.aliyuncs.com/v1"

    def test_get_api_credentials_no_key_anywhere(self):
        """Neither database nor env has a key → empty string."""
        svc, _, _, _ = self._make_service()
        config = {}

        with (
            patch.dict("os.environ", {}, clear=True),
            patch("app.modules.workspace.api_key_proxy.get_api_key_proxy_service") as mock_get,
        ):
            mock_proxy = MagicMock()
            mock_proxy.resolve_api_key_for_scope.return_value = None
            mock_get.return_value = mock_proxy

            api_key, _ = svc._get_api_credentials(config)
            assert api_key == ""

    def test_get_api_credentials_db_exception_falls_to_env(self):
        """DB exception is caught gracefully, falls back to env."""
        svc, _, _, _ = self._make_service()
        config = {}

        with (
            patch.dict("os.environ", {"OPENAI_API_KEY": "env-key"}, clear=False),
            patch(
                "app.modules.workspace.api_key_proxy.get_api_key_proxy_service",
                side_effect=Exception("DB down"),
            ),
        ):
            api_key, _ = svc._get_api_credentials(config)
            assert api_key == "env-key"

    def test_get_api_credentials_db_over_env(self):
        """Database key takes priority over environment variable."""
        svc, _, _, _ = self._make_service()
        config = {}

        with (
            patch.dict("os.environ", {"OPENAI_API_KEY": "env-key"}, clear=True),
            patch("app.modules.workspace.api_key_proxy.get_api_key_proxy_service") as mock_get,
        ):
            mock_proxy = MagicMock()
            mock_proxy.resolve_api_key_for_scope.return_value = (
                "db-key",
                "https://db.api.com/v1",
                1,
            )
            mock_get.return_value = mock_proxy

            api_key, base_url = svc._get_api_credentials(config)
            assert api_key == "db-key"
            assert base_url == "https://db.api.com/v1"


class TestInsightsServicePrompts:
    """Test prompt building."""

    def _make_service(self):
        return InsightsService(
            user_repo=MagicMock(),
            message_repo=MagicMock(),
            insights_repo=MagicMock(),
        )

    def test_build_system_prompt(self):
        svc = self._make_service()
        prompt = svc._build_system_prompt()
        assert "overall_score" in prompt
        assert "JSON" in prompt
        assert len(prompt) > 100

    def test_build_user_prompt(self):
        svc = self._make_service()
        stats = {
            "total_conversations": 10,
            "total_messages": 100,
            "total_tokens": 5000,
            "avg_messages_per_conversation": 10,
        }
        conversations = [
            {
                "messages": [
                    {"role": "user", "content": "Hello"},
                    {"role": "assistant", "content": "Hi there"},
                ]
            }
        ]
        prompt = svc._build_user_prompt(stats, conversations)
        assert "10" in prompt
        assert "100" in prompt
        assert "Hello" in prompt
        assert "用户" in prompt

    def test_build_user_prompt_multiple_conversations(self):
        svc = self._make_service()
        stats = {
            "total_conversations": 2,
            "total_messages": 4,
            "total_tokens": 100,
            "avg_messages_per_conversation": 2,
        }
        conversations = [
            {"messages": [{"role": "user", "content": "Q1"}]},
            {"messages": [{"role": "user", "content": "Q2"}]},
        ]
        prompt = svc._build_user_prompt(stats, conversations)
        assert "1" in prompt
        assert "2" in prompt


class TestInsightsServiceExtractJson:
    """Test JSON extraction from AI response."""

    def _make_service(self):
        return InsightsService(
            user_repo=MagicMock(),
            message_repo=MagicMock(),
            insights_repo=MagicMock(),
        )

    def test_extract_plain_json(self):
        svc = self._make_service()
        json_str = '{"overall_score": 7}'
        result = svc._extract_json(json_str)
        assert result == '{"overall_score": 7}'

    def test_extract_json_with_code_fence(self):
        svc = self._make_service()
        text = '```json\n{"overall_score": 7}\n```'
        result = svc._extract_json(text)
        assert result == '{"overall_score": 7}'

    def test_extract_json_with_generic_code_fence(self):
        svc = self._make_service()
        text = '```\n{"overall_score": 7}\n```'
        result = svc._extract_json(text)
        assert result == '{"overall_score": 7}'

    def test_extract_json_no_closing_fence(self):
        svc = self._make_service()
        text = '```json\n{"overall_score": 7}'
        result = svc._extract_json(text)
        # Should return the original text stripped when no closing fence found properly
        assert isinstance(result, str)


class TestInsightsServiceParseResponse:
    """Test AI response parsing."""

    def _make_service(self):
        return InsightsService(
            user_repo=MagicMock(),
            message_repo=MagicMock(),
            insights_repo=MagicMock(),
        )

    def test_parse_valid_response(self):
        svc = self._make_service()
        response_text = json.dumps(
            {
                "overall_score": 7,
                "overall_assessment": "Good usage",
                "strengths": ["Clear prompts"],
                "areas_for_improvement": ["Add more context"],
                "suggestions": [{"title": "Be specific", "description": "desc", "example": "ex"}],
            }
        )
        stats = {"total_messages": 100}
        result = svc._parse_ai_response(response_text, stats)
        assert result["overall_score"] == 7
        assert result["overall_assessment"] == "Good usage"
        assert len(result["strengths"]) == 1
        assert result["usage_summary"] == stats
        assert result["raw_response"] == response_text

    def test_parse_response_missing_required_field(self):
        svc = self._make_service()
        response_text = json.dumps(
            {
                "overall_score": 7,
                "overall_assessment": "Good",
                # Missing "strengths" and "areas_for_improvement"
            }
        )
        with pytest.raises(ValueError, match="Missing required field"):
            svc._parse_ai_response(response_text, {})

    def test_parse_response_score_clamping_high(self):
        svc = self._make_service()
        response_text = json.dumps(
            {
                "overall_score": 15,
                "overall_assessment": "Great",
                "strengths": [],
                "areas_for_improvement": [],
            }
        )
        result = svc._parse_ai_response(response_text, {})
        assert result["overall_score"] == 10

    def test_parse_response_score_clamping_low(self):
        svc = self._make_service()
        response_text = json.dumps(
            {
                "overall_score": -5,
                "overall_assessment": "Bad",
                "strengths": [],
                "areas_for_improvement": [],
            }
        )
        result = svc._parse_ai_response(response_text, {})
        assert result["overall_score"] == 1

    def test_parse_response_with_suggestions(self):
        svc = self._make_service()
        response_text = json.dumps(
            {
                "overall_score": 8,
                "overall_assessment": "Excellent",
                "strengths": ["Structured prompts"],
                "areas_for_improvement": ["Context provision"],
                "suggestions": [
                    {"title": "Use templates", "description": "desc", "example": "ex"},
                ],
            }
        )
        result = svc._parse_ai_response(response_text, {})
        assert len(result["suggestions"]) == 1

    def test_parse_response_without_suggestions(self):
        svc = self._make_service()
        response_text = json.dumps(
            {
                "overall_score": 5,
                "overall_assessment": "Average",
                "strengths": [],
                "areas_for_improvement": [],
            }
        )
        result = svc._parse_ai_response(response_text, {})
        assert result["suggestions"] == []

    def test_parse_response_invalid_json(self):
        svc = self._make_service()
        with pytest.raises(json.JSONDecodeError):
            svc._parse_ai_response("not json", {})


class TestInsightsServiceCallApi:
    """Test AI API calls."""

    def _make_service(self):
        return InsightsService(
            user_repo=MagicMock(),
            message_repo=MagicMock(),
            insights_repo=MagicMock(),
        )

    @patch("app.services.insights_service.requests")
    def test_call_ai_api_success(self, mock_requests):
        svc = self._make_service()
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": '{"overall_score": 7}'}}]
        }
        mock_response.raise_for_status.return_value = None
        mock_requests.post.return_value = mock_response

        result = svc._call_ai_api(
            api_key="test-key",
            base_url="https://api.example.com/v1",
            model="glm-5",
            system_prompt="system",
            user_prompt="user",
        )
        assert result == '{"overall_score": 7}'

    @patch("app.services.insights_service.requests")
    def test_call_ai_api_empty_content(self, mock_requests):
        svc = self._make_service()
        mock_response = MagicMock()
        mock_response.json.return_value = {"choices": [{"message": {"content": ""}}]}
        mock_response.raise_for_status.return_value = None
        mock_requests.post.return_value = mock_response

        with pytest.raises(ValueError, match="empty content"):
            svc._call_ai_api(
                api_key="test-key",
                base_url="https://api.example.com/v1",
                model="glm-5",
                system_prompt="system",
                user_prompt="user",
            )

    @patch("app.services.insights_service.requests")
    def test_call_ai_api_http_error(self, mock_requests):
        svc = self._make_service()
        mock_requests.post.side_effect = Exception("HTTP error")

        with pytest.raises(Exception, match="HTTP error"):
            svc._call_ai_api(
                api_key="test-key",
                base_url="https://api.example.com/v1",
                model="glm-5",
                system_prompt="system",
                user_prompt="user",
            )

    @patch("app.services.insights_service.requests")
    def test_call_ai_api_sends_correct_payload(self, mock_requests):
        svc = self._make_service()
        mock_response = MagicMock()
        mock_response.json.return_value = {"choices": [{"message": {"content": "result"}}]}
        mock_response.raise_for_status.return_value = None
        mock_requests.post.return_value = mock_response

        svc._call_ai_api(
            api_key="key",
            base_url="https://api.example.com/v1",
            model="glm-5",
            system_prompt="sys",
            user_prompt="usr",
            temperature=0.5,
            max_tokens=2048,
        )

        call_args = mock_requests.post.call_args
        assert call_args[1]["headers"]["Authorization"] == "Bearer key"
        payload = call_args[1]["json"]
        assert payload["model"] == "glm-5"
        assert payload["temperature"] == 0.5
        assert payload["max_tokens"] == 2048


class TestInsightsServiceGenerateInsights:
    """Test generate_insights method."""

    def _make_service(self):
        mock_user = MagicMock()
        mock_msg = MagicMock()
        mock_insights = MagicMock()
        svc = InsightsService(
            user_repo=mock_user,
            message_repo=mock_msg,
            insights_repo=mock_insights,
        )
        return svc, mock_user, mock_msg, mock_insights

    def test_returns_cached_report(self):
        svc, _, _, mock_insights = self._make_service()
        mock_insights.get_report.return_value = {
            "id": 1,
            "overall_score": 8,
            "overall_assessment": "Good",
            "strengths": '["Clear prompts"]',
            "areas_for_improvement": "[]",
            "suggestions": "[]",
            "usage_summary": "{}",
            "model": "glm-5",
            "start_date": "2026-01-01",
            "end_date": "2026-01-31",
            "created_at": "2026-01-31T00:00:00",
        }

        result, error = svc.generate_insights(1, "2026-01-01", "2026-01-31")
        assert error is None
        assert result["overall_score"] == 8
        assert result["strengths"] == ["Clear prompts"]

    def test_user_not_found(self):
        svc, mock_user, _, mock_insights = self._make_service()
        mock_insights.get_report.return_value = None
        mock_user.get_user_by_id.return_value = None

        result, error = svc.generate_insights(999, "2026-01-01", "2026-01-31")
        assert result is None
        assert error == "User not found"

    def test_user_no_identity(self):
        svc, mock_user, _, mock_insights = self._make_service()
        mock_insights.get_report.return_value = None
        mock_user.get_user_by_id.return_value = {
            "username": "",
            "system_account": "",
        }

        result, error = svc.generate_insights(1, "2026-01-01", "2026-01-31")
        assert result is None
        assert error == "Cannot determine user identity"

    def test_insufficient_data_messages(self):
        svc, mock_user, mock_msg, mock_insights = self._make_service()
        mock_insights.get_report.return_value = None
        mock_user.get_user_by_id.return_value = {
            "username": "testuser",
            "system_account": "sys",
        }
        mock_msg.get_user_messages_stats.return_value = {"total_messages": 3}

        result, error = svc.generate_insights(1, "2026-01-01", "2026-01-31")
        assert result is None
        assert error == "insufficient_data"

    def test_insufficient_data_conversations(self):
        svc, mock_user, mock_msg, mock_insights = self._make_service()
        mock_insights.get_report.return_value = None
        mock_user.get_user_by_id.return_value = {
            "username": "testuser",
            "system_account": "sys",
        }
        mock_msg.get_user_messages_stats.return_value = {"total_messages": 50}
        mock_msg.get_user_conversation_samples.return_value = []

        result, error = svc.generate_insights(1, "2026-01-01", "2026-01-31")
        assert result is None
        assert error == "insufficient_data"

    def test_no_api_key(self):
        svc, mock_user, mock_msg, mock_insights = self._make_service()
        mock_insights.get_report.return_value = None
        mock_user.get_user_by_id.return_value = {
            "username": "testuser",
            "system_account": "sys",
        }
        mock_msg.get_user_messages_stats.return_value = {"total_messages": 50}
        mock_msg.get_user_conversation_samples.return_value = [{"messages": []}]

        with patch.object(svc, "_load_config", return_value={"auth": {"env": {}}}):
            with patch.dict("os.environ", {}, clear=True):
                result, error = svc.generate_insights(1, "2026-01-01", "2026-01-31")
                assert result is None
                assert error == "API key not configured"

    def test_generate_success(self):
        svc, mock_user, mock_msg, mock_insights = self._make_service()
        mock_insights.get_report.return_value = None
        mock_user.get_user_by_id.return_value = {
            "username": "testuser",
            "system_account": "sys",
        }
        stats = {"total_messages": 50}
        mock_msg.get_user_messages_stats.return_value = stats
        mock_msg.get_user_conversation_samples.return_value = [{"messages": []}]

        config = {
            "auth": {"env": {"OPENAI_API_KEY": "test-key"}},
            "insights": {"model": "glm-5"},
        }
        mock_insights.save_report.return_value = 42

        ai_response = json.dumps(
            {
                "overall_score": 7,
                "overall_assessment": "Good",
                "strengths": ["Clear"],
                "areas_for_improvement": ["Context"],
            }
        )

        with (
            patch.object(svc, "_load_config", return_value=config),
            patch.object(svc, "_call_ai_api", return_value=ai_response),
        ):
            result, error = svc.generate_insights(1, "2026-01-01", "2026-01-31")
            assert error is None
            assert result["overall_score"] == 7
            assert result["id"] == 42

    def test_generate_ai_failure(self):
        svc, mock_user, mock_msg, mock_insights = self._make_service()
        mock_insights.get_report.return_value = None
        mock_user.get_user_by_id.return_value = {
            "username": "testuser",
            "system_account": "sys",
        }
        mock_msg.get_user_messages_stats.return_value = {"total_messages": 50}
        mock_msg.get_user_conversation_samples.return_value = [{"messages": []}]

        config = {
            "auth": {"env": {"OPENAI_API_KEY": "test-key"}},
            "insights": {"model": "glm-5"},
        }

        with (
            patch.object(svc, "_load_config", return_value=config),
            patch.object(svc, "_call_ai_api", side_effect=Exception("API down")),
        ):
            result, error = svc.generate_insights(1, "2026-01-01", "2026-01-31")
            assert result is None
            assert "API down" in error


class TestInsightsServiceFormatReport:
    """Test report formatting."""

    def _make_service(self):
        return InsightsService(
            user_repo=MagicMock(),
            message_repo=MagicMock(),
            insights_repo=MagicMock(),
        )

    def test_format_report(self):
        svc = self._make_service()
        report = {
            "id": 1,
            "overall_score": 8,
            "overall_assessment": "Good",
            "strengths": '["Clear prompts"]',
            "areas_for_improvement": '["Context"]',
            "suggestions": '[{"title": "Tip"}]',
            "usage_summary": '{"total": 100}',
            "model": "glm-5",
            "start_date": "2026-01-01",
            "end_date": "2026-01-31",
            "created_at": "2026-01-31",
        }
        result = svc._format_report(report)
        assert result["id"] == 1
        assert result["strengths"] == ["Clear prompts"]
        assert result["areas_for_improvement"] == ["Context"]
        assert result["usage_summary"] == {"total": 100}

    def test_format_report_missing_fields(self):
        svc = self._make_service()
        report = {
            "id": 1,
            "overall_score": 5,
            "overall_assessment": "Average",
            # strengths, etc. are missing
        }
        result = svc._format_report(report)
        assert result["strengths"] == []
        assert result["areas_for_improvement"] == []
        assert result["suggestions"] == []


class TestInsightsReportRepository:
    """Test InsightsReportRepository."""

    def _make_repo(self):
        mock_db = MagicMock()
        repo = InsightsReportRepository(db=mock_db)
        return repo, mock_db

    def test_init_default_db(self):
        with patch("app.repositories.insights_repo.Database"):
            repo = InsightsReportRepository()
            assert repo.db is not None

    def test_save_report_postgresql(self):
        repo, mock_db = self._make_repo()
        mock_db.fetch_one.return_value = {"id": 1}

        with patch("app.repositories.insights_repo.is_postgresql", return_value=True):
            result = repo.save_report(
                user_id=1,
                start_date="2026-01-01",
                end_date="2026-01-31",
                report_data={
                    "overall_score": 7,
                    "overall_assessment": "Good",
                    "strengths": ["Clear"],
                    "areas_for_improvement": [],
                    "suggestions": [],
                    "usage_summary": {},
                    "raw_response": "raw",
                },
                model="glm-5",
            )
            assert result == 1

    def test_save_report_sqlite(self):
        repo, mock_db = self._make_repo()
        mock_db.fetch_one.return_value = {"id": 2}

        with patch("app.repositories.insights_repo.is_postgresql", return_value=False):
            result = repo.save_report(
                user_id=1,
                start_date="2026-01-01",
                end_date="2026-01-31",
                report_data={
                    "overall_score": 7,
                    "overall_assessment": "Good",
                    "strengths": [],
                    "areas_for_improvement": [],
                    "suggestions": [],
                    "usage_summary": {},
                    "raw_response": "raw",
                },
                model="glm-5",
            )
            assert result == 2

    def test_save_report_error(self):
        repo, mock_db = self._make_repo()
        mock_db.fetch_one.side_effect = Exception("DB error")

        result = repo.save_report(
            user_id=1,
            start_date="2026-01-01",
            end_date="2026-01-31",
            report_data={"overall_score": 7},
            model="glm-5",
        )
        assert result is None

    def test_get_report(self):
        repo, mock_db = self._make_repo()
        mock_db.fetch_one.return_value = {"id": 1, "user_id": 1}

        result = repo.get_report(1, "2026-01-01", "2026-01-31")
        assert result["id"] == 1
        mock_db.fetch_one.assert_called_once()

    def test_get_user_reports(self):
        repo, mock_db = self._make_repo()
        mock_db.fetch_all.return_value = [
            {"id": 1, "overall_score": 7},
            {"id": 2, "overall_score": 8},
        ]

        result = repo.get_user_reports(1, limit=5)
        assert len(result) == 2

    def test_delete_report_success(self):
        repo, mock_db = self._make_repo()

        result = repo.delete_report(1, user_id=1)
        assert result is True
        mock_db.execute.assert_called_once()

    def test_delete_report_error(self):
        repo, mock_db = self._make_repo()
        mock_db.execute.side_effect = Exception("Delete error")

        result = repo.delete_report(1, user_id=1)
        assert result is False

    def test_get_report_by_id(self):
        repo, mock_db = self._make_repo()
        mock_db.fetch_one.return_value = {"id": 1, "user_id": 1}

        result = repo.get_report_by_id(1, user_id=1)
        assert result["id"] == 1
        mock_db.fetch_one.assert_called_once()
