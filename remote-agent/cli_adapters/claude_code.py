"""
Open ACE - Claude Code CLI Adapter

Adapter for the Claude Code CLI tool (@anthropic-ai/claude-code on npm).
Uses ANTHROPIC_API_KEY and ANTHROPIC_BASE_URL environment variables to
route requests through the Open ACE proxy.
"""

from __future__ import annotations

import logging
import shutil

from .base import BaseCLIAdapter

logger = logging.getLogger(__name__)


class ClaudeCodeAdapter(BaseCLIAdapter):
    """Adapter for the Claude Code CLI tool."""

    EXECUTABLE = "claude"
    DISPLAY_NAME = "Claude Code"
    NPM_PACKAGE = "@anthropic-ai/claude-code"

    def get_install_command(self) -> str:
        """Return the command to install Claude Code CLI."""
        return f"npm install -g {self.NPM_PACKAGE}@latest"

    def check_installed(self) -> bool:
        """Check if Claude Code CLI is installed."""
        return shutil.which(self.EXECUTABLE) is not None

    def get_env_vars(self, proxy_url: str, proxy_token: str) -> dict[str, str]:
        """
        Get environment variables for Claude Code CLI.

        Claude Code reads ANTHROPIC_API_KEY and ANTHROPIC_BASE_URL from the
        environment. We set both so that every request is routed through the
        Open ACE LLM proxy and authenticated with the proxy token.
        """
        base = proxy_url.rstrip("/")
        return {
            "ANTHROPIC_API_KEY": proxy_token,
            "ANTHROPIC_BASE_URL": base,
        }

    def build_start_args(
        self,
        session_id: str,
        project_path: str,
        model: str | None = None,
        permission_mode: str | None = None,
        allowed_tools: list[str] | None = None,
        resume: bool = False,
    ) -> list[str]:
        """
        Build command-line arguments to start Claude Code.

        Uses --print for non-interactive (piped) mode with --input-format
        stream-json for SDK-compatible stdin communication and --output-format
        stream-json for machine-parseable output.
        """
        args = [
            self.EXECUTABLE,
            "--print",
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
            "--verbose",
        ]

        if resume:
            args.extend(["--resume", session_id])
        if model:
            args.extend(["--model", model])

        return args

    def get_display_name(self) -> str:
        """Return the display name for this CLI tool."""
        return self.DISPLAY_NAME

    def get_executable_name(self) -> str:
        """Return the executable name."""
        return self.EXECUTABLE

    def build_settings(
        self,
        base_settings: dict,
        api_key: str,
        base_url: str,
    ) -> dict:
        """
        Build complete settings.json for Claude Code.

        Merges user-configured settings with system-injected credentials.
        User settings should not contain sensitive fields like ANTHROPIC_API_KEY.

        Args:
            base_settings: User-configured settings (model mappings, etc.)
            api_key: Real API key from api_key_store (or proxy token)
            base_url: Base URL for API requests

        Returns:
            Complete settings dict ready to write to ~/.claude/settings.json
        """
        settings = base_settings.copy()
        settings.setdefault("env", {})

        # Inject API credentials (from api_key_store, not user config)
        settings["env"]["ANTHROPIC_API_KEY"] = api_key
        settings["env"]["ANTHROPIC_BASE_URL"] = base_url.rstrip("/")

        # Preserve user-configured model mappings if present:
        # env.ANTHROPIC_MODEL, env.ANTHROPIC_DEFAULT_HAIKU_MODEL, etc.

        return settings

    def get_settings_path(self) -> str:
        """Return the path to Claude Code settings.json."""
        import os

        return os.path.expanduser("~/.claude/settings.json")
