#!/usr/bin/env python3
"""
Open ACE - Qwen Code CLI Adapter

Adapter for the qwen-code CLI tool (@qwen-code/qwen-code on npm).
Uses OPENAI_API_KEY / OPENAI_BASE_URL environment variables to route
requests through the Open ACE proxy.
"""

import json
import logging
import shutil
from pathlib import Path
from typing import Optional

from .base import BaseCLIAdapter

logger = logging.getLogger(__name__)


class QwenCodeAdapter(BaseCLIAdapter):
    """Adapter for the qwen-code CLI tool."""

    # The npm package installs the `qwen` executable
    EXECUTABLE = "qwen"
    DISPLAY_NAME = "Qwen Code"
    NPM_PACKAGE = "@qwen-code/qwen-code"

    def get_install_command(self) -> str:
        """Return the command to install qwen-code CLI."""
        return f"npm install -g {self.NPM_PACKAGE}@latest"

    def check_installed(self) -> bool:
        """Check if qwen-code CLI is installed."""
        return shutil.which(self.EXECUTABLE) is not None

    def get_env_vars(self, proxy_url: str, proxy_token: str) -> dict[str, str]:
        """
        Get environment variables for qwen-code CLI.

        Qwen-code reads OPENAI_API_KEY and OPENAI_BASE_URL from the environment,
        following the standard OpenAI-compatible client convention. We point
        the base URL at the Open ACE LLM proxy endpoint and supply the proxy
        token as the API key so that every request is authenticated.
        """
        # Ensure proxy_url has no trailing slash, then append /v1 for
        # OpenAI-compatible routing used by the proxy.
        base = proxy_url.rstrip("/")
        return {
            "OPENAI_API_KEY": proxy_token,
            "OPENAI_BASE_URL": f"{base}/v1",
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
        args = [
            self.EXECUTABLE,
            "--auth-type",
            "openai",
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
            "--channel=SDK",
        ]

        if resume:
            args.extend(["--resume", session_id])
        if permission_mode:
            args.extend(["--approval-mode", permission_mode])
        if model:
            args.extend(["--model", model])
        if allowed_tools:
            for tool in allowed_tools:
                args.extend(["--allowed-tools", tool])

        return args

    def supports_stdin_input(self) -> bool:
        """Qwen CLI supports stdin input via stream-json format."""
        return True

    def build_single_shot_args(
        self, prompt: str, project_path: str, model: Optional[str] = None
    ) -> list[str]:
        args = [
            self.EXECUTABLE,
            "--auth-type",
            "openai",
            "--output-format",
            "stream-json",
            prompt,
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

    # ------------------------------------------------------------------
    # Optional helpers
    # ------------------------------------------------------------------

    @staticmethod
    def configure_settings(proxy_url: str, proxy_token: str) -> Optional[str]:
        """
        Write a minimal ~/.qwen/settings.json so that the CLI picks up
        the proxy even when environment variables are not the primary
        configuration path.

        Returns the path to the written settings file, or None on failure.
        """
        try:
            qwen_dir = Path.home() / ".qwen"
            qwen_dir.mkdir(parents=True, exist_ok=True)

            settings_path = qwen_dir / "settings.json"

            # Preserve existing settings if present
            settings: dict = {}
            if settings_path.exists():
                try:
                    settings = json.loads(settings_path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    settings = {}

            base = proxy_url.rstrip("/")
            settings.setdefault("modelProvider", {})
            settings["modelProvider"]["baseUrl"] = f"{base}/v1"

            settings_path.write_text(
                json.dumps(settings, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            logger.info(f"Wrote qwen-code settings to {settings_path}")
            return str(settings_path)
        except Exception as exc:
            logger.warning(f"Failed to write qwen-code settings: {exc}")
            return None
