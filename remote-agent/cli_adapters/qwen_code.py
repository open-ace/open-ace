"""
Open ACE - Qwen Code CLI Adapter

Adapter for the qwen-code CLI tool (@qwen-code/qwen-code on npm).
Uses OPENAI_API_KEY / OPENAI_BASE_URL environment variables to route
requests through the Open ACE proxy.
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

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
        model: str | None = None,
        permission_mode: str | None = None,
        allowed_tools: list[str] | None = None,
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
            # Log resume session ID for debugging (Issue #316)
            logger.info(
                "Building resume command for session_id=%s (project=%s)",
                session_id[:8] if session_id else "N/A",
                project_path,
            )
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
        self, prompt: str, project_path: str, model: str | None = None
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

    def build_settings(
        self,
        base_settings: dict,
        api_key: str,
        base_url: str,
        provider_name: str = "openai",
    ) -> dict:
        """
        Build complete settings.json for Qwen Code (bailian format).

        Reference: ~/.qwen/settings.json.bailian format with:
        - env: API key environment variables
        - modelProviders: Provider-specific model configurations
        - security: Auth type selection
        - model: Default model selection

        Args:
            base_settings: User-configured settings (modelProviders, model, etc.)
            api_key: Real API key from api_key_store (or proxy token)
            base_url: Base URL for API requests
            provider_name: Provider type (default: openai)

        Returns:
            Complete settings dict ready to write to ~/.qwen/settings.json
        """
        settings = base_settings.copy()
        settings.setdefault("env", {})
        settings.setdefault("modelProviders", {})
        settings.setdefault("security", {"auth": {"selectedType": provider_name}})
        settings["$version"] = 3

        # Ensure target provider exists
        settings["modelProviders"].setdefault(provider_name, [])

        # Determine env key name (default based on provider)
        env_key_name = f"{provider_name.upper()}_API_KEY"

        # Process modelProviders - inject baseUrl where needed
        for model_config in settings["modelProviders"][provider_name]:
            # Use the model's envKey if specified
            if "envKey" in model_config:
                env_key_name = model_config["envKey"]

            # If baseUrl not set in model config, use api_key_store's base_url
            if "baseUrl" not in model_config:
                model_config["baseUrl"] = base_url.rstrip("/")

        # Inject the API key into env
        settings["env"][env_key_name] = api_key

        return settings

    def get_settings_path(self) -> str:
        """Return the path to Qwen Code settings.json."""
        import os

        return os.path.expanduser("~/.qwen/settings.json")

    # ------------------------------------------------------------------
    # Optional helpers
    # ------------------------------------------------------------------

    @staticmethod
    def configure_settings(proxy_url: str, proxy_token: str) -> str | None:
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
