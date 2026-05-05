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

    def _get_api_credentials(self, config: dict) -> tuple[str, str]:
        """
        Get API credentials from config and environment.

        Supports multiple key names used across deployments:
        - BAILIAN_CODING_PLAN_API_KEY (production deployment)
        - OPENAI_API_KEY (standard)

        Returns:
            Tuple[api_key, base_url]
        """
        auth_cfg = config.get("auth", {}).get("env", {})
        api_key = (
            auth_cfg.get("BAILIAN_CODING_PLAN_API_KEY")
            or os.environ.get("BAILIAN_CODING_PLAN_API_KEY", "")
            or auth_cfg.get("OPENAI_API_KEY")
            or os.environ.get("OPENAI_API_KEY", "")
        )
        base_url = (
            auth_cfg.get("OPENAI_BASE_URL")
            or os.environ.get("OPENAI_BASE_URL")
            or "https://coding.dashscope.aliyuncs.com/v1"
        )
        return api_key, base_url

    def generate_insights(
        self, user_id: int, start_date: str, end_date: str
    ) -> tuple[Optional[dict], Optional[str]]:
        """
        Generate insights report for a user's conversations.

        Args:
            user_id: User ID.
            start_date: Start date (YYYY-MM-DD).
            end_date: End date (YYYY-MM-DD).

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
        model = insights_cfg.get("model", "glm-5")
        api_key, base_url = self._get_api_credentials(config)

        if not api_key:
            return None, "API key not configured"

        # 7. Build prompt
        system_prompt = self._build_system_prompt()
        user_prompt = self._build_user_prompt(stats, conversations)

        # 8. Call AI API
        try:
            response_text = self._call_ai_api(
                api_key=api_key,
                base_url=base_url,
                model=model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=insights_cfg.get("temperature", 0.3),
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

    def _build_system_prompt(self) -> str:
        """Build the system prompt for AI analysis."""
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

    def _build_user_prompt(self, stats: dict, conversations: list) -> str:
        """Build the user prompt with conversation data."""
        stats_summary = f"""## 用户对话统计（分析周期内）

- 总会话数：{stats.get('total_conversations', 0)}
- 总消息数：{stats.get('total_messages', 0)}
- 总Token消耗：{stats.get('total_tokens', 0)}
- 平均每会话消息数：{stats.get('avg_messages_per_conversation', 0)}
"""

        conversations_text = "## 抽样对话内容\n\n"
        for i, conv in enumerate(conversations, 1):
            conversations_text += f"### 会话 {i}\n"
            for msg in conv.get("messages", []):
                role_label = "用户" if msg["role"] == "user" else "AI助手"
                conversations_text += f"**{role_label}**：{msg['content']}\n\n"
            conversations_text += "---\n\n"

        return f"""{stats_summary}

{conversations_text}

请分析以上用户与AI的对话数据，给出AI使用效率的评估和改进建议。"""

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
