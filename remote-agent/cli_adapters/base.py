"""
Open ACE - Base CLI Adapter

Abstract base class for CLI tool adapters used by the remote agent.
Each adapter knows how to install, configure, and launch a specific
CLI coding tool (e.g., qwen-code, claude-code, openclaw).
"""

from __future__ import annotations

import abc
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def collect_custom_envkeys(
    settings_path: str | Path,
    token_value: str,
) -> dict[str, str]:
    """
    Read a qwen-code settings file and return env vars for any custom
    envKeys found in modelProviders.

    The CLI may reference non-standard env var names (e.g.
    BAILIAN_CODING_PLAN_API_KEY) in modelProviders entries.  This
    function collects those keys and maps them all to *token_value* so
    the proxy token is available regardless of which envKey the CLI
    reads at runtime.

    Args:
        settings_path: Path to the settings.json file.
        token_value: Value to assign (typically the proxy token).

    Returns:
        Dict of env var name -> token_value for every custom envKey
        found.  Standard ``OPENAI_API_KEY`` is excluded (already set by
        the adapter).
    """
    env_overrides: dict[str, str] = {}
    try:
        p = Path(settings_path)
        if not p.is_file():
            return env_overrides
        with open(p, encoding="utf-8") as f:
            settings_data = json.load(f)
        providers = settings_data.get("modelProviders", {})
        if isinstance(providers, dict):
            for _provider_name, models_list in providers.items():
                if not isinstance(models_list, list):
                    continue
                for entry in models_list:
                    if isinstance(entry, dict):
                        custom_key = entry.get("envKey")
                        if custom_key and custom_key != "OPENAI_API_KEY":
                            env_overrides[custom_key] = token_value
    except Exception:
        logger.debug(
            "Failed to read custom envKeys from %s",
            settings_path,
            exc_info=True,
        )
    return env_overrides


def normalize_model_providers(settings: dict) -> None:
    """
    Normalize modelProviders in-place: unify all envKeys to
    OPENAI_API_KEY and remove baseUrl entries.

    This ensures the CLI reads credentials from the standard
    OPENAI_API_KEY / OPENAI_BASE_URL env vars injected by the agent,
    rather than user-configured custom keys or external baseUrls that
    would bypass the LLM proxy.

    Only ``envKey`` and ``baseUrl`` fields are touched; all other
    model configuration (id, name, generationConfig, etc.) and
    top-level settings keys (mcpServers, statusLine, etc.) are
    preserved.
    """
    providers = settings.get("modelProviders", {})
    if not isinstance(providers, dict):
        return
    for _provider_name, models in providers.items():
        if not isinstance(models, list):
            continue
        for model in models:
            if not isinstance(model, dict):
                continue
            if "envKey" in model:
                model["envKey"] = "OPENAI_API_KEY"
            # Intentionally modify dict values (not keys) during iteration.
            model.pop("baseUrl", None)


class BaseCLIAdapter(abc.ABC):
    """Abstract base class for CLI tool adapters."""

    @abc.abstractmethod
    def get_install_command(self) -> str:
        """Return the command to install this CLI tool."""
        pass

    @abc.abstractmethod
    def check_installed(self) -> bool:
        """Check if this CLI tool is installed."""
        pass

    @abc.abstractmethod
    def get_env_vars(self, proxy_url: str, proxy_token: str) -> dict[str, str]:
        """Get environment variables to set when spawning the CLI."""
        pass

    @abc.abstractmethod
    def build_start_args(
        self,
        session_id: str,
        project_path: str,
        model: str | None = None,
        permission_mode: str | None = None,
        allowed_tools: list[str] | None = None,
        resume: bool = False,
    ) -> list[str]:
        """Build the command-line arguments to start the CLI."""
        pass

    @abc.abstractmethod
    def get_display_name(self) -> str:
        """Return the display name for this CLI tool."""
        pass

    @abc.abstractmethod
    def get_executable_name(self) -> str:
        """Return the executable name (e.g., 'qwen', 'claude')."""
        pass

    def supports_stdin_input(self) -> bool:
        """Whether this CLI tool supports sending messages via stdin pipe."""
        return True

    def build_single_shot_args(
        self, prompt: str, project_path: str, model: str | None = None
    ) -> list[str]:
        """Build args for a single-shot prompt execution (used when stdin is not supported)."""
        return [self.get_executable_name(), prompt]
