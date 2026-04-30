#!/usr/bin/env python3
"""
Open ACE - CLI Adapters

Registry of CLI tool adapters used by the remote agent to discover,
install, configure, and launch coding assistants.

Usage::

    from cli_adapters import get_adapter, list_adapters

    adapter = get_adapter("qwen-code-cli")
    if not adapter.check_installed():
        print(adapter.get_install_command())
    env = adapter.get_env_vars(proxy_url, proxy_token)
    args = adapter.build_start_args(session_id, project_path, model)
"""

import logging

from .base import BaseCLIAdapter
from .claude_code import ClaudeCodeAdapter
from .openclaw import OpenClawAdapter
from .qwen_code import QwenCodeAdapter

logger = logging.getLogger(__name__)

# Registry mapping CLI tool identifier -> adapter class
ADAPTERS = {
    "qwen-code-cli": QwenCodeAdapter,
    "claude-code": ClaudeCodeAdapter,
    "openclaw": OpenClawAdapter,
}


def get_adapter(cli_tool: str) -> BaseCLIAdapter:
    """
    Get adapter instance for a CLI tool.

    If the tool is not recognised, a generic BaseCLIAdapter is returned
    with sensible defaults so that the executor can still attempt to
    launch an unknown tool.

    Args:
        cli_tool: Identifier string (e.g. 'qwen-code-cli', 'claude-code', 'openclaw').

    Returns:
        A BaseCLIAdapter subclass instance.
    """
    adapter_class = ADAPTERS.get(cli_tool)
    if adapter_class:
        return adapter_class()

    # Unknown tool -- return a generic adapter that will try to find
    # an executable matching the tool name.
    logger.debug("No dedicated adapter for '%s'; using generic adapter", cli_tool)

    class GenericAdapter(BaseCLIAdapter):
        def get_install_command(self):
            return f"# Install {cli_tool} per its documentation"

        def check_installed(self):
            import shutil

            return shutil.which(cli_tool) is not None

        def get_env_vars(self, proxy_url: str, proxy_token: str):
            base = proxy_url.rstrip("/")
            return {
                "OPENAI_API_KEY": proxy_token,
                "OPENAI_BASE_URL": f"{base}/v1",
                "OPENACE_PROXY_URL": proxy_url,
                "OPENACE_PROXY_TOKEN": proxy_token,
            }

        def build_start_args(self, session_id, project_path, model=None):
            args = [cli_tool]
            if model:
                args.extend(["--model", model])
            return args

        def get_display_name(self):
            return cli_tool

        def get_executable_name(self):
            return cli_tool

    return GenericAdapter()


def list_adapters():
    """List registered CLI adapter identifiers."""
    return list(ADAPTERS.keys())
