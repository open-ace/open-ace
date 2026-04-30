#!/usr/bin/env python3
"""
Open ACE - Base CLI Adapter

Abstract base class for CLI tool adapters used by the remote agent.
Each adapter knows how to install, configure, and launch a specific
CLI coding tool (e.g., qwen-code, claude-code, openclaw).
"""

import abc
from typing import Dict, List, Optional


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
    def get_env_vars(self, proxy_url: str, proxy_token: str) -> Dict[str, str]:
        """Get environment variables to set when spawning the CLI."""
        pass

    @abc.abstractmethod
    def build_start_args(self, session_id: str, project_path: str, model: Optional[str] = None,
                         permission_mode: Optional[str] = None, allowed_tools: Optional[List[str]] = None,
                         resume: bool = False) -> List[str]:
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
        self, prompt: str, project_path: str, model: Optional[str] = None
    ) -> List[str]:
        """Build args for a single-shot prompt execution (used when stdin is not supported)."""
        return [self.get_executable_name(), prompt]
