"""
Open ACE - Codex CLI Adapter

Adapter for the Codex CLI tool (@openai/codex on npm).
Uses OPENAI_API_KEY / OPENAI_BASE_URL environment variables to route
requests through the Open ACE proxy.

Configuration is stored in ~/.codex/config.toml (TOML format).
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

from .base import BaseCLIAdapter

logger = logging.getLogger(__name__)

# TOML keys that contain sensitive values and must be stripped from
# the config file — they are injected via environment variables instead.
_SENSITIVE_TOML_KEYS = frozenset(
    {
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
    }
)


class CodexCLIAdapter(BaseCLIAdapter):
    """Adapter for the Codex CLI tool."""

    EXECUTABLE = "codex"
    DISPLAY_NAME = "Codex"
    NPM_PACKAGE = "@openai/codex"

    # ------------------------------------------------------------------
    # Installation & discovery
    # ------------------------------------------------------------------

    def get_install_command(self) -> str:
        """Return the command to install Codex CLI."""
        return f"npm install -g {self.NPM_PACKAGE}@latest"

    def check_installed(self) -> bool:
        """Check if Codex CLI is installed."""
        return shutil.which(self.EXECUTABLE) is not None

    # ------------------------------------------------------------------
    # Environment variables
    # ------------------------------------------------------------------

    def get_env_vars(self, proxy_url: str, proxy_token: str) -> dict[str, str]:
        """
        Get environment variables for Codex CLI.

        Codex reads OPENAI_API_KEY and OPENAI_BASE_URL from the environment,
        following the standard OpenAI-compatible client convention. We point
        the base URL at the Open ACE LLM proxy endpoint and supply the proxy
        token as the API key so that every request is authenticated.
        """
        base = proxy_url.rstrip("/")
        return {
            "OPENAI_API_KEY": proxy_token,
            "OPENAI_BASE_URL": f"{base}/v1",
        }

    # ------------------------------------------------------------------
    # Command-line argument builders
    # ------------------------------------------------------------------

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
        Build command-line arguments to start Codex.

        For interactive mode (used by web terminal), the basic invocation
        is just ``codex`` with optional flags for model, sandbox, and
        approval mode.

        For remote session mode (app-server), this won't be called the same
        way, but we still provide basic args here.

        Args:
            session_id: Session identifier (used for resume).
            project_path: Working directory for the session.
            model: Model name to use.
            permission_mode: Approval mode - "default", "plan", or "auto".
            allowed_tools: Not directly used by Codex CLI currently.
            resume: Whether to resume an existing session.
        """
        args = [self.EXECUTABLE]

        if model:
            args.extend(["--model", model])

        # Map permission_mode to Codex approval flags
        if permission_mode:
            if permission_mode == "plan":
                args.extend(["--ask-for-approval", "untrusted"])
            elif permission_mode == "auto":
                args.append("--dangerously-bypass-approvals-and-sandbox")
            # "default" -> no extra flag

        # Codex supports `codex resume <SESSION_ID>` for restoring sessions
        if resume and session_id:
            args = [self.EXECUTABLE, "resume", session_id]
            if model:
                args.extend(["--model", model])
            if project_path:
                args.extend(["--cd", project_path])
            logger.info("Resuming Codex session_id=%s", session_id[:8])

        return args

    def build_single_shot_args(
        self, prompt: str, project_path: str, model: str | None = None
    ) -> list[str]:
        """
        Build args for a single-shot prompt execution.

        Uses ``codex exec --json`` for machine-parseable output with a
        read-only sandbox as the default safety boundary.
        """
        args = [
            self.EXECUTABLE,
            "exec",
            "--json",
            "--sandbox",
            "read-only",
        ]

        if model:
            args.extend(["--model", model])

        args.append(prompt)
        return args

    def supports_stdin_input(self) -> bool:
        """Codex CLI supports stdin input via JSONRPC 2.0 (app-server mode)."""
        return True

    # ------------------------------------------------------------------
    # Display helpers
    # ------------------------------------------------------------------

    def get_display_name(self) -> str:
        """Return the display name for this CLI tool."""
        return self.DISPLAY_NAME

    def get_executable_name(self) -> str:
        """Return the executable name."""
        return self.EXECUTABLE

    # ------------------------------------------------------------------
    # Settings / configuration
    # ------------------------------------------------------------------

    def build_settings(
        self,
        base_settings: dict,
    ) -> dict:
        """
        Build config settings for Codex (non-sensitive config only).

        The Codex config file is ~/.codex/config.toml in TOML format.
        This method returns a dict representation that should be serialized
        to TOML before writing to disk.

        API credentials and baseUrl are NOT included -- they are injected
        via environment variables by the agent.

        Args:
            base_settings: User-configured settings dict.

        Returns:
            Settings dict with sensitive keys stripped.
        """
        settings = base_settings.copy()

        # Strip any API credential fields that the user may have
        # accidentally included.
        env = settings.get("env", {})
        if env:
            env = {k: v for k, v in env.items() if k not in _SENSITIVE_TOML_KEYS}
            settings["env"] = env

        # Enable reasoning summary output by default
        if "model_reasoning_summary" not in settings:
            settings["model_reasoning_summary"] = "auto"

        return settings

    def get_settings_path(self) -> str:
        """Return the path to Codex config.toml."""
        return os.path.expanduser("~/.codex/config.toml")

    # ------------------------------------------------------------------
    # Static configuration helper
    # ------------------------------------------------------------------

    @staticmethod
    def configure_settings(proxy_url: str, proxy_token: str) -> str | None:
        """
        Write a minimal ~/.codex/config.toml so that the CLI picks up
        the proxy URL as the model provider base URL.

        The config uses TOML format. We set:
        - model_reasoning_summary = "auto" to enable reasoning output
        - A model_providers entry with the proxy base_url

        Args:
            proxy_url: The LLM proxy base URL.
            proxy_token: The proxy authentication token (stored in env, not file).

        Returns:
            The path to the written config file, or None on failure.
        """
        try:
            codex_dir = Path.home() / ".codex"
            codex_dir.mkdir(parents=True, exist_ok=True)

            config_path = codex_dir / "config.toml"

            base = proxy_url.rstrip("/").replace('"', '\\"').replace("\\", "\\\\")

            # Build the config content using simple string formatting.
            config_lines = []

            # Top-level settings
            config_lines.append('model_reasoning_summary = "auto"')
            config_lines.append("")

            # Model provider section for proxy
            config_lines.append("[model_providers.openai_proxy]")
            config_lines.append(f'base_url = "{base}/v1"')
            config_lines.append('env_key = "OPENAI_API_KEY"')
            config_lines.append("")

            config_content = "\n".join(config_lines)

            config_path.write_text(config_content, encoding="utf-8")
            logger.info("Wrote Codex config to %s", config_path)
            return str(config_path)
        except Exception as exc:
            logger.warning("Failed to write Codex config: %s", exc)
            return None
