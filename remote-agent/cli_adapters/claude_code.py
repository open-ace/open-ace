"""
Open ACE - Claude Code CLI Adapter

Adapter for the Claude Code CLI tool (@anthropic-ai/claude-code on npm).
Uses ANTHROPIC_API_KEY and ANTHROPIC_BASE_URL environment variables to
route requests through the Open ACE proxy.
"""

import logging
import shutil
from typing import Optional

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
        model: Optional[str] = None,
        permission_mode: Optional[str] = None,
        allowed_tools: Optional[list[str]] = None,
        resume: bool = False,
    ) -> list[str]:
        """
        Build command-line arguments to start Claude Code.

        Uses --print for non-interactive (piped) mode and --output-format
        stream-json for machine-parseable output. The CLI is launched with
        its working directory set to project_path (handled by the caller).
        """
        args = [
            self.EXECUTABLE,
            "--print",
            "--output-format",
            "stream-json",
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
