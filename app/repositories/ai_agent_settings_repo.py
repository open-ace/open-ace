"""
Open ACE - AI Agent Settings Repository

CRUD operations for the ai_agent_settings table.
Stores AI agent configuration such as the GitHub account
used by autonomous workflows.
"""
from __future__ import annotations


import logging
from typing import Any

from app.repositories.database import Database, adapt_sql

logger = logging.getLogger(__name__)

# Default values for AI agent settings
DEFAULT_SETTINGS: dict[str, Any] = {
    "ai_github_token": "",
    "ai_github_author_name": "Open ACE AI",
    "ai_github_author_email": "bot@open-ace.com",
}


def _mask_token(token: str) -> str:
    """Mask a GitHub token for display, keeping first 4 and last 4 chars.

    Examples:
        ghp_abc1234567890 -> ghp_****7890
        (empty)           -> (empty)
        short             -> ****
    """
    if not token or len(token) <= 8:
        return "****" if token else ""
    return f"{token[:4]}****{token[-4:]}"


class AiAgentSettingsRepo:
    """Repository for AI agent settings."""

    def __init__(self, db: Database | None = None):
        self.db = db or Database()

    def get_ai_agent_settings(self, mask_token: bool = True) -> dict[str, Any]:
        """Read all AI agent settings from the database.

        Args:
            mask_token: If True, mask the GitHub token value for API responses.

        Returns:
            Dict of setting_key -> setting_value with defaults applied.
        """
        settings = dict(DEFAULT_SETTINGS)

        try:
            rows = self.db.fetch_all("SELECT setting_key, setting_value FROM ai_agent_settings")
            if rows:
                for row in rows:
                    settings[row["setting_key"]] = row["setting_value"]
        except Exception as e:
            logger.debug("ai_agent_settings table not available, using defaults: %s", e)

        if mask_token and settings.get("ai_github_token"):
            settings["ai_github_token"] = _mask_token(settings["ai_github_token"])

        return settings

    def update_ai_agent_settings(self, settings: dict[str, Any]) -> bool:
        """Update AI agent settings in the database (UPSERT).

        Args:
            settings: Dict of setting_key -> new value.

        Returns:
            True if successful.
        """
        try:
            with self.db.connection() as conn:
                cursor = conn.cursor()

                for key, value in settings.items():
                    str_value = str(value) if not isinstance(value, str) else value

                    cursor.execute(
                        adapt_sql(
                            """
                            INSERT INTO ai_agent_settings (setting_key, setting_value, updated_at)
                            VALUES (?, ?, CURRENT_TIMESTAMP)
                            ON CONFLICT(setting_key) DO UPDATE SET
                                setting_value = excluded.setting_value,
                                updated_at = CURRENT_TIMESTAMP
                            """
                        ),
                        (key, str_value),
                    )

                conn.commit()
            return True
        except Exception as e:
            logger.error("Failed to update AI agent settings: %s", e)
            return False

    def get_ai_github_env(self) -> dict[str, str] | None:
        """Return env overrides for the AI GitHub account, or None if not configured.

        This method reads the **unmasked** token directly from the database
        and returns a dict suitable for injecting into subprocess.run() env.

        Returns:
            Dict with GH_TOKEN, GIT_AUTHOR_NAME/EMAIL, GIT_COMMITTER_NAME/EMAIL,
            or None if no token is configured.
        """
        settings = self.get_ai_agent_settings(mask_token=False)

        token = settings.get("ai_github_token", "")
        if not token or not token.strip():
            return None

        name = settings.get("ai_github_author_name", "Open ACE AI") or "Open ACE AI"
        email = settings.get("ai_github_author_email", "bot@open-ace.com") or "bot@open-ace.com"

        return {
            "GH_TOKEN": token.strip(),
            "GIT_AUTHOR_NAME": name,
            "GIT_AUTHOR_EMAIL": email,
            "GIT_COMMITTER_NAME": name,
            "GIT_COMMITTER_EMAIL": email,
        }
