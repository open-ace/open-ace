#!/usr/bin/env python3
"""
Open ACE - OpenClaw CLI Adapter

Adapter for the OpenClaw agent platform. OpenClaw uses an OpenAI-compatible
API surface, so we route requests through the Open ACE proxy using
OPENAI_API_KEY and OPENAI_BASE_URL environment variables.
"""

import logging
import shutil
from typing import Optional

from .base import BaseCLIAdapter

logger = logging.getLogger(__name__)


class OpenClawAdapter(BaseCLIAdapter):
    """Adapter for the OpenClaw CLI tool."""

    EXECUTABLE = "openclaw"
    DISPLAY_NAME = "OpenClaw"

    def get_install_command(self) -> str:
        """
        Return the command to install OpenClaw.

        OpenClaw has its own installer. Provide a placeholder that points
        the operator to the official installation instructions.
        """
        return (
            "npm install -g openclaw@latest || "
            "pip install openclaw || "
            "echo 'See OpenClaw documentation for installation instructions'"
        )

    def check_installed(self) -> bool:
        """Check if OpenClaw CLI is installed."""
        return shutil.which(self.EXECUTABLE) is not None

    def get_env_vars(self, proxy_url: str, proxy_token: str) -> dict[str, str]:
        """
        Get environment variables for OpenClaw.

        OpenClaw follows the OpenAI-compatible client convention and reads
        OPENAI_API_KEY and OPENAI_BASE_URL from the environment. We also
        set OPENCLAW_API_KEY and OPENCLAW_BASE_URL as fallbacks in case
        the tool supports its own env vars.
        """
        base = proxy_url.rstrip("/")
        return {
            "OPENAI_API_KEY": proxy_token,
            "OPENAI_BASE_URL": f"{base}/v1",
            "OPENCLAW_API_KEY": proxy_token,
            "OPENCLAW_BASE_URL": f"{base}/v1",
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
        Build command-line arguments to start OpenClaw.

        OpenClaw is launched in agent mode with the working directory set
        to project_path (handled by the caller). The --json flag requests
        machine-parseable output.
        """
        args = [
            self.EXECUTABLE,
            "--agent",
            "--json",
        ]

        if model:
            args.extend(["--model", model])

        return args

    def get_display_name(self) -> str:
        """Return the display name for this CLI tool."""
        return self.DISPLAY_NAME

    def get_executable_name(self) -> str:
        """Return the executable name."""
        return self.EXECUTABLE
