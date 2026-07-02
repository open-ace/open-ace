"""
Open ACE - Insights Service

Generates AI conversation insights reports by analyzing user conversation data.
Calls GLM-5 model (OpenAI-compatible API) to produce structured analysis.
"""

import json
import logging
import os
from typing import Optional, cast

import requests

from app.repositories.database import CONFIG_DIR
from app.repositories.insights_repo import InsightsReportRepository
from app.repositories.message_repo import MessageRepository
from app.repositories.user_repo import UserRepository

logger = logging.getLogger(__name__)


class InsightsService:
    """Service for generating AI conversation insights reports."""

    def __init__(
        self,
        user_repo: Optional[UserRepository] = None,
        message_repo: Optional[MessageRepository] = None,
        insights_repo: Optional[InsightsReportRepository] = None,
    ):
        self.user_repo = user_repo or UserRepository()
        self.message_repo = message_repo or MessageRepository()
        self.insights_repo = insights_repo or InsightsReportRepository()

    def _load_config(self) -> dict:
        """Load insights configuration from config.json."""
        config_path = os.path.join(CONFIG_DIR, "config.json")
        try:
            with open(config_path) as f:
                return cast("dict", json.load(f))
        except Exception as e:
            logger.warning(f"Could not load config.json: {e}")
            return {}

    def _get_api_credentials(self, config: dict) -> tuple[str, str, Optional[str]]:
        """
        Get API credentials from the api_key_store database.

        Resolves keys with scope='local' or 'shared' for the openai provider.
        Falls back to environment variables if no key is found in the database.

        Returns:
            Tuple[api_key, base_url, default_model]
        """
        # Primary: resolve from database
        try:
            from app.modules.workspace.api_key_proxy import get_api_key_proxy_service

            api_proxy = get_api_key_proxy_service()
            result = api_proxy.resolve_api_key_for_scope(1, "openai", scope="local")
            if result:
                api_key, base_url, _, cli_settings = result
                default_model = self._extract_model_from_cli_settings(cli_settings)
                if base_url:
                    return api_key, base_url, default_model
                return api_key, "https://coding.dashscope.aliyuncs.com/v1", default_model
        except Exception as e:
            logger.warning("Failed to resolve API key from database: %s", e)

        # Fallback: environment variables
        api_key = os.environ.get("OPENAI_API_KEY", "")
        base_url = os.environ.get("OPENAI_BASE_URL", "https://coding.dashscope.aliyuncs.com/v1")
        return api_key, base_url, None

    def _extract_model_from_cli_settings(self, cli_settings: Optional[str]) -> Optional[str]:
        """
        Extract default model from cli_settings JSON.

        Parses cli_settings and looks for modelProviders.openai[0].id.
        Returns None if cli_settings is empty, invalid, or missing the model.

        Args:
            cli_settings: JSON string from api_key_store.cli_settings.

        Returns:
            Model ID string or None.
        """
        if not cli_settings:
            return None

        try:
            settings = json.loads(cli_settings)
        except json.JSONDecodeError:
            logger.warning("Invalid cli_settings JSON: %s", cli_settings[:100])
            return None

        # Try modelProviders.openai[0].id
        model_providers = settings.get("modelProviders", {})
        openai_models = model_providers.get("openai", [])
        if isinstance(openai_models, list) and openai_models:
            first_model = openai_models[0]
            if isinstance(first_model, dict):
                model_id = first_model.get("id")
                if model_id:
                    return str(model_id)

        return None

    def generate_insights(
        self, user_id: int, start_date: str, end_date: str, language: str = "zh"
    ) -> tuple[Optional[dict], Optional[str]]:
        """
        Generate insights report for a user's conversations.

        Args:
            user_id: User ID.
            start_date: Start date (YYYY-MM-DD).
            end_date: End date (YYYY-MM-DD).
            language: Output language (zh, en, ja, ko). Defaults to "zh".

        Returns:
            Tuple[Optional[Dict], Optional[str]]: (report_data, None) on success
                or (None, error_message) on failure.
        """
        # 1. Check cache
        existing = self.insights_repo.get_report(user_id, start_date, end_date)
        if existing:
            logger.info(f"Returning cached insights report for user {user_id}")
            return self._format_report(existing), None

        # 2. Resolve user identity
        user = self.user_repo.get_user_by_id(user_id)
        if not user:
            return None, "User not found"

        sender_prefix = user.get("system_account") or user.get("username", "")
        if not sender_prefix:
            return None, "Cannot determine user identity"

        # 3. Query statistics
        stats = self.message_repo.get_user_messages_stats(start_date, end_date, sender_prefix)

        # 4. Check data volume
        if stats.get("total_messages", 0) < 5:
            return None, "insufficient_data"

        # 5. Sample conversations
        conversations = self.message_repo.get_user_conversation_samples(
            start_date, end_date, sender_prefix, limit=5
        )

        if not conversations:
            return None, "insufficient_data"

        # 6. Load config and credentials
        config = self._load_config()
        insights_cfg = config.get("insights", {})
        api_key, base_url, cli_model = self._get_api_credentials(config)

        # Model priority: config.insights.model > cli_settings model > default
        model = insights_cfg.get("model") or cli_model or "glm-5.1"

        if not api_key:
            return None, "API key not configured"

        # 7. Build prompt
        system_prompt = self._build_system_prompt(language)
        user_prompt = self._build_user_prompt(stats, conversations, language)

        # 8. Call AI API
        try:
            response_text = self._call_ai_api(
                api_key=api_key,
                base_url=base_url,
                model=model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=insights_cfg.get("temperature", 0),
                max_tokens=insights_cfg.get("max_tokens", 4096),
            )
        except Exception as e:
            logger.error(f"Error calling AI API for insights: {e}")
            return None, f"AI analysis failed: {str(e)}"

        # 9. Parse response
        try:
            report_data = self._parse_ai_response(response_text, stats)
        except Exception as e:
            logger.error(f"Error parsing AI response: {e}")
            return None, f"Failed to parse AI response: {str(e)}"

        # 10. Save report
        report_id = self.insights_repo.save_report(
            user_id=user_id,
            start_date=start_date,
            end_date=end_date,
            report_data=report_data,
            model=model,
        )

        if report_id:
            report_data["id"] = report_id

        report_data["start_date"] = start_date
        report_data["end_date"] = end_date

        return report_data, None

    def _build_system_prompt(self, language: str = "zh") -> str:
        """Build the system prompt for AI analysis based on language."""
        prompts = {
            "zh": self._build_chinese_system_prompt(),
            "en": self._build_english_system_prompt(),
            "ja": self._build_japanese_system_prompt(),
            "ko": self._build_korean_system_prompt(),
        }
        return prompts.get(language, prompts["zh"])

    def _build_chinese_system_prompt(self) -> str:
        """Build Chinese system prompt."""
        return """你是一位AI使用效率分析专家。你的任务是分析用户与AI的对话数据，评估用户的AI使用效率，并给出个性化的改进建议。

请严格按照以下JSON格式输出分析结果：
{
    "overall_score": <1-10的整数评分>,
    "overall_assessment": "<总体评价文本>",
    "strengths": ["<优势1>", "<优势2>", ...],
    "areas_for_improvement": ["<改进方向1>", "<改进方向2>", ...],
    "suggestions": [
        {
            "title": "<建议标题>",
            "description": "<详细描述>",
            "example": "<具体的改进示例>"
        },
        ...
    ]
}

分析维度：
1. **表达清晰度**：用户的提问是否清晰明确，是否容易被AI理解
2. **上下文提供**：用户是否提供了足够的背景信息和上下文
3. **交互效率**：用户是否能够高效地与AI沟通，避免无效重复
4. **提示词质量**：用户的提示词是否结构化、具体、有针对性
5. **学习适应**：用户是否能根据AI的反馈调整自己的提问方式

评分标准：
- 8-10分：优秀，能高效利用AI
- 5-7分：良好，有改进空间
- 1-4分：需要提升AI使用技能

请确保：
- 评分客观公正
- 优势具体、有针对性
- 改进方向切实可行
- 建议包含可操作的示例
- 使用中文输出"""

    def _build_english_system_prompt(self) -> str:
        """Build English system prompt."""
        return """You are an AI usage efficiency analysis expert. Your task is to analyze user-AI conversation data, evaluate the user's AI usage efficiency, and provide personalized improvement suggestions.

Please output the analysis results strictly in the following JSON format:
{
    "overall_score": <integer score 1-10>,
    "overall_assessment": "<overall assessment text>",
    "strengths": ["<strength1>", "<strength2>", ...],
    "areas_for_improvement": ["<improvement1>", "<improvement2>", ...],
    "suggestions": [
        {
            "title": "<suggestion title>",
            "description": "<detailed description>",
            "example": "<specific improvement example>"
        },
        ...
    ]
}

Analysis dimensions:
1. **Clarity**: Whether the user's questions are clear and easy for AI to understand
2. **Context**: Whether the user provides sufficient background information and context
3. **Efficiency**: Whether the user can communicate efficiently with AI, avoiding ineffective repetition
4. **Prompt Quality**: Whether the user's prompts are structured, specific, and targeted
5. **Adaptability**: Whether the user can adjust their questioning style based on AI feedback

Scoring criteria:
- 8-10: Excellent, can efficiently utilize AI
- 5-7: Good, has room for improvement
- 1-4: Needs to improve AI usage skills

Please ensure:
- Fair and objective scoring
- Specific and targeted strengths
- Feasible improvement directions
- Suggestions include actionable examples
- Output in English"""

    def _build_japanese_system_prompt(self) -> str:
        """Build Japanese system prompt."""
        return """あなたはAI使用効率分析の専門家です。ユーザーとAIの会話データを分析し、ユーザーのAI使用効率を評価し、個別の改善提案を提供することがあなたの任务です。

以下のJSON形式で分析結果を出力してください：
{
    "overall_score": <1-10の整数スコア>,
    "overall_assessment": "<総合評価テキスト>",
    "strengths": ["<強み1>", "<強み2>", ...],
    "areas_for_improvement": ["<改善点1>", "<改善点2>", ...],
    "suggestions": [
        {
            "title": "<提案タイトル>",
            "description": "<詳細な説明>",
            "example": "<具体的な改善例>"
        },
        ...
    ]
}

分析の観点：
1. **明確性**：ユーザーの質問が明確でAIが理解しやすいか
2. **コンテキスト**：ユーザーが十分な背景情報とコンテキストを提供しているか
3. **効率性**：ユーザーが効率的にAIとコミュニケーションでき、無効な繰り返しを避けているか
4. **プロンプト品質**：ユーザーのプロンプトが構造化され、具体的で、ターゲットを持っているか
5. **適応性**：ユーザーがAIのフィードバックに基づいて質問スタイルを調整できるか

評価基準：
- 8-10：優秀、AIを効率的に活用できる
- 5-7：良好、改善の余地がある
- 1-4：AI使用スキルの向上が必要

以下を確保してください：
- 公平で客観的な評価
- 具体的でターゲットを持った強み
- 実現可能な改善方向
- 実行可能な例を含む提案
- 日本語で出力"""

    def _build_korean_system_prompt(self) -> str:
        """Build Korean system prompt."""
        return """당신은 AI 사용 효율 분석 전문가입니다. 사용자와 AI의 대화 데이터를 분석하고, 사용자의 AI 사용 효율을 평가하고, 개인화된 개선 제안을 제공하는 것이 당신의 작업입니다.

다음 JSON 형식으로 분석 결과를 출력하세요:
{
    "overall_score": <1-10 정수 점수>,
    "overall_assessment": "<종합 평가 텍스트>",
    "strengths": ["<강점1>", "<강점2>", ...],
    "areas_for_improvement": ["<개선점1>", "<개선점2>", ...],
    "suggestions": [
        {
            "title": "<제안 제목>",
            "description": "<자세한 설명>",
            "example": "<구체적인 개선 예시>"
        },
        ...
    ]
}

분석 차원:
1. **명확성**: 사용자의 질문이 명확하고 AI가 이해하기 쉬운지
2. **컨텍스트**: 사용자가 충분한 배경 정보와 컨텍스트를 제공하는지
3. **효율성**: 사용자가 AI와 효율적으로 통신하고 비효율적인 반복을 피하는지
4. **프롬프트 품질**: 사용자의 프롬프트가 구조화되고, 구체적이고, 타겟이 있는지
5. **적응성**: 사용자가 AI의 피드백을 기반으로 질문 스타일을 조정할 수 있는지

평가 기준:
- 8-10: 우수, AI를 효율적으로 활용할 수 있음
- 5-7: 양호, 개선 공간이 있음
- 1-4: AI 사용 기술 향상 필요

다음을 확인하세요:
- 공정하고 객체적인 평가
- 구체적이고 타겟이 있는 강점
- 실현 가능한 개선 방향
- 실행 가능한 예시를 포함한 제안
- 한국어로 출력"""

    def _build_user_prompt(self, stats: dict, conversations: list, language: str = "zh") -> str:
        """Build the user prompt with conversation data based on language."""
        # Get language-specific labels
        labels = self._get_user_prompt_labels(language)

        stats_summary = f"""## {labels['stats_title']}

- {labels['total_conversations']}：{stats.get("total_conversations", 0)}
- {labels['total_messages']}：{stats.get("total_messages", 0)}
- {labels['total_tokens']}：{stats.get("total_tokens", 0)}
- {labels['avg_messages']}：{stats.get("avg_messages_per_conversation", 0)}
"""

        conversations_text = f"## {labels['sample_title']}\n\n"
        for i, conv in enumerate(conversations, 1):
            conversations_text += f"### {labels['session']} {i}\n"
            for msg in conv.get("messages", []):
                role_label = labels["user"] if msg["role"] == "user" else labels["assistant"]
                conversations_text += f"**{role_label}**：{msg['content']}\n\n"
            conversations_text += "---\n\n"

        return f"""{stats_summary}

{conversations_text}

{labels['request']}"""

    def _get_user_prompt_labels(self, language: str) -> dict:
        """Get language-specific labels for user prompt."""
        labels = {
            "zh": {
                "stats_title": "用户对话统计（分析周期内）",
                "total_conversations": "总会话数",
                "total_messages": "总消息数",
                "total_tokens": "总Token消耗",
                "avg_messages": "平均每会话消息数",
                "sample_title": "抽样对话内容",
                "session": "会话",
                "user": "用户",
                "assistant": "AI助手",
                "request": "请分析以上用户与AI的对话数据，给出AI使用效率的评估和改进建议。",
            },
            "en": {
                "stats_title": "User Conversation Statistics (Analysis Period)",
                "total_conversations": "Total Conversations",
                "total_messages": "Total Messages",
                "total_tokens": "Total Token Consumption",
                "avg_messages": "Avg Messages per Conversation",
                "sample_title": "Sample Conversation Content",
                "session": "Conversation",
                "user": "User",
                "assistant": "AI Assistant",
                "request": "Please analyze the above user-AI conversation data and provide an evaluation of AI usage efficiency and improvement suggestions.",
            },
            "ja": {
                "stats_title": "ユーザー会話統計（分析期間内）",
                "total_conversations": "総会話数",
                "total_messages": "総メッセージ数",
                "total_tokens": "総Token消費",
                "avg_messages": "平均会話メッセージ数",
                "sample_title": "サンプル会話内容",
                "session": "会話",
                "user": "ユーザー",
                "assistant": "AIアシスタント",
                "request": "上記のユーザーとAIの会話データを分析し、AI使用効率の評価と改善提案を提供してください。",
            },
            "ko": {
                "stats_title": "사용자 대화 통계 (분석 기간)",
                "total_conversations": "총 대화 수",
                "total_messages": "총 메시지 수",
                "total_tokens": "총 Token 소비",
                "avg_messages": "평균 대화 메시지 수",
                "sample_title": "샘플 대화 내용",
                "session": "대화",
                "user": "사용자",
                "assistant": "AI 도우미",
                "request": "위의 사용자와 AI의 대화 데이터를 분석하고 AI 사용 효율 평가와 개선 제안을 제공하세요.",
            },
        }
        return labels.get(language, labels["zh"])

    def _call_ai_api(
        self,
        api_key: str,
        base_url: str,
        model: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> str:
        """Call the OpenAI-compatible API."""
        url = f"{base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }

        response = requests.post(url, headers=headers, json=payload, timeout=300)
        response.raise_for_status()

        data = response.json()
        content = data["choices"][0]["message"].get("content", "")
        if not content or not content.strip():
            raise ValueError(
                "AI returned empty content (reasoning model may have consumed all tokens). "
                "Consider increasing max_tokens or using a non-reasoning model."
            )
        return cast("str", content)

    def _extract_json(self, text: str) -> str:
        """Extract JSON from AI response, stripping markdown code fences if present."""
        text = text.strip()
        # Match ```json ... ``` or ``` ... ```
        if text.startswith("```"):
            # Find the first newline after the opening ```
            first_newline = text.find("\n")
            if first_newline != -1:
                # Find the closing ```
                last_backticks = text.rfind("```")
                if last_backticks > first_newline:
                    text = text[first_newline + 1 : last_backticks].strip()
        return text

    def _parse_ai_response(self, response_text: str, stats: dict) -> dict:
        """Parse AI response into structured report data."""
        clean_text = self._extract_json(response_text)
        parsed = json.loads(clean_text)

        # Validate required fields
        required_fields = [
            "overall_score",
            "overall_assessment",
            "strengths",
            "areas_for_improvement",
        ]
        for field in required_fields:
            if field not in parsed:
                raise ValueError(f"Missing required field: {field}")

        # Ensure score is within range
        score = parsed["overall_score"]
        if not isinstance(score, int) or score < 1 or score > 10:
            score = max(1, min(10, int(score)))

        return {
            "overall_score": score,
            "overall_assessment": parsed["overall_assessment"],
            "strengths": parsed.get("strengths", []),
            "areas_for_improvement": parsed.get("areas_for_improvement", []),
            "suggestions": parsed.get("suggestions", []),
            "usage_summary": stats,
            "raw_response": response_text,
        }

    def _format_report(self, report: dict) -> dict:
        """Format a database report record for API response."""
        return {
            "id": report.get("id"),
            "overall_score": report.get("overall_score"),
            "overall_assessment": report.get("overall_assessment"),
            "strengths": json.loads(report.get("strengths", "[]")),
            "areas_for_improvement": json.loads(report.get("areas_for_improvement", "[]")),
            "suggestions": json.loads(report.get("suggestions", "[]")),
            "usage_summary": json.loads(report.get("usage_summary", "{}")),
            "model": report.get("model"),
            "start_date": report.get("start_date"),
            "end_date": report.get("end_date"),
            "created_at": report.get("created_at"),
        }
